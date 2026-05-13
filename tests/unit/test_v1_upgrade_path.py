"""Tests for the v1 → v3 direct upgrade path.

v1 had a single-file install (``~/_knowledge/sessions.db``) with knowledge
tables and conversation transcripts co-located. v3's migrator auto-detects
this layout and copies the transcript rows into a new ``sessions.db``
(non-destructive — the original file is untouched), then applies the v3
additive layer in place.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import doctor
from runaway_context.config import Config
from runaway_context.migrate import (
    detect_v1_layout,
    migrate,
    schema_version,
)


pytestmark = pytest.mark.feature


def _seed_v1_db(install_dir: Path, *, chunks: int = 1, lessons: int = 1,
                sessions: int = 2) -> Path:
    """Create a v1-shaped single-file install.

    Returns:
        Path to the seeded v1 ``sessions.db`` file.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    v1_path = install_dir / "knowledge.db"
    conn = sqlite3.connect(str(v1_path))
    try:
        conn.execute(
            "CREATE TABLE knowledge_chunks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project TEXT, project_tags TEXT DEFAULT '[]', "
            "topic TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, "
            "tags TEXT DEFAULT '[]', "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "UNIQUE(project, topic))"
        )
        conn.execute(
            "CREATE TABLE lessons_learned ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project TEXT, title TEXT NOT NULL, "
            "lesson TEXT, context TEXT, source_session_id INTEGER, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            # v2-style columns the user may already have on a "v1.1" install
            "slug TEXT, what_happened TEXT, why TEXT, the_fix TEXT, "
            "prevention_rule TEXT, severity TEXT DEFAULT 'warning', "
            "status TEXT DEFAULT 'active', superseded_by INTEGER, "
            "project_tags TEXT DEFAULT '[]', source_conversation_ref TEXT, "
            "date_learned DATE, last_revisited DATETIME)"
        )
        # v1's `sessions` table (transcripts) co-located in same file.
        conn.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "conversation_id TEXT, tool TEXT, machine TEXT, "
            "project_hint TEXT, "
            "started_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "ended_at DATETIME, full_transcript TEXT, "
            "token_in INTEGER, token_out INTEGER, notes TEXT)"
        )
        for n in range(chunks):
            conn.execute(
                "INSERT INTO knowledge_chunks (project, topic, title, body, project_tags) "
                "VALUES (?, ?, ?, ?, ?)",
                ("tooling", f"t{n}", f"chunk {n}", "body", '["tooling"]'),
            )
        for n in range(lessons):
            conn.execute(
                "INSERT INTO lessons_learned (title, prevention_rule, project_tags) "
                "VALUES (?, ?, ?)",
                (f"lesson {n}", "do not panic", '["tooling"]'),
            )
        for n in range(sessions):
            conn.execute(
                "INSERT INTO sessions (conversation_id, tool, full_transcript) "
                "VALUES (?, ?, ?)",
                (f"conv-{n}", "claude-code", f"transcript body {n}"),
            )
        conn.commit()
    finally:
        conn.close()
    return v1_path


def test_detect_v1_on_seeded_file(tmp_path):
    """A v1-shaped file is detected."""
    v1 = _seed_v1_db(tmp_path)
    assert detect_v1_layout(v1) is True


def test_detect_v1_false_on_empty(tmp_path):
    """A missing or empty file is not v1."""
    assert detect_v1_layout(tmp_path / "nothing.db") is False


def test_v1_migrate_preserves_knowledge_rows(tmp_path):
    """Knowledge counts survive the v1→v3 auto-split."""
    _seed_v1_db(tmp_path, chunks=3, lessons=4, sessions=2)
    report = migrate(
        tmp_path / "knowledge.db",
        sessions_db=tmp_path / "sessions.db",
        metrics_db=tmp_path / "metrics.db",
        backup=False,
    )
    assert report.aborted_reason is None
    assert report.row_counts_after["knowledge_chunks"] == 3
    assert report.row_counts_after["lessons_learned"] == 4
    assert schema_version(tmp_path / "knowledge.db") == (3, 0, 0)


def test_v1_migrate_copies_transcripts_to_sessions_db(tmp_path):
    """v1's `sessions` rows land in sessions.db as session_logs (non-destructive)."""
    _seed_v1_db(tmp_path, sessions=5)
    migrate(
        tmp_path / "knowledge.db",
        sessions_db=tmp_path / "sessions.db",
        metrics_db=tmp_path / "metrics.db",
        backup=False,
    )
    # sessions.db has the rows
    sconn = sqlite3.connect(str(tmp_path / "sessions.db"))
    try:
        n_logs = sconn.execute(
            "SELECT COUNT(*) FROM session_logs"
        ).fetchone()[0]
    finally:
        sconn.close()
    assert n_logs == 5

    # The original v1 `sessions` table is untouched (HR-4 spirit).
    kconn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    try:
        n_orig = kconn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        kconn.close()
    assert n_orig == 5


def test_v1_migrate_auto_creates_sessions_db_without_explicit_arg(tmp_path):
    """If the user omits --sessions-db, the migrator places sessions.db next to knowledge.db."""
    _seed_v1_db(tmp_path, sessions=2)
    report = migrate(
        tmp_path / "knowledge.db",
        # sessions_db deliberately omitted
        metrics_db=tmp_path / "metrics.db",
        backup=False,
    )
    expected = tmp_path / "sessions.db"
    assert expected.exists()
    assert report.sessions_db == expected


def test_doctor_v1_finding(tmp_path):
    """Doctor emits V1_DB_UNUPGRADED with a v1-specific remediation string."""
    _seed_v1_db(tmp_path)
    cfg = Config.load(tmp_path)
    cfg.save()
    findings = doctor.run_diagnostics(install_dir=tmp_path)
    by_code = {f.code: f for f in findings}
    assert "V1_DB_UNUPGRADED" in by_code, [f.code for f in findings]
    f = by_code["V1_DB_UNUPGRADED"]
    assert f.level == "fail"
    assert "auto-detects v1" in f.remediation
    assert "non-destructive" in f.remediation


def test_doctor_post_v1_upgrade_clean(tmp_path):
    """After the migrator runs, V1_DB_UNUPGRADED is gone."""
    _seed_v1_db(tmp_path)
    migrate(
        tmp_path / "knowledge.db",
        sessions_db=tmp_path / "sessions.db",
        metrics_db=tmp_path / "metrics.db",
        backup=False,
    )
    cfg = Config.load(tmp_path)
    cfg.save()
    findings = doctor.run_diagnostics(install_dir=tmp_path)
    codes = {f.code for f in findings}
    assert "V1_DB_UNUPGRADED" not in codes
    assert "V2_DB_UNUPGRADED" not in codes
