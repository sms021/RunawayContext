"""E2 — soft-delete + record versioning."""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.feature


def test_e02_soft_delete_marks_row(seeded_client):
    """E2: soft_delete marks deleted_at + writes record_versions snapshot."""
    lessons = seeded_client.list_lessons(project="tooling")
    target = lessons[0]["id"]
    seeded_client.soft_delete(
        table="lessons_learned",
        record_id=target,
        actor="tester",
        reason="E2 spec",
    )
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT deleted_at, deleted_by, deletion_reason "
            "FROM lessons_learned WHERE id = ?",
            (target,),
        ).fetchone()
        assert row[0] is not None
        assert row[1] == "tester"
        assert row[2] == "E2 spec"
        snap = conn.execute(
            "SELECT COUNT(*) FROM record_versions WHERE record_id = ?",
            (target,),
        ).fetchone()
        assert snap[0] >= 1
    finally:
        conn.close()


def test_e02_active_view_filters_soft_deleted(seeded_client):
    """E2: ``lessons_learned_active`` view excludes soft-deleted rows."""
    lessons = seeded_client.list_lessons(project="tooling")
    seeded_client.soft_delete(
        table="lessons_learned",
        record_id=lessons[0]["id"],
        actor="t",
        reason="hide",
    )
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        active = conn.execute(
            "SELECT COUNT(*) FROM lessons_learned_active"
        ).fetchone()[0]
        all_rows = conn.execute(
            "SELECT COUNT(*) FROM lessons_learned"
        ).fetchone()[0]
    finally:
        conn.close()
    assert active < all_rows


def test_e02_soft_delete_rejects_unknown_table(seeded_client):
    """E2: soft_delete refuses tables outside the allowlist."""
    with pytest.raises(ValueError):
        seeded_client.soft_delete(
            table="audit_log", record_id=1, actor="t", reason="nope"
        )
