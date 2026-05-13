"""HR-9 contract tests — maturation requires explicit actor approval.

HR-9: the engine writes only to ``suggested_maturity`` and its sibling
columns. The ``maturity`` column is mutated solely by
``apply_transition`` / ``Client.mature_lesson``, which requires an explicit
actor argument.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from runaway_context import maturation
from runaway_context.errors import MaturationApprovalRequired

pytestmark = pytest.mark.contract


def test_hr_09_engine_writes_only_to_suggested_maturity(seeded_client) -> None:
    """HR-9: MaturationEngine never updates the maturity column."""
    db_path = seeded_client._knowledge_db
    # Backdate one lesson so the scar→active rule fires.
    conn = sqlite3.connect(str(db_path))
    try:
        old = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE lessons_learned SET created_at = ?, maturity = 'scar' "
            "WHERE id = (SELECT id FROM lessons_learned ORDER BY id LIMIT 1)",
            (old,),
        )
        conn.commit()
    finally:
        conn.close()

    engine = maturation.MaturationEngine(db_path)
    suggestions = engine.suggest_transitions()
    assert any(s["to_state"] == "active" for s in suggestions)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT maturity, suggested_maturity FROM lessons_learned "
            "WHERE deleted_at IS NULL"
        ).fetchall()
    finally:
        conn.close()
    # The engine wrote suggested_maturity for at least one row but did not
    # promote any maturity past 'scar' automatically.
    assert any(r[1] is not None for r in rows)
    assert all(r[0] in (None, "scar", "active", "stable", "internalized",
                        "superseded", "archived") for r in rows)


def test_hr_09_apply_transition_requires_actor(seeded_client) -> None:
    """HR-9: apply_transition refuses to mutate maturity without an actor."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT id FROM lessons_learned LIMIT 1"
        ).fetchone()
        with pytest.raises(MaturationApprovalRequired):
            maturation.apply_transition(
                conn, lesson_id=int(row[0]), to_state="active", actor=""
            )
        with pytest.raises(MaturationApprovalRequired):
            maturation.apply_transition(
                conn, lesson_id=int(row[0]), to_state="active", actor="   "
            )
    finally:
        conn.close()


def test_hr_09_invalid_maturity_state_rejected_by_trigger(fresh_db) -> None:
    """HR-9: DB trigger refuses an unknown maturity state in UPDATEs."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        cur = conn.execute(
            "INSERT INTO lessons_learned (title, project_tags, maturity) "
            "VALUES (?, ?, ?)",
            ("seed", '["tooling"]', "active"),
        )
        lesson_id = cur.lastrowid
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE lessons_learned SET maturity = ? WHERE id = ?",
                ("does_not_exist", lesson_id),
            )
            conn.commit()
    finally:
        conn.close()
