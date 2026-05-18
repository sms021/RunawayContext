"""Tests for detect_partial_shape + check_partial_shape (v3.3.3)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import doctor
from runaway_context.config import Config
from runaway_context.migrate import (
    detect_partial_shape, migrate, _apply_sql_file, _schema_dir,
)

pytestmark = pytest.mark.feature


def _bare_db(path: Path) -> None:
    """Empty SQLite DB (no tables)."""
    conn = sqlite3.connect(str(path))
    conn.close()


def _lessons_only_db(path: Path) -> None:
    """Has lessons_learned but NO knowledge_chunks — the Parkway case."""
    _apply_sql_file(sqlite3.connect(str(path)), _schema_dir() / "000_knowledge_db.sql")
    # Drop chunks to simulate the homemade KS state.
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE knowledge_chunks")
        conn.commit()
    finally:
        conn.close()


def test_detect_partial_returns_none_for_missing_file(tmp_path):
    assert detect_partial_shape(tmp_path / "nope.db") is None


def test_detect_partial_returns_none_for_empty_db(tmp_path):
    db = tmp_path / "k.db"
    _bare_db(db)
    assert detect_partial_shape(db) is None


def test_detect_partial_returns_none_for_full_v3(tmp_path):
    db = tmp_path / "k.db"
    migrate(db)
    assert detect_partial_shape(db) is None


def test_detect_partial_flags_lessons_only(tmp_path):
    db = tmp_path / "k.db"
    _lessons_only_db(db)
    result = detect_partial_shape(db)
    assert result == {
        "present": ["lessons_learned"],
        "missing": ["knowledge_chunks"],
    }


def test_check_partial_shape_warns(tmp_install):
    db = tmp_install / "knowledge.db"
    _lessons_only_db(db)
    cfg = Config.load(tmp_install)
    finding = doctor.check_partial_shape(cfg)
    assert finding.level == "warn"
    assert finding.code == "PARTIAL_SHAPE"
    assert "import-legacy" in finding.remediation


def test_check_partial_shape_ok_after_migrate(tmp_install):
    """A clean v3 install passes the partial-shape check."""
    db = tmp_install / "knowledge.db"
    migrate(db)
    cfg = Config.load(tmp_install)
    finding = doctor.check_partial_shape(cfg)
    assert finding.level == "ok"
