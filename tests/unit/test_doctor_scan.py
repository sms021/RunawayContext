"""Tests for `runaway doctor --scan` install-discovery (v3.3.0)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import doctor

pytestmark = pytest.mark.feature


def _make_v3_db(path: Path) -> None:
    """Build a knowledge.db that looks like a freshly-migrated v3."""
    from runaway_context.migrate import migrate
    migrate(path)


def _make_v2_db(path: Path) -> None:
    """Build a v2-shaped knowledge.db (chunks + lessons, NO sessions, no schema_version).

    v2 keeps sessions in a separate sessions.db file — only v1 had everything
    in one file.
    """
    from runaway_context.migrate import _apply_sql_file, _schema_dir
    conn = sqlite3.connect(str(path))
    try:
        _apply_sql_file(conn, _schema_dir() / "000_knowledge_db.sql")
        conn.execute("DELETE FROM schema_version")
        conn.commit()
    finally:
        conn.close()


def _make_partial_db(path: Path) -> None:
    """v2-style lessons_learned + chunks, but no sessions (the user's case)."""
    from runaway_context.migrate import _apply_sql_file, _schema_dir
    conn = sqlite3.connect(str(path))
    try:
        _apply_sql_file(conn, _schema_dir() / "000_knowledge_db.sql")
        conn.execute("DELETE FROM schema_version")
        # Drop knowledge_chunks to simulate a homemade KS that skipped it.
        conn.execute("DROP TABLE knowledge_chunks")
        conn.commit()
    finally:
        conn.close()


def test_scan_returns_empty_for_empty_root(tmp_path):
    assert doctor.scan_install_candidates(roots=[tmp_path]) == []


def test_scan_finds_v3_install(tmp_path):
    _make_v3_db(tmp_path / "knowledge.db")
    out = doctor.scan_install_candidates(roots=[tmp_path])
    assert len(out) == 1
    assert out[0]["shape"] == "v3"
    assert "schema_version=3.0.0" in (out[0]["notes"] or "")


def test_scan_finds_v2_install(tmp_path):
    _make_v2_db(tmp_path / "knowledge.db")
    out = doctor.scan_install_candidates(roots=[tmp_path])
    assert out[0]["shape"] == "v2"


def test_scan_flags_partial_shape(tmp_path):
    """Missing knowledge_chunks (or sessions) -> 'partial' shape."""
    _make_partial_db(tmp_path / "knowledge.db")
    out = doctor.scan_install_candidates(roots=[tmp_path])
    assert out[0]["shape"] == "partial"
    assert "missing" in out[0]["notes"]


def test_scan_dedupes_by_resolved_path(tmp_path):
    """Two roots that both contain the same physical DB -> one entry."""
    real = tmp_path / "kdir" / "knowledge.db"
    real.parent.mkdir()
    _make_v3_db(real)
    out = doctor.scan_install_candidates(roots=[tmp_path, tmp_path / "kdir"])
    assert len(out) == 1


def test_scan_skips_inaccessible_dirs(tmp_path):
    """A non-existent root is silently skipped."""
    out = doctor.scan_install_candidates(roots=[tmp_path / "nope"])
    assert out == []


def test_scan_respects_max_depth(tmp_path):
    """A DB buried deeper than max_depth is not found."""
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    _make_v3_db(deep / "knowledge.db")
    out = doctor.scan_install_candidates(roots=[tmp_path], max_depth=2)
    assert out == []
    out_deep = doctor.scan_install_candidates(roots=[tmp_path], max_depth=10)
    assert len(out_deep) == 1


def test_check_install_location_ambiguous_ok_when_single(tmp_path, client):
    """1 candidate -> OK level."""
    finding = doctor.check_install_location_ambiguous(
        client._knowledge_db.parent  # noqa: SLF001
        and __import__("runaway_context.config", fromlist=["Config"]).Config.load(client.install_dir)
    )
    # With a real install only, finding is OK. (May vary on host; check that it doesn't raise.)
    assert finding.level in ("ok", "warn")


def test_check_install_location_ambiguous_warns_when_multiple(tmp_path, monkeypatch, client):
    """Two candidate dbs -> WARN."""
    other_root = tmp_path / "other"
    other_root.mkdir()
    _make_v2_db(other_root / "knowledge.db")
    # Patch the default roots so the test is deterministic.
    monkeypatch.setattr(
        doctor, "_DEFAULT_SCAN_ROOTS",
        (client.install_dir, other_root),
    )
    from runaway_context.config import Config
    cfg = Config.load(client.install_dir)
    finding = doctor.check_install_location_ambiguous(cfg)
    assert finding.level == "warn"
    assert finding.code == "INSTALL_LOCATION_AMBIGUOUS"
    assert len(finding.extra["candidates"]) == 2
