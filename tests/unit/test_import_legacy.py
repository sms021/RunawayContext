"""Unit tests for runaway import-legacy."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


pytestmark = pytest.mark.feature


def _seed_homemade_db(src_dir: Path) -> Path:
    """Create the schema shape Steven's homemade sessions.py used."""
    src_dir.mkdir(parents=True, exist_ok=True)
    db = src_dir / "sessions.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE knowledge_chunks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project TEXT, project_tags TEXT, "
            "topic TEXT, title TEXT, body TEXT, tags TEXT, "
            "created_at DATETIME, updated_at DATETIME)"
        )
        conn.execute(
            "CREATE TABLE lessons_learned ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project TEXT, project_tags TEXT, title TEXT, "
            "lesson TEXT, context TEXT, source_session_id INTEGER, "
            "created_at DATETIME, updated_at DATETIME)"
        )
        conn.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "conversation_id TEXT, tool TEXT, started_at DATETIME, "
            "ended_at DATETIME, full_transcript TEXT, "
            "token_in INTEGER, token_out INTEGER, notes TEXT)"
        )
        conn.execute(
            "INSERT INTO knowledge_chunks (project, project_tags, topic, title, body, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("3DProjects", '["3DProjects"]', "topic-a", "title-a", "body-a\n", '["x"]'),
        )
        conn.execute(
            "INSERT INTO knowledge_chunks (project, project_tags, topic, title, body, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("runaway-knight", '["runaway-knight"]', "topic-b", "title-b", "body-b\n", '["y"]'),
        )
        conn.execute(
            "INSERT INTO lessons_learned (project, project_tags, title, lesson, context) "
            "VALUES (?, ?, ?, ?, ?)",
            ("3DProjects", '["3DProjects"]', "lesson-1", "do not panic", "we panicked"),
        )
        conn.execute(
            "INSERT INTO sessions (conversation_id, tool, full_transcript) "
            "VALUES (?, ?, ?)",
            ("conv-A", "claude-code", "transcript A"),
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _seed_v3_install(install_dir: Path) -> None:
    """Create a minimal v3 install at *install_dir* (knowledge.db + sessions.db)."""
    from runaway_context.migrate import migrate

    install_dir.mkdir(parents=True, exist_ok=True)
    migrate(
        knowledge_db=install_dir / "knowledge.db",
        sessions_db=install_dir / "sessions.db",
        metrics_db=install_dir / "metrics.db",
    )


def test_detect_homemade_layout(tmp_path: Path):
    from runaway_context.import_legacy import _detect_layout

    src = tmp_path / "legacy"
    _seed_homemade_db(src)
    assert _detect_layout(src / "sessions.db") == "homemade"


def test_run_imports_chunks_lessons_sessions(tmp_path: Path):
    from runaway_context.config import Config
    from runaway_context import import_legacy as imp

    src = tmp_path / "legacy"
    _seed_homemade_db(src)
    install = tmp_path / "install"
    _seed_v3_install(install)
    cfg = Config(install_dir=install,
                 knowledge_db=install / "knowledge.db",
                 sessions_db=install / "sessions.db",
                 metrics_db=install / "metrics.db")

    report = imp.run(cfg=cfg, source_dir=src)

    assert report["status"] == "ok"
    assert report["layout"] == "homemade"
    assert report["chunks"]["imported"] == 2
    assert report["lessons"]["imported"] == 1
    assert report["sessions"]["imported"] == 1


def test_hyphen_slug_normalized_to_canonical(tmp_path: Path):
    """A 'runaway-knight' source slug auto-registers 'runaway_knight'.

    HR-2 requires alias slugs themselves be canonical-format, so the hyphen
    form cannot also be aliased — it's just dropped after canonicalization.
    Imported rows reference the canonical slug.
    """
    from runaway_context.config import Config
    from runaway_context import import_legacy as imp

    src = tmp_path / "legacy"
    _seed_homemade_db(src)
    install = tmp_path / "install"
    _seed_v3_install(install)
    cfg = Config(install_dir=install,
                 knowledge_db=install / "knowledge.db",
                 sessions_db=install / "sessions.db",
                 metrics_db=install / "metrics.db")
    imp.run(cfg=cfg, source_dir=src)

    conn = sqlite3.connect(str(install / "knowledge.db"))
    try:
        slugs = {row[0] for row in conn.execute(
            "SELECT slug FROM slug_registry WHERE deprecated_at IS NULL"
        )}
        assert "runaway_knight" in slugs  # canonical registered
        # The imported chunk for the hyphen-named project points at canonical
        n = conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE project = ?",
            ("runaway_knight",),
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_dry_run_does_not_write(tmp_path: Path):
    from runaway_context.config import Config
    from runaway_context import import_legacy as imp

    src = tmp_path / "legacy"
    _seed_homemade_db(src)
    install = tmp_path / "install"
    _seed_v3_install(install)
    cfg = Config(install_dir=install,
                 knowledge_db=install / "knowledge.db",
                 sessions_db=install / "sessions.db",
                 metrics_db=install / "metrics.db")

    report = imp.run(cfg=cfg, source_dir=src, dry_run=True)
    assert report["status"] == "ok"

    # Destination still empty
    conn = sqlite3.connect(str(install / "knowledge.db"))
    try:
        n_chunks = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        n_lessons = conn.execute("SELECT COUNT(*) FROM lessons_learned").fetchone()[0]
    finally:
        conn.close()
    assert n_chunks == 0
    assert n_lessons == 0


def test_unrecognized_source_returns_status_not_raises(tmp_path: Path):
    from runaway_context.config import Config
    from runaway_context import import_legacy as imp

    src = tmp_path / "empty"
    src.mkdir()
    install = tmp_path / "install"
    _seed_v3_install(install)
    cfg = Config(install_dir=install,
                 knowledge_db=install / "knowledge.db",
                 sessions_db=install / "sessions.db",
                 metrics_db=install / "metrics.db")
    report = imp.run(cfg=cfg, source_dir=src)
    assert report["status"] == "unrecognized"
