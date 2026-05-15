"""HR-4 contract tests — migrator is atomic and refuses foreign v1-shaped DBs.

Two failure modes the migrator must defend against:

1. A foreign DB (homemade or unrelated) that happens to share v1 table names
   but has different columns. Real incident 2026-05-13: a user's homemade
   ``sessions.py`` system was partially migrated before failure; the failed
   migration left ``schema_version`` and ``session_logs`` tables behind in the
   user's foreign file, requiring manual restore.

2. Any SQL error during schema application that would otherwise leave a
   half-migrated file on disk.

The migrator's contract:
- Refuse to write when a foreign v1-shaped DB is detected. No tables are
  created. The error message points at ``runaway import-legacy``.
- If schema application fails after starting, restore the pre-migration backup
  before raising.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context.errors import MigrationAborted
from runaway_context.migrate import (
    detect_foreign_v1_shape,
    detect_v1_layout,
    migrate,
)

pytestmark = pytest.mark.contract


def _seed_foreign_v1_shape(install_dir: Path) -> Path:
    """Create a DB with v1 table NAMES but a non-canonical column shape.

    This is exactly the shape of the homemade ``sessions.py`` system that
    triggered the 2026-05-13 incident: ``lessons_learned`` has ``lesson`` +
    ``context`` instead of canonical v1's ``prevention_rule``.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    path = install_dir / "sessions.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE knowledge_chunks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project TEXT, "
            "title TEXT, body TEXT)"  # missing canonical 'topic'
        )
        conn.execute(
            "CREATE TABLE lessons_learned ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project TEXT, title TEXT, "
            "lesson TEXT, context TEXT, source_session_id INTEGER)"
            # missing canonical 'prevention_rule'
        )
        conn.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "tool TEXT, started_at DATETIME)"
            # missing canonical 'conversation_id' AND 'full_transcript'
        )
        conn.execute(
            "INSERT INTO lessons_learned (title, lesson, context) "
            "VALUES (?, ?, ?)",
            ("user-data", "do not lose me", "user authored this"),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_hr_04_foreign_v1_shape_is_detected(tmp_path: Path) -> None:
    """detect_foreign_v1_shape names the missing canonical columns."""
    path = _seed_foreign_v1_shape(tmp_path)
    foreign = detect_foreign_v1_shape(path)
    assert foreign is not None
    assert "lessons_learned" in foreign
    assert "prevention_rule" in foreign["lessons_learned"]
    # And it is NOT recognized as a real v1 install
    assert detect_v1_layout(path) is False


def test_hr_04_migrator_refuses_foreign_v1_shape(tmp_path: Path) -> None:
    """Migrator raises MigrationAborted with the import-legacy hint."""
    path = _seed_foreign_v1_shape(tmp_path)
    with pytest.raises(MigrationAborted) as excinfo:
        migrate(path)
    msg = str(excinfo.value)
    assert "runaway import-legacy" in msg
    assert "lessons_learned" in msg


def test_hr_04_foreign_v1_shape_leaves_file_untouched(tmp_path: Path) -> None:
    """The user's file is byte-identical after a refused migration.

    Real incident: the migrator partially wrote ``schema_version`` and
    ``session_logs`` into the user's foreign DB before failing. The fix is
    refuse-before-write. This test asserts no v3 tables appear on disk and
    no user rows were lost.
    """
    path = _seed_foreign_v1_shape(tmp_path)
    bytes_before = path.read_bytes()

    with pytest.raises(MigrationAborted):
        migrate(path)

    bytes_after = path.read_bytes()
    assert bytes_after == bytes_before, "foreign DB was modified by refused migration"

    # And the user row is still readable
    conn = sqlite3.connect(str(path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "schema_version" not in tables
        assert "session_logs" not in tables
        row = conn.execute(
            "SELECT title, lesson FROM lessons_learned"
        ).fetchone()
        assert row == ("user-data", "do not lose me")
    finally:
        conn.close()


def test_hr_04_fresh_db_migrates_cleanly(tmp_path: Path) -> None:
    """A non-existent target migrates without tripping the foreign-shape guard."""
    target = tmp_path / "fresh" / "knowledge.db"
    report = migrate(target)
    assert report.aborted_reason is None
    assert target.exists()
    conn = sqlite3.connect(str(target))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "lessons_learned" in tables
        assert "knowledge_chunks" in tables
        assert "schema_version" in tables
    finally:
        conn.close()


def test_hr_04_atomic_rollback_on_schema_error(tmp_path: Path, monkeypatch) -> None:
    """If schema application raises mid-flight, the file is restored from backup."""
    from runaway_context import migrate as migrate_mod

    target = tmp_path / "knowledge.db"
    # Seed a canonical v1 install so the foreign-shape guard does NOT trip.
    conn = sqlite3.connect(str(target))
    try:
        conn.execute(
            "CREATE TABLE knowledge_chunks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, "
            "topic TEXT, title TEXT, body TEXT)"
        )
        conn.execute(
            "CREATE TABLE lessons_learned ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, title TEXT, "
            "prevention_rule TEXT)"
        )
        conn.execute(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, "
            "full_transcript TEXT)"
        )
        conn.execute(
            "INSERT INTO lessons_learned (title, prevention_rule) VALUES (?, ?)",
            ("canon", "rule"),
        )
        conn.commit()
    finally:
        conn.close()
    bytes_before = target.read_bytes()

    # Force _apply_sql_file to raise after the first commit-able statement
    original = migrate_mod._apply_sql_file
    calls = {"n": 0}

    def faulty_apply(conn, sql_path):
        calls["n"] += 1
        if calls["n"] >= 2:  # let the first file pass, blow up on the second
            raise sqlite3.OperationalError("simulated mid-migration failure")
        return original(conn, sql_path)

    monkeypatch.setattr(migrate_mod, "_apply_sql_file", faulty_apply)

    with pytest.raises(MigrationAborted):
        migrate(target)

    # Backup should exist
    backup = target.with_suffix(target.suffix + ".pre-v3.bak")
    assert backup.exists()

    # Target restored — content must match pre-migration bytes
    assert target.read_bytes() == bytes_before, (
        "target file was not restored after mid-migration failure"
    )
