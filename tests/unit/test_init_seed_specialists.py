"""Tests for default-specialist seeding (v3.2.0)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context.init import (
    DEFAULT_SPECIALISTS,
    _seed_specialists,
    run as init_run,
)

pytestmark = pytest.mark.feature


def _count_specialists(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM specialists").fetchone()[0]
    finally:
        conn.close()


def test_default_specialists_constant_shape():
    assert len(DEFAULT_SPECIALISTS) >= 4
    for entry in DEFAULT_SPECIALISTS:
        assert {"name", "domain", "description"}.issubset(entry.keys())
        assert entry["name"] == entry["name"].lower()


def test_seed_specialists_idempotent(tmp_install):
    """Re-running _seed_specialists doesn't duplicate rows."""
    from runaway_context.migrate import migrate
    migrate(tmp_install / "knowledge.db")

    n1 = _seed_specialists(tmp_install)
    n2 = _seed_specialists(tmp_install)
    assert sorted(n1) == sorted(n2)
    assert _count_specialists(tmp_install / "knowledge.db") == len(DEFAULT_SPECIALISTS)


def test_seed_skipped_when_no_db(tmp_install):
    """If knowledge.db is missing, _seed_specialists returns [] without raising."""
    assert _seed_specialists(tmp_install) == []


def test_non_interactive_init_seeds_specialists(tmp_install):
    """`runaway init --non-interactive` seeds specialists by default."""
    cfg = init_run(install_dir=tmp_install, non_interactive=True)
    assert _count_specialists(cfg.install_dir / "knowledge.db") == len(DEFAULT_SPECIALISTS)


def test_non_interactive_init_can_opt_out(tmp_install):
    """`defaults={'seed_specialists': False}` disables seeding."""
    cfg = init_run(
        install_dir=tmp_install, non_interactive=True,
        defaults={"seed_specialists": False},
    )
    assert _count_specialists(cfg.install_dir / "knowledge.db") == 0
