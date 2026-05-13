"""E3 — six-state maturation curve, suggestion engine + approval contract."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from runaway_context import maturation
from runaway_context.errors import MaturationApprovalRequired
from runaway_context.maturation import MaturationEngine, STATES, apply_transition

pytestmark = pytest.mark.feature


def test_e03_states_definition():
    """E3: the canonical curve has six states in the expected order."""
    assert STATES == (
        "scar", "active", "stable", "internalized", "superseded", "archived",
    )


def test_e03_engine_writes_only_suggested(seeded_client):
    """E3: MaturationEngine writes suggested_maturity, not maturity."""
    db_path = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db_path))
    try:
        old = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE lessons_learned SET created_at = ?, maturity = 'scar' "
            "WHERE id IN (SELECT id FROM lessons_learned LIMIT 1)",
            (old,),
        )
        conn.commit()
    finally:
        conn.close()

    engine = MaturationEngine(db_path)
    suggestions = engine.suggest_transitions()
    assert any(s["to_state"] == "active" for s in suggestions)


def test_e03_apply_transition_writes_audit(seeded_client):
    """E3: apply_transition mutates maturity AND appends to audit_log."""
    db_path = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db_path))
    try:
        lid = conn.execute(
            "SELECT id FROM lessons_learned ORDER BY id LIMIT 1"
        ).fetchone()[0]
        audit_before = conn.execute(
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()[0]
        apply_transition(conn, lesson_id=lid, to_state="stable", actor="tester")
        row = conn.execute(
            "SELECT maturity FROM lessons_learned WHERE id = ?", (lid,)
        ).fetchone()
        assert row[0] == "stable"
        audit_after = conn.execute(
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()[0]
        assert audit_after > audit_before
    finally:
        conn.close()


def test_e03_apply_transition_refuses_empty_actor(seeded_client):
    """E3: HR-9 refusal — empty actor raises MaturationApprovalRequired."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        with pytest.raises(MaturationApprovalRequired):
            apply_transition(conn, lesson_id=1, to_state="stable", actor="")
    finally:
        conn.close()
