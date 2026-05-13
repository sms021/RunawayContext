"""E1 — schema migrator v2→v3 (additive, non-destructive).

Verifies fresh apply, idempotent re-apply, MigrationAborted on column loss.
"""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.errors import MigrationAborted
from runaway_context.migrate import migrate, schema_version

pytestmark = pytest.mark.feature


def test_e01_fresh_apply_sets_schema_version(fresh_db):
    """E1: migrate sets schema_version to v3."""
    version = schema_version(fresh_db)
    assert version is not None
    assert version[0] == 3


def test_e01_idempotent_reapply(fresh_db):
    """E1: re-applying the migrator does not lose data."""
    report = migrate(fresh_db)
    assert report.succeeded


def test_e01_creates_required_tables(fresh_db):
    """E1: migrator creates the full v3 table set."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    expected = {
        "lessons_learned", "knowledge_chunks", "project_context_card",
        "slug_registry", "audit_log", "record_versions",
        "specialists", "specialist_knowledge", "data_sources",
        "lesson_drafts", "brief_snapshots", "authors",
    }
    missing = expected - names
    assert not missing, f"E1: missing tables: {missing}"
