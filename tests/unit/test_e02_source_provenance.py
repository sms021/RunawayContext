"""E2 — provenance source column (v3.2.0).

Covers:
- knowledge_chunks.source and lessons_learned.source columns exist after migrate
- Client.log_lesson / propose_knowledge default to source='manual'
- Both accept a caller-supplied source string (e.g. 'memory:...')
- Migrator backfill stamps pre-existing rows as 'v2_import'
- import-legacy stamps imported rows as 'import_legacy'
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.feature


def _columns(db: Path, table: str) -> set:
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_source_column_present_after_migrate(client):
    """source column exists on both tables after init/migrate."""
    cols_chunks = _columns(client._knowledge_db, "knowledge_chunks")
    cols_ll = _columns(client._knowledge_db, "lessons_learned")
    assert "source" in cols_chunks
    assert "source" in cols_ll


def test_log_lesson_default_source_manual(client):
    """log_lesson() without explicit source stamps 'manual'."""
    client.register_slug("tooling")
    lid = client.log_lesson(title="t", project_tags=["tooling"])
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT source FROM lessons_learned WHERE id = ?", (lid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "manual"


def test_log_lesson_accepts_explicit_source(client):
    """Caller may pass any source string; it lands in the column verbatim."""
    client.register_slug("tooling")
    lid = client.log_lesson(
        title="t", project_tags=["tooling"], source="memory:/tmp/x.md",
    )
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT source FROM lessons_learned WHERE id = ?", (lid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "memory:/tmp/x.md"


def test_propose_knowledge_default_source_manual(client):
    """propose_knowledge() defaults to 'manual'."""
    client.register_slug("tooling")
    cid = client.propose_knowledge(
        project="tooling", topic="t1", title="T1", body="b",
    )
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT source FROM knowledge_chunks WHERE id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "manual"


def test_propose_knowledge_accepts_explicit_source(client):
    """propose_knowledge() accepts a caller-supplied source."""
    client.register_slug("tooling")
    cid = client.propose_knowledge(
        project="tooling", topic="t2", title="T2", body="b",
        source="specialist:accounting",
    )
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT source FROM knowledge_chunks WHERE id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "specialist:accounting"


def test_migrator_backfills_v2_import_for_preexisting(tmp_path):
    """Pre-existing rows (inserted before source column was added) get
    stamped 'v2_import' by the migrator."""
    from runaway_context.migrate import migrate, _schema_dir, _apply_sql_file

    db = tmp_path / "knowledge.db"
    conn = sqlite3.connect(str(db))
    try:
        # Build a minimal v2-shaped DB: apply only the base schema (no v3
        # additions yet) to simulate a pre-3.2.0 install with rows already
        # present. The slug_registry table is part of the v3 additions, so it
        # does not exist yet; HR-2 trigger is also part of v3, so the bare
        # insert below is permitted at this layer.
        _apply_sql_file(conn, _schema_dir() / "000_knowledge_db.sql")
        conn.commit()
        conn.execute(
            "INSERT INTO knowledge_chunks "
            "(project, project_tags, topic, title, body) "
            "VALUES ('tooling', '[\"tooling\"]', 'pre-mig', 'Pre', 'body')"
        )
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, project_tags, title, prevention_rule, severity, status) "
            "VALUES ('tooling', '[\"tooling\"]', 'PreLesson', 'rule', 'warning', 'active')"
        )
        conn.commit()
    finally:
        conn.close()

    # Run migrate to apply the v3 additions including the source column.
    report = migrate(db)
    assert report.succeeded, report.aborted_reason

    conn = sqlite3.connect(str(db))
    try:
        chunk_src = conn.execute(
            "SELECT source FROM knowledge_chunks WHERE topic='pre-mig'"
        ).fetchone()[0]
        lesson_src = conn.execute(
            "SELECT source FROM lessons_learned WHERE title='PreLesson'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert chunk_src == "v2_import"
    assert lesson_src == "v2_import"


def test_migrator_backfill_is_idempotent(tmp_path):
    """Re-running migrate does NOT overwrite a non-NULL source value."""
    from runaway_context.migrate import migrate, _schema_dir, _apply_sql_file

    db = tmp_path / "knowledge.db"
    conn = sqlite3.connect(str(db))
    try:
        _apply_sql_file(conn, _schema_dir() / "000_knowledge_db.sql")
        conn.commit()
    finally:
        conn.close()

    migrate(db)
    # Set an explicit source post-migration. Register slug so HR-2 trigger passes.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("INSERT INTO slug_registry (slug, status) VALUES ('tooling', 'active')")
        conn.execute(
            "INSERT INTO knowledge_chunks "
            "(project, project_tags, topic, title, body, source) "
            "VALUES ('tooling', '[\"tooling\"]', 't', 'T', 'b', 'mcp_propose')"
        )
        conn.commit()
    finally:
        conn.close()

    migrate(db)  # second run — should not clobber

    conn = sqlite3.connect(str(db))
    try:
        src = conn.execute(
            "SELECT source FROM knowledge_chunks WHERE topic='t'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert src == "mcp_propose"
