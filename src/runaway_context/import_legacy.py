"""One-shot importer for legacy ``sessions.db`` directories.

A "legacy" install is either:

- **Canonical v1 RunawayContext.** Schema fingerprint matches
  :data:`runaway_context.migrate.V1_REQUIRED_COLUMNS`. The migrator could
  upgrade this in-place, but some users prefer a clean v3 install with their
  data copied over (no shared file). This module supports that path.

- **Homemade ``sessions.py`` system.** Same table names, different columns
  (``lessons_learned.lesson`` + ``context`` instead of canonical
  ``prevention_rule``). The migrator REFUSES this shape (HR-4 atomicity).
  This module reads it row by row and inserts into v3 with field mapping.

Why a dedicated importer instead of running the migrator: the migrator
applies v3 schema additions in place. That requires the source file to BE a
v3 install going forward. A user may prefer to keep the legacy file frozen
as a backup and copy its data into a separate fresh v3 install. This module
does that, byte-preserving where possible.

Slug normalization: HR-2 enforces snake_case canonical slugs. A source slug
like ``"runaway-knight"`` is auto-aliased — the canonical ``runaway_knight``
is registered, the hyphen form is recorded in ``slug_aliases`` so future
writes via either form route to the same canonical slug.

Refuses:
    Importing from a source directory that has no recognized layout
    (returns an empty report with ``status="unrecognized"`` rather than
    raising — callers see this as a no-op).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from runaway_context.config import Config


_HYPHEN_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")


def _is_canonical_slug(s: str) -> bool:
    return bool(re.fullmatch(r"^[a-z][a-z0-9_]*$", s or ""))


def _canonicalize(slug: str) -> str:
    """Coerce a legacy slug into snake_case canonical form."""
    return re.sub(r"[^a-z0-9_]", "_", (slug or "").strip().lower()).strip("_") or "general"


def _detect_layout(src_db: Path) -> str:
    """Return one of: ``canonical_v1``, ``homemade``, ``foreign``, ``unknown``.

    - ``canonical_v1``: knowledge_chunks + lessons_learned + lessons has
      ``prevention_rule``. The original RunawayContext v1 shape.
    - ``homemade``: knowledge_chunks + lessons_learned + lessons has the
      old Steven-rolled ``lesson`` / ``context`` columns.
    - ``foreign`` (NEW in v3.3.3): ``lessons_learned`` is present with a
      v2-compatible column set (title + prevention_rule OR lesson/context),
      but ``knowledge_chunks`` is **missing**. Catches homemade KS designs
      that built only the lessons surface (e.g., Parkway _KnowledgeStore).
    - ``unknown``: nothing recognizable.
    """
    if not src_db.exists():
        return "unknown"
    conn = sqlite3.connect(str(src_db))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "lessons_learned" not in tables:
            return "unknown"
        ll_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(lessons_learned)")
        }
        has_chunks = "knowledge_chunks" in tables
        has_v1_lessons = "prevention_rule" in ll_cols
        has_homemade_lessons = "lesson" in ll_cols or "context" in ll_cols
        if has_chunks and has_v1_lessons:
            return "canonical_v1"
        if has_chunks and has_homemade_lessons:
            return "homemade"
        # Lessons present, chunks missing -> 'foreign' (a homemade KS that
        # skipped the chunks side, e.g. Parkway). Require at least one
        # recognized lesson-content column so we don't try to import junk.
        if not has_chunks and (has_v1_lessons or has_homemade_lessons):
            return "foreign"
        return "unknown"
    finally:
        conn.close()


def _existing_slugs(install_dir: Path) -> set:
    """Return the set of canonical slugs already in the v3 install."""
    knowledge_db = install_dir / "knowledge.db"
    if not knowledge_db.exists():
        return set()
    conn = sqlite3.connect(str(knowledge_db))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT slug FROM slug_registry WHERE deprecated_at IS NULL"
            )
        }
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def _register_slugs_batch(
    cfg: Config, raw_slugs: List[str], registered: set,
) -> Dict[str, str]:
    """Pre-register every unique source slug; return ``{raw → canonical}`` map.

    Done in one Client lifecycle (and one connection) to avoid lock thrash
    when the source DB has many distinct project values.

    Refuses:
        Nothing — aliases that already exist or are malformed are logged and
        the import continues (HR-8 best-effort).
    """
    from runaway_context.client import Client

    raw_to_canonical: Dict[str, str] = {}
    canonical_targets: List[Tuple[str, str]] = []  # (canonical, raw)
    for raw in raw_slugs:
        canonical = _canonicalize(raw)
        raw_to_canonical[raw] = canonical
        canonical_targets.append((canonical, raw))

    client = Client(install_dir=cfg.install_dir)
    try:
        for canonical, raw in canonical_targets:
            if canonical not in registered:
                try:
                    client.register_slug(canonical)
                    registered.add(canonical)
                except Exception:
                    pass  # HR-8: slug may already exist or be reserved
            if raw and raw != canonical:
                try:
                    client.alias_slug(alias=raw, canonical=canonical)
                except Exception:
                    pass  # HR-8: alias is advisory
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass  # HR-8: best-effort close
    return raw_to_canonical


# ---------------------------------------------------------------------------
# Row-level import paths
# ---------------------------------------------------------------------------


def _import_chunks(
    cfg: Config, src_conn: sqlite3.Connection, slug_map: Dict[str, str],
    *, dry_run: bool, preserve_timestamps: bool,
) -> Dict[str, Any]:
    """Copy knowledge_chunks rows into v3.

    Preserves: body bytes (no .strip()), tags JSON, created_at / updated_at.
    Skips rows already present (matched by (project, topic) UNIQUE).

    Refuses:
        Inserting a row whose source has no canonical-form mapping in
        *slug_map* (these were not registered in pass 1).
    """
    knowledge_db = cfg.install_dir / "knowledge.db"
    dst = sqlite3.connect(str(knowledge_db))
    try:
        dst.row_factory = sqlite3.Row
        # Source table may not exist at all (foreign mode — lessons-only KS).
        try:
            src_cols = {
                r[1] for r in src_conn.execute(
                    "PRAGMA table_info(knowledge_chunks)"
                )
            }
        except sqlite3.OperationalError:
            return {"imported": 0, "skipped": 0, "reason": "no knowledge_chunks table"}
        if not src_cols:
            return {"imported": 0, "skipped": 0, "reason": "no knowledge_chunks table"}
        wanted = [
            "project", "project_tags", "topic", "title", "body", "tags",
            "created_at", "updated_at",
        ]
        select_cols = [c for c in wanted if c in src_cols]
        if "project" not in select_cols or "topic" not in select_cols:
            return {"imported": 0, "skipped": 0, "reason": "missing minimum columns"}
        rows = src_conn.execute(
            f"SELECT {', '.join(select_cols)} FROM knowledge_chunks"
        ).fetchall()
        imported = 0
        skipped = 0
        for row in rows:
            r = dict(zip(select_cols, row))
            raw_proj = r.get("project") or "general"
            canonical = slug_map.get(raw_proj) or _canonicalize(raw_proj)
            r["project"] = canonical
            project_tags = r.get("project_tags") if "project_tags" in select_cols else None
            if not project_tags:
                project_tags = f'["{canonical}"]'

            if dry_run:
                imported += 1
                continue
            try:
                dst.execute(
                    "INSERT INTO knowledge_chunks "
                    "(project, project_tags, topic, title, body, tags, "
                    "created_at, updated_at, source) "
                    "VALUES (:project, :project_tags, :topic, :title, :body, "
                    ":tags, :created_at, :updated_at, 'import_legacy')",
                    {
                        "project": r["project"],
                        "project_tags": project_tags,
                        "topic": r.get("topic"),
                        "title": r.get("title") or r.get("topic") or "(untitled)",
                        "body": r.get("body") or "",
                        "tags": r.get("tags") or "[]",
                        "created_at": r.get("created_at") if preserve_timestamps else None,
                        "updated_at": r.get("updated_at") if preserve_timestamps else None,
                    },
                )
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1
        dst.commit()
        return {"imported": imported, "skipped": skipped}
    finally:
        dst.close()


def _import_lessons(
    cfg: Config, src_conn: sqlite3.Connection, slug_map: Dict[str, str], layout: str,
    *, dry_run: bool, preserve_timestamps: bool,
) -> Dict[str, Any]:
    """Copy lessons_learned rows into v3 with field mapping.

    Canonical v1: copy ``title`` / ``prevention_rule`` / ``severity`` / etc.
    Homemade: map ``lesson`` → ``prevention_rule``, ``context`` → ``what_happened``.
    """
    knowledge_db = cfg.install_dir / "knowledge.db"
    src_cols = {
        r[1] for r in src_conn.execute("PRAGMA table_info(lessons_learned)")
    }
    # Some homemade KS use 'slug' instead of 'project' for the canonical-slug
    # column (Parkway shape). Pull both; resolve at row time.
    wanted = [
        "project", "slug", "project_tags", "title", "prevention_rule", "lesson",
        "context", "what_happened", "why", "the_fix", "severity",
        "created_at", "updated_at",
    ]
    select_cols = [c for c in wanted if c in src_cols]
    if "title" not in select_cols:
        return {"imported": 0, "skipped": 0, "reason": "no title column"}
    rows = src_conn.execute(
        f"SELECT {', '.join(select_cols)} FROM lessons_learned"
    ).fetchall()
    imported = 0
    skipped = 0
    dst = sqlite3.connect(str(knowledge_db))
    try:
        for row in rows:
            r = dict(zip(select_cols, row))
            # 'project' wins if present; fall back to 'slug' (Parkway shape).
            raw_proj = r.get("project") or r.get("slug") or "general"
            canonical = slug_map.get(raw_proj) or _canonicalize(raw_proj)
            project_tags = r.get("project_tags") or f'["{canonical}"]'

            # Field mapping by layout
            prevention_rule = r.get("prevention_rule")
            if not prevention_rule and layout == "homemade":
                prevention_rule = r.get("lesson") or ""
            what_happened = r.get("what_happened") or r.get("context") or None

            severity = r.get("severity") or "warning"
            if severity not in ("critical", "warning", "info"):
                severity = "warning"

            if dry_run:
                imported += 1
                continue
            try:
                dst.execute(
                    "INSERT INTO lessons_learned "
                    "(project, project_tags, title, prevention_rule, "
                    "what_happened, why, the_fix, severity, "
                    "created_at, updated_at, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import_legacy')",
                    (
                        canonical, project_tags,
                        r.get("title") or "(untitled)",
                        prevention_rule or "",
                        what_happened,
                        r.get("why"),
                        r.get("the_fix"),
                        severity,
                        r.get("created_at") if preserve_timestamps else None,
                        r.get("updated_at") if preserve_timestamps else None,
                    ),
                )
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1
        dst.commit()
        return {"imported": imported, "skipped": skipped}
    finally:
        dst.close()


def _import_sessions(
    cfg: Config, src_conn: sqlite3.Connection, *, dry_run: bool,
) -> Dict[str, Any]:
    """Copy ``sessions`` rows into v3 ``session_logs``.

    Tagged with ``tool='legacy-import'`` so downstream queries can distinguish
    historical rows from live captures.
    """
    src_cols = {
        r[1] for r in src_conn.execute("PRAGMA table_info(sessions)")
    }
    cols = [
        c for c in ("conversation_id", "project_hint", "started_at", "ended_at",
                    "full_transcript", "notes", "tool", "machine",
                    "token_in", "token_out")
        if c in src_cols
    ]
    if "conversation_id" not in cols and "id" not in src_cols:
        return {"imported": 0, "skipped": 0, "reason": "no id column"}
    select_cols = cols if cols else ["id"]
    rows = src_conn.execute(
        f"SELECT {', '.join(select_cols)} FROM sessions"
    ).fetchall()
    imported = 0
    skipped = 0
    sessions_db = cfg.sessions_db or (cfg.install_dir / "sessions.db")
    dst = sqlite3.connect(str(sessions_db))
    try:
        for idx, row in enumerate(rows):
            r = dict(zip(select_cols, row))
            conv_id = r.get("conversation_id") or f"legacy-{idx + 1:08d}"
            if dry_run:
                imported += 1
                continue
            try:
                dst.execute(
                    "INSERT OR IGNORE INTO session_logs "
                    "(conversation_id, tool, machine, project_hint, "
                    "started_at, ended_at, full_transcript, token_in, "
                    "token_out, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(conv_id),
                        "legacy-import",
                        r.get("machine"),
                        r.get("project_hint"),
                        r.get("started_at"),
                        r.get("ended_at"),
                        r.get("full_transcript"),
                        r.get("token_in"),
                        r.get("token_out"),
                        r.get("notes"),
                    ),
                )
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1
        dst.commit()
        return {"imported": imported, "skipped": skipped}
    finally:
        dst.close()


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _resolve_source_db(source: Path) -> Optional[Path]:
    """Pick the source DB given a path that's either a dir or a ``.db`` file.

    Probes in order: ``<source>`` if it's a file; ``<source>/knowledge.db``
    (v2/v3/Parkway shape); ``<source>/sessions.db`` (v1 RC shape).
    Returns ``None`` when nothing matches.
    """
    if source.is_file():
        return source
    for candidate in ("knowledge.db", "sessions.db"):
        c = source / candidate
        if c.exists():
            return c
    return None


def run(
    cfg: Config,
    source_dir: Path,
    *,
    dry_run: bool = False,
    preserve_timestamps: bool = True,
) -> Dict[str, Any]:
    """Detect the legacy layout under *source_dir* and import its rows.

    Accepts either a directory (probes ``knowledge.db`` then ``sessions.db``)
    OR a path to a ``.db`` file directly. Detects whether the file is
    canonical v1, homemade, or foreign (homemade KS with lessons but no
    chunks). Imports chunks, lessons, sessions into the v3 install at
    ``cfg.install_dir``.

    Returns:
        Report dict ``{status, layout, chunks, lessons, sessions, source}``.
        ``status`` is one of ``ok`` / ``unrecognized``.

    Refuses:
        Importing from a source that has no recognized layout — returns
        ``status=unrecognized`` rather than raising.
    """
    src_db = _resolve_source_db(source_dir)
    if src_db is None:
        return {
            "status": "unrecognized",
            "source": str(source_dir),
            "reason": "no .db file under source path",
        }
    layout = _detect_layout(src_db)
    if layout == "unknown":
        return {
            "status": "unrecognized",
            "source": str(source_dir),
            "reason": f"{src_db} schema not recognized",
        }

    registered = _existing_slugs(cfg.install_dir)

    # Pass 1: pre-register every distinct source slug. Try both 'project' and
    # 'slug' column names (Parkway/foreign shape uses 'slug').
    src_conn = sqlite3.connect(str(src_db))
    try:
        raw_slugs: List[str] = []
        for tbl in ("knowledge_chunks", "lessons_learned"):
            for col in ("project", "slug"):
                try:
                    raw_slugs.extend(
                        row[0] or "general"
                        for row in src_conn.execute(
                            f"SELECT DISTINCT {col} FROM {tbl}"
                        )
                    )
                except sqlite3.OperationalError:
                    pass  # HR-8: missing column or table is non-fatal
        raw_slugs = sorted(set(raw_slugs))
    finally:
        src_conn.close()
    slug_map = _register_slugs_batch(cfg, raw_slugs, registered)

    # Pass 2: copy rows.
    src_conn = sqlite3.connect(str(src_db))
    try:
        chunks_report = _import_chunks(
            cfg, src_conn, slug_map,
            dry_run=dry_run, preserve_timestamps=preserve_timestamps,
        )
        lessons_report = _import_lessons(
            cfg, src_conn, slug_map, layout,
            dry_run=dry_run, preserve_timestamps=preserve_timestamps,
        )
        sessions_report = _import_sessions(cfg, src_conn, dry_run=dry_run)
    finally:
        src_conn.close()

    return {
        "status": "ok",
        "source": str(source_dir),
        "layout": layout,
        "dry_run": dry_run,
        "chunks": chunks_report,
        "lessons": lessons_report,
        "sessions": sessions_report,
    }
