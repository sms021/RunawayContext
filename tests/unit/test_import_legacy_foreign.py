"""Tests for import-legacy foreign-shape support (v3.3.3)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context.config import Config
from runaway_context.import_legacy import (
    _detect_layout, _resolve_source_db, run as import_run,
)

pytestmark = pytest.mark.feature


def _make_parkway_ks(path: Path, n_lessons: int = 3) -> None:
    """Build a Parkway-shape KS: lessons_learned with 'slug' instead of 'project',
    no knowledge_chunks table at all."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("""
            CREATE TABLE lessons_learned (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                what_happened TEXT,
                why TEXT,
                the_fix TEXT,
                prevention_rule TEXT,
                severity TEXT DEFAULT 'warning',
                status TEXT DEFAULT 'active',
                project_tags TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME
            )
        """)
        for i in range(n_lessons):
            conn.execute(
                "INSERT INTO lessons_learned (slug, title, prevention_rule, severity) "
                "VALUES (?, ?, ?, ?)",
                (f"parkway_{i % 2}", f"Lesson {i}", f"Rule {i}", "warning"),
            )
        conn.commit()
    finally:
        conn.close()


def test_detect_layout_foreign_for_parkway(tmp_path):
    db = tmp_path / "knowledge.db"
    _make_parkway_ks(db)
    assert _detect_layout(db) == "foreign"


def test_resolve_source_db_accepts_file_path(tmp_path):
    db = tmp_path / "knowledge.db"
    _make_parkway_ks(db)
    assert _resolve_source_db(db) == db


def test_resolve_source_db_prefers_knowledge_db(tmp_path):
    """When both knowledge.db and sessions.db exist, knowledge.db wins."""
    (tmp_path / "knowledge.db").touch()
    (tmp_path / "sessions.db").touch()
    assert _resolve_source_db(tmp_path).name == "knowledge.db"


def test_resolve_source_db_falls_back_to_sessions_db(tmp_path):
    (tmp_path / "sessions.db").touch()
    assert _resolve_source_db(tmp_path).name == "sessions.db"


def test_resolve_source_db_returns_none_when_empty(tmp_path):
    assert _resolve_source_db(tmp_path) is None


def test_foreign_import_creates_v3_install_with_lessons(tmp_install, tmp_path):
    """The full pipeline: Parkway KS → fresh v3 install."""
    from runaway_context.migrate import migrate
    migrate(tmp_install / "knowledge.db")

    src_db = tmp_path / "src_knowledge.db"
    _make_parkway_ks(src_db, n_lessons=4)

    cfg = Config.load(tmp_install)
    report = import_run(cfg, src_db, dry_run=False)

    assert report["status"] == "ok"
    assert report["layout"] == "foreign"
    assert report["lessons"]["imported"] == 4
    # chunks report should be a noop (no chunks table in source)
    assert report["chunks"]["imported"] == 0
    assert "no knowledge_chunks" in report["chunks"].get("reason", "")

    # Verify the rows landed with source='import_legacy'
    conn = sqlite3.connect(str(tmp_install / "knowledge.db"))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM lessons_learned WHERE source = 'import_legacy'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 4


def test_foreign_import_via_dir(tmp_install, tmp_path):
    """import-legacy --from <dir> finds knowledge.db inside the dir."""
    from runaway_context.migrate import migrate
    migrate(tmp_install / "knowledge.db")

    src_dir = tmp_path / "parkway_ks"
    src_dir.mkdir()
    _make_parkway_ks(src_dir / "knowledge.db", n_lessons=2)

    cfg = Config.load(tmp_install)
    report = import_run(cfg, src_dir)

    assert report["status"] == "ok"
    assert report["layout"] == "foreign"
    assert report["lessons"]["imported"] == 2


def test_foreign_import_slug_aliasing(tmp_install, tmp_path):
    """Source slugs like 'parkway_0' get registered into slug_registry."""
    from runaway_context.migrate import migrate
    migrate(tmp_install / "knowledge.db")

    src_db = tmp_path / "k.db"
    _make_parkway_ks(src_db, n_lessons=2)

    cfg = Config.load(tmp_install)
    import_run(cfg, src_db)

    conn = sqlite3.connect(str(tmp_install / "knowledge.db"))
    try:
        slugs = {
            r[0] for r in conn.execute(
                "SELECT slug FROM slug_registry WHERE status = 'active'"
            )
        }
    finally:
        conn.close()
    # parkway_0 and parkway_1 should both be registered
    assert "parkway_0" in slugs
    assert "parkway_1" in slugs
