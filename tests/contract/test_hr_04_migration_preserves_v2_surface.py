"""HR-4 contract tests — migration is non-destructive.

HR-4: the v2→v3 migrator never drops a column, never reorders columns, never
shrinks row counts. Idempotent re-application is a no-op. Any destructive
step aborts with MigrationAborted.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from runaway_context.errors import MigrationAborted
from runaway_context.migrate import migrate, schema_version

pytestmark = pytest.mark.contract


# Subset of columns we know v2 had — the additive migrator must keep these.
_V2_LESSONS_COLUMNS = {
    "id", "project", "title", "lesson", "context", "source_session_id",
    "created_at", "updated_at",
}
_V2_CHUNKS_COLUMNS = {
    "id", "project", "topic", "title", "body", "tags", "created_at", "updated_at",
}


def _columns(conn: sqlite3.Connection, table: str):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_hr_04_v2_columns_present_after_migrate(fresh_db) -> None:
    """HR-4: v2 columns must still exist after the migrator runs."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        cols_ll = _columns(conn, "lessons_learned")
        cols_kc = _columns(conn, "knowledge_chunks")
    finally:
        conn.close()
    missing_ll = _V2_LESSONS_COLUMNS - cols_ll
    missing_kc = _V2_CHUNKS_COLUMNS - cols_kc
    assert not missing_ll, f"HR-4: missing v2 lessons cols: {missing_ll}"
    assert not missing_kc, f"HR-4: missing v2 chunks cols: {missing_kc}"


def test_hr_04_row_counts_preserved(tmp_install: Path) -> None:
    """HR-4: pre-existing rows survive migration; row counts never shrink."""
    # Apply the canonical v3 schema first so we know the table shape matches
    # what a real v2-then-v3 install would look like at row-insert time.
    from runaway_context.migrate import migrate as _migrate
    knowledge_db = tmp_install / "knowledge.db"
    _migrate(knowledge_db)

    # Then add some seed rows that look like v2-shaped writes (no v3 columns
    # touched).
    conn = sqlite3.connect(str(knowledge_db))
    try:
        conn.execute(
            "INSERT INTO knowledge_chunks (project, topic, title, body, project_tags) "
            "VALUES ('p1', 't1', 'title1', 'body1', '[\"p1\"]')"
        )
        conn.execute(
            "INSERT INTO lessons_learned (project, title, project_tags) "
            "VALUES ('p1', 'L1', '[\"p1\"]')"
        )
        conn.commit()
    finally:
        conn.close()

    report = migrate(knowledge_db)
    assert report.succeeded
    assert report.row_counts_before.get("lessons_learned", 0) == 1
    assert report.row_counts_after.get("lessons_learned", 0) >= 1
    assert report.row_counts_before.get("knowledge_chunks", 0) == 1
    assert report.row_counts_after.get("knowledge_chunks", 0) >= 1


def test_hr_04_idempotent_reapplication(fresh_db) -> None:
    """HR-4: applying migrate() twice must not lose data."""
    # First call already ran in the fixture. Re-apply.
    report = migrate(fresh_db)
    assert report.succeeded
    assert schema_version(fresh_db) is not None


def test_hr_04_aborts_on_column_loss(tmp_install: Path, monkeypatch) -> None:
    """HR-4: the migrator aborts when a column present pre-step disappears."""
    from runaway_context import migrate as migrate_mod

    # Start from a fully-migrated DB so cols_before is populated with real
    # v3 columns. Then on the *post-step* call simulate a column being lost
    # by stripping one entry — this is the failure mode the abort guard is
    # designed to detect (HR-4).
    knowledge_db = tmp_install / "knowledge.db"
    migrate_mod.migrate(knowledge_db)  # first run fully migrates

    real_table_info = migrate_mod._table_info
    real_apply = migrate_mod._apply_sql_file
    state = {"after_phase": False}

    def marker_apply(conn, path):
        state["after_phase"] = True
        return real_apply(conn, path)

    def fake_table_info(conn, table):
        info = real_table_info(conn, table)
        if state["after_phase"] and table == "lessons_learned":
            # Simulate the destructive change: drop a column that was
            # captured pre-step.
            info.pop("title", None)
        return info

    monkeypatch.setattr(migrate_mod, "_apply_sql_file", marker_apply)
    monkeypatch.setattr(migrate_mod, "_table_info", fake_table_info)
    with pytest.raises(MigrationAborted):
        migrate_mod.migrate(knowledge_db)
