"""Tests for the v2 → v3 upgrade detection path.

Two surfaces:

* :func:`runaway_context.init.detect_v2_install` — used by the wizard.
* :func:`runaway_context.doctor.check_schema_version` — emits the
  ``V2_DB_UNUPGRADED`` finding adopters' AIs route on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import doctor
from runaway_context.config import Config
from runaway_context.init import detect_v2_install
from runaway_context.migrate import migrate


pytestmark = pytest.mark.feature


def _seed_v2_db(install_dir: Path, *, chunks: int = 0, lessons: int = 0) -> Path:
    """Create a realistic v2-shaped knowledge.db (no schema_version row).

    Mirrors the v2.0.1 schema surface (knowledge_chunks + the post-ALTER
    lessons_learned column set) so the v3 migrator runs against the shape it
    will see in real installs.

    Returns:
        Path to the seeded DB.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    kdb = install_dir / "knowledge.db"
    conn = sqlite3.connect(str(kdb))
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
            # v2 ALTER columns
            "slug TEXT, what_happened TEXT, why TEXT, the_fix TEXT, "
            "prevention_rule TEXT, severity TEXT DEFAULT 'warning', "
            "status TEXT DEFAULT 'active', superseded_by INTEGER, "
            "project_tags TEXT DEFAULT '[]', source_conversation_ref TEXT, "
            "date_learned DATE, last_revisited DATETIME)"
        )
        conn.execute(
            "CREATE TABLE project_context_card ("
            "project TEXT PRIMARY KEY, "
            "top_warnings TEXT DEFAULT '[]', "
            "active_lesson_ids TEXT DEFAULT '[]', "
            "active_chunk_ids TEXT DEFAULT '[]', "
            "md_path TEXT, md_line_cap INTEGER DEFAULT 150, "
            "last_rebuilt DATETIME, notes TEXT)"
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
        conn.commit()
    finally:
        conn.close()
    return kdb


# ---------------------------------------------------------------------------
# detect_v2_install
# ---------------------------------------------------------------------------


def test_detect_v2_install_on_fresh_dir(tmp_path):
    """Empty directory → not a v2 install."""
    rpt = detect_v2_install(tmp_path)
    assert rpt["is_v2"] is False
    assert rpt["has_knowledge_db"] is False
    assert rpt["has_v3_config"] is False


def test_detect_v2_install_with_v2_tables(tmp_path):
    """v2 tables present and no schema_version → is_v2 True with row counts."""
    _seed_v2_db(tmp_path, chunks=2, lessons=3)
    rpt = detect_v2_install(tmp_path)
    assert rpt["is_v2"] is True
    assert rpt["has_knowledge_db"] is True
    assert rpt["row_counts"]["knowledge_chunks"] == 2
    assert rpt["row_counts"]["lessons_learned"] == 3


def test_detect_v2_install_after_v3_migrate(tmp_path):
    """Once migrated, the same DB no longer counts as v2."""
    _seed_v2_db(tmp_path, chunks=1, lessons=1)
    migrate(
        tmp_path / "knowledge.db",
        tmp_path / "sessions.db",
        tmp_path / "metrics.db",
        backup=False,
    )
    rpt = detect_v2_install(tmp_path)
    assert rpt["is_v2"] is False
    assert rpt["has_knowledge_db"] is True


def test_detect_v2_preserves_rows_after_migrate(tmp_path):
    """HR-4: row counts survive the in-place v2→v3 migrator."""
    _seed_v2_db(tmp_path, chunks=4, lessons=7)
    before = detect_v2_install(tmp_path)["row_counts"]
    migrate(
        tmp_path / "knowledge.db",
        tmp_path / "sessions.db",
        tmp_path / "metrics.db",
        backup=False,
    )
    conn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    try:
        kn = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        ll = conn.execute("SELECT COUNT(*) FROM lessons_learned").fetchone()[0]
    finally:
        conn.close()
    assert kn == before["knowledge_chunks"]
    assert ll == before["lessons_learned"]


# ---------------------------------------------------------------------------
# doctor: V2_DB_UNUPGRADED finding
# ---------------------------------------------------------------------------


def test_doctor_v2_db_emits_v2_unupgraded_fail(tmp_path):
    """A v2 DB triggers the V2_DB_UNUPGRADED finding with a migrate remediation."""
    _seed_v2_db(tmp_path, chunks=1, lessons=1)
    # Persist a config so doctor's Config.load() uses this dir.
    cfg = Config.load(tmp_path)
    cfg.save()
    findings = doctor.run_diagnostics(install_dir=tmp_path)
    by_code = {f.code: f for f in findings}
    assert "V2_DB_UNUPGRADED" in by_code, [f.code for f in findings]
    f = by_code["V2_DB_UNUPGRADED"]
    assert f.level == "fail"
    assert "runaway db migrate" in f.remediation
    assert "HR-4" in f.remediation


def test_doctor_post_upgrade_no_v2_finding(tmp_path):
    """After `runaway db migrate`, V2_DB_UNUPGRADED is gone and SCHEMA_VERSION is ok."""
    _seed_v2_db(tmp_path, chunks=1, lessons=1)
    migrate(
        tmp_path / "knowledge.db",
        tmp_path / "sessions.db",
        tmp_path / "metrics.db",
        backup=False,
    )
    cfg = Config.load(tmp_path)
    cfg.save()
    findings = doctor.run_diagnostics(install_dir=tmp_path)
    codes = {f.code for f in findings}
    assert "V2_DB_UNUPGRADED" not in codes
    by_code = {f.code: f for f in findings}
    assert by_code["SCHEMA_VERSION"].level == "ok"
