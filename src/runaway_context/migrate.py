"""Schema migrator v2 → v3 (E1).

HR-4: non-destructive. Every step is ADD COLUMN / CREATE TABLE / CREATE VIEW
/ CREATE INDEX. The migrator runs PRAGMA table_info() before and after each
step. Any column lost aborts and restores from backup.

Refuses:
    Migration that would lose a column or row count.

Returns:
    A MigrationReport summarizing applied steps and row counts.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from runaway_context.errors import MigrationAborted


def _schema_dir() -> Path:
    return Path(__file__).parent.parent.parent / "schema"


SCHEMA_FILES = [
    "000_knowledge_db.sql",
    "001_v3_additions.sql",
    "003_semantic_sidecar.sql",
]
SESSIONS_SCHEMA_FILES = ["002_sessions_db.sql"]
METRICS_SCHEMA_FILES = ["004_metrics_db.sql"]


def detect_v1_layout(knowledge_db: Path) -> bool:
    """Return True iff *knowledge_db* is a v1 single-file install.

    v1 fingerprint: ``knowledge_chunks`` + ``lessons_learned`` + ``sessions``
    (transcripts) all co-located in one DB file. v2 split this into two files;
    a file matching the v1 fingerprint pre-dates the split.

    Returns:
        True only when all three v1 tables exist in the file.

    Refuses:
        Nothing — read-only probe.
    """
    p = Path(knowledge_db)
    if not p.exists():
        return False
    conn = sqlite3.connect(str(p))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    return {"knowledge_chunks", "lessons_learned", "sessions"}.issubset(tables)


def _split_v1_sessions(v1_path: Path, sessions_db: Path) -> int:
    """Copy v1's ``sessions`` table rows into ``sessions_db.session_logs``.

    HR-4: non-destructive. v1's ``sessions`` table is **left in place** in the
    original file; we copy the transcript rows into a new ``sessions.db`` so the
    v3 code path (which expects ATTACH-style ``session_logs``) works without
    deleting anything.

    Returns:
        Number of rows copied.

    Refuses:
        Nothing — if the v1 file has no ``sessions`` table, returns 0.
    """
    if not detect_v1_layout(v1_path):
        return 0
    sessions_db.parent.mkdir(parents=True, exist_ok=True)

    # Create sessions.db with the v3 session_logs schema first.
    sconn = sqlite3.connect(str(sessions_db))
    try:
        for fname in SESSIONS_SCHEMA_FILES:
            _apply_sql_file(sconn, _schema_dir() / fname)
        sconn.commit()

        # Pull rows from v1's `sessions` and translate to v3 `session_logs`.
        v1conn = sqlite3.connect(str(v1_path))
        try:
            v1_cols = [
                r[1]
                for r in v1conn.execute("PRAGMA table_info(sessions)")
            ]
            target_cols = [
                "conversation_id", "tool", "machine", "project_hint",
                "started_at", "ended_at", "full_transcript",
                "token_in", "token_out", "notes",
            ]
            select_parts = []
            for c in target_cols:
                if c in v1_cols:
                    select_parts.append(c)
                else:
                    # Old v1 column shape varies; fall back to NULL.
                    select_parts.append("NULL AS " + c)
            rows = v1conn.execute(
                "SELECT " + ", ".join(select_parts) + " FROM sessions"
            ).fetchall()
        finally:
            v1conn.close()

        copied = 0
        for row in rows:
            # If conversation_id is missing (v1 used integer ids), synthesize
            # a stable string id from the file path + rowid index.
            if row[0] is None:
                conv_id = f"v1-{copied + 1:08d}"
            else:
                conv_id = str(row[0])
            sconn.execute(
                "INSERT OR IGNORE INTO session_logs "
                "(conversation_id, tool, machine, project_hint, started_at, "
                "ended_at, full_transcript, token_in, token_out, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_id,) + tuple(row[1:]),
            )
            copied += 1
        sconn.commit()
    finally:
        sconn.close()
    return copied


@dataclass
class MigrationReport:
    """Result of a migration run."""

    knowledge_db: Path
    sessions_db: Optional[Path] = None
    metrics_db: Optional[Path] = None
    steps_applied: List[str] = field(default_factory=list)
    columns_added: Dict[str, List[str]] = field(default_factory=dict)
    row_counts_before: Dict[str, int] = field(default_factory=dict)
    row_counts_after: Dict[str, int] = field(default_factory=dict)
    backup_path: Optional[Path] = None
    aborted_reason: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """Return True iff the migration ran to completion without abort.

        Returns:
            ``True`` when ``aborted_reason`` is ``None``; ``False`` otherwise.

        Refuses:
            Nothing — pure-property check, no I/O.
        """
        return self.aborted_reason is None


def _table_info(conn: sqlite3.Connection, table: str) -> Dict[str, str]:
    info = {}
    try:
        for row in conn.execute(f"PRAGMA table_info({table})"):
            info[row[1]] = row[2]
    except sqlite3.OperationalError:
        return {}
    return info


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _apply_sql_file(conn: sqlite3.Connection, sql_path: Path) -> None:
    """Apply a .sql file to the connection.

    Strategy: pre-flight every ALTER TABLE ADD COLUMN by checking PRAGMA
    table_info; skip if the column already exists. Then executescript() the
    remainder (CREATE TABLE / VIEW / TRIGGER / INDEX / INSERT OR IGNORE).
    """
    sql_text = sql_path.read_text()

    # Pull ALTER TABLE ADD COLUMN out separately
    alter_pattern = re.compile(
        r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)[^;]*;",
        re.IGNORECASE | re.MULTILINE,
    )

    altered_text = sql_text
    pending_alters: List[str] = []
    for m in alter_pattern.finditer(sql_text):
        pending_alters.append(m.group(0))
    altered_text = alter_pattern.sub("", altered_text)

    # Apply ADD COLUMNs one at a time, skipping duplicates
    for alter_stmt in pending_alters:
        m = re.match(
            r"\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
            alter_stmt, re.IGNORECASE,
        )
        if not m:
            continue
        table, col = m.group(1), m.group(2)
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col in existing:
            continue
        try:
            conn.execute(alter_stmt)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column" in msg:
                continue
            raise

    # Apply the rest as a single script (CREATE statements are IF NOT EXISTS)
    if altered_text.strip():
        try:
            conn.executescript(altered_text)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "already exists" in msg:
                return
            raise


def migrate(
    knowledge_db: Path,
    sessions_db: Optional[Path] = None,
    metrics_db: Optional[Path] = None,
    backup: bool = True,
) -> MigrationReport:
    """Apply the v3 schema to a fresh DB or upgrade a v1/v2 DB in place.

    The migration is additive (HR-4). Existing rows are preserved.

    For v1 installs (single-file ``sessions.db`` with knowledge tables and
    transcripts co-located) the migrator first copies the transcript rows
    into a new ``sessions.db`` (creating it if needed), leaving the v1 file
    intact. The v3 additive layer then applies to the original file.

    Returns:
        :class:`MigrationReport` describing applied steps, row counts, and
        any v1-split that ran.

    Raises:
        MigrationAborted: if a column was lost or row counts dropped.
    """
    report = MigrationReport(knowledge_db=knowledge_db,
                             sessions_db=sessions_db,
                             metrics_db=metrics_db)

    knowledge_db.parent.mkdir(parents=True, exist_ok=True)
    if backup and knowledge_db.exists():
        backup_path = knowledge_db.with_suffix(knowledge_db.suffix + ".pre-v3.bak")
        shutil.copy2(knowledge_db, backup_path)
        report.backup_path = backup_path

    # v1 → v3 auto-split (HR-4 non-destructive: original file untouched).
    if knowledge_db.exists() and detect_v1_layout(knowledge_db):
        target_sessions = sessions_db or (knowledge_db.parent / "sessions.db")
        copied = _split_v1_sessions(knowledge_db, target_sessions)
        report.steps_applied.append(f"v1_split:copied_{copied}_session_rows")
        # Remember the implicit sessions_db so the rest of the function uses it.
        if sessions_db is None:
            sessions_db = target_sessions
            report.sessions_db = target_sessions

    conn = sqlite3.connect(knowledge_db)
    try:
        # capture row counts before
        for t in ("knowledge_chunks", "lessons_learned"):
            report.row_counts_before[t] = _row_count(conn, t)
        cols_before = {
            t: _table_info(conn, t) for t in ("knowledge_chunks", "lessons_learned")
        }

        # apply each schema file
        for fname in SCHEMA_FILES:
            sql_path = _schema_dir() / fname
            if not sql_path.exists():
                continue
            _apply_sql_file(conn, sql_path)
            report.steps_applied.append(fname)
            conn.commit()

        # post-step verification: no column lost (HR-4)
        for t, before in cols_before.items():
            after = _table_info(conn, t)
            lost = sorted(set(before) - set(after))
            if lost:
                report.aborted_reason = f"columns lost from {t}: {lost}"
                conn.close()
                if report.backup_path:
                    shutil.copy2(report.backup_path, knowledge_db)
                raise MigrationAborted(report.aborted_reason)
            new_cols = sorted(set(after) - set(before))
            if new_cols:
                report.columns_added[t] = new_cols

        # row counts after
        for t in ("knowledge_chunks", "lessons_learned"):
            report.row_counts_after[t] = _row_count(conn, t)

        # row-count regression check
        for t in report.row_counts_before:
            if report.row_counts_after.get(t, 0) < report.row_counts_before[t]:
                report.aborted_reason = (
                    f"row count regressed in {t}: "
                    f"{report.row_counts_before[t]} -> {report.row_counts_after.get(t, 0)}"
                )
                conn.close()
                if report.backup_path:
                    shutil.copy2(report.backup_path, knowledge_db)
                raise MigrationAborted(report.aborted_reason)
    finally:
        conn.close()

    # sessions.db (separate file)
    if sessions_db is not None:
        sessions_db.parent.mkdir(parents=True, exist_ok=True)
        sconn = sqlite3.connect(sessions_db)
        try:
            for fname in SESSIONS_SCHEMA_FILES:
                _apply_sql_file(sconn, _schema_dir() / fname)
                report.steps_applied.append(f"sessions:{fname}")
            sconn.commit()
        finally:
            sconn.close()

    # metrics.db (separate file)
    if metrics_db is not None:
        metrics_db.parent.mkdir(parents=True, exist_ok=True)
        mconn = sqlite3.connect(metrics_db)
        try:
            for fname in METRICS_SCHEMA_FILES:
                _apply_sql_file(mconn, _schema_dir() / fname)
                report.steps_applied.append(f"metrics:{fname}")
            mconn.commit()
        finally:
            mconn.close()

    return report


def schema_version(knowledge_db: Path) -> Optional[tuple]:
    """Return the current (major, minor, patch) version, or None if missing.

    Returns:
        Tuple of three integers, or None when the DB has not been migrated.
    """
    if not knowledge_db.exists():
        return None
    conn = sqlite3.connect(knowledge_db)
    try:
        row = conn.execute(
            "SELECT major, minor, patch FROM schema_version WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        return (row[0], row[1], row[2])
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
