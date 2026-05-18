"""Tests for cross_system.import_from_markdown (v3.2.0)."""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from runaway_context.cross_system import (
    DataMap,
    ImportReport,
    import_from_markdown,
    _scan_markdown_tables,
)

pytestmark = pytest.mark.feature


SIMPLE_MD = textwrap.dedent("""\
    # Example map

    | System | Name | Kind | Description |
    |--------|------|------|-------------|
    | vista | gldt | view | GL transactions |
    | procore | projects | table | Construction projects |
""")


SYNONYM_MD = textwrap.dedent("""\
    | Source | Table | Type |
    |--------|-------|------|
    | monday | boards | endpoint |
""")


NO_TABLES_MD = "# just narrative, no tables\n\nSome words.\n"


MIXED_MD = textwrap.dedent("""\
    A table we should skip (no system column):

    | A | B |
    |---|---|
    | 1 | 2 |

    A table we should pick up:

    | system | name |
    |--------|------|
    | opc | p6_schedule |
""")


def test_scan_markdown_tables_finds_one(tmp_path):
    tables = _scan_markdown_tables(SIMPLE_MD)
    assert len(tables) == 1
    headers, rows = tables[0]
    assert headers == ["System", "Name", "Kind", "Description"]
    assert len(rows) == 2


def test_import_dry_run_no_writes(tmp_path):
    db = tmp_path / "knowledge.db"
    # Build a minimal v3 DB
    from runaway_context.migrate import migrate
    migrate(db)
    md = tmp_path / "map.md"
    md.write_text(SIMPLE_MD)
    report = import_from_markdown(db, md, dry_run=True)
    assert report.sources_added == 2
    # Confirm nothing landed in the DB
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM data_sources").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_import_writes_sources(tmp_path):
    db = tmp_path / "knowledge.db"
    from runaway_context.migrate import migrate
    migrate(db)
    md = tmp_path / "map.md"
    md.write_text(SIMPLE_MD)
    report = import_from_markdown(db, md, project="general")
    assert report.sources_added == 2
    dm = DataMap(db)
    found = dm.find_sources(system="vista")
    assert any(s["name"] == "gldt" for s in found)


def test_import_recognises_synonyms(tmp_path):
    db = tmp_path / "knowledge.db"
    from runaway_context.migrate import migrate
    migrate(db)
    md = tmp_path / "map.md"
    md.write_text(SYNONYM_MD)
    report = import_from_markdown(db, md)
    assert report.sources_added == 1
    dm = DataMap(db)
    assert any(s["name"] == "boards" for s in dm.find_sources(system="monday"))


def test_import_skips_unmatched_tables(tmp_path):
    db = tmp_path / "knowledge.db"
    from runaway_context.migrate import migrate
    migrate(db)
    md = tmp_path / "map.md"
    md.write_text(MIXED_MD)
    report = import_from_markdown(db, md)
    assert report.sources_added == 1


def test_import_missing_file_returns_noop(tmp_path):
    db = tmp_path / "knowledge.db"
    from runaway_context.migrate import migrate
    migrate(db)
    report = import_from_markdown(db, tmp_path / "doesnotexist.md")
    assert report.sources_added == 0
    assert any("no such file" in n for n in report.notes)


def test_import_no_tables_returns_noop(tmp_path):
    db = tmp_path / "knowledge.db"
    from runaway_context.migrate import migrate
    migrate(db)
    md = tmp_path / "map.md"
    md.write_text(NO_TABLES_MD)
    report = import_from_markdown(db, md)
    assert report.sources_added == 0
    assert any("no markdown tables" in n for n in report.notes)


def test_import_is_idempotent(tmp_path):
    """Running twice adds nothing the second time (add_source upserts)."""
    db = tmp_path / "knowledge.db"
    from runaway_context.migrate import migrate
    migrate(db)
    md = tmp_path / "map.md"
    md.write_text(SIMPLE_MD)
    r1 = import_from_markdown(db, md)
    r2 = import_from_markdown(db, md)
    # Both runs report adding 2 (because add_source is upsert and we don't
    # distinguish insert vs update at the importer layer), but the row count
    # in data_sources should still be 2.
    assert r1.sources_added == r2.sources_added == 2
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM data_sources").fetchone()[0]
    finally:
        conn.close()
    assert n == 2


def test_migrate_reports_data_map_candidate(tmp_path, monkeypatch):
    """When a candidate data-map file exists, migrate sets the hint."""
    from runaway_context import migrate as mig
    # Point the discovery at tmp_path
    target = tmp_path / "claude_database_map.md"
    target.write_text(SIMPLE_MD)
    monkeypatch.setattr(
        mig, "_discover_data_map_file",
        lambda: target,
    )
    db = tmp_path / "knowledge.db"
    report = mig.migrate(db)
    assert report.succeeded
    assert report.data_map_candidate == target
    assert report.data_map_import_command is not None
    assert "import-data-map" in report.data_map_import_command
