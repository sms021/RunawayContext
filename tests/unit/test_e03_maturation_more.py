"""E3 — maturation — more branches for suggest_transitions and apply_transition."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from runaway_context import maturation
from runaway_context.maturation import (
    MaturationEngine,
    apply_transition,
    _parse_dt,
    _count_revisions,
)

pytestmark = pytest.mark.feature


def _back_date(db_path: Path, lesson_id: int, days: int) -> None:
    when = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE lessons_learned SET created_at = ? WHERE id = ?",
                     (when, lesson_id))
        conn.commit()
    finally:
        conn.close()


def test_parse_dt_variants():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("not a date") is None
    assert isinstance(_parse_dt("2024-01-02 03:04:05"), datetime)
    assert isinstance(_parse_dt("2024-01-02T03:04:05"), datetime)
    assert isinstance(_parse_dt("2024-01-02"), datetime)
    now = datetime.utcnow()
    assert _parse_dt(now) is now


def test_count_revisions_zero(seeded_client):
    """count_revisions returns 0 when no record_versions exist."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        n = _count_revisions(conn, 99999)
    finally:
        conn.close()
    assert n == 0


def test_count_revisions_nonzero(seeded_client):
    """count_revisions counts existing record_versions for the lesson."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        lid = conn.execute("SELECT id FROM lessons_learned LIMIT 1").fetchone()[0]
        # Insert versions
        for i in range(3):
            conn.execute(
                "INSERT INTO record_versions "
                "(table_name, record_id, version, payload, saved_by, reason) "
                "VALUES ('lessons_learned', ?, ?, '{}', 'tester', 'manual')",
                (lid, i + 1),
            )
        conn.commit()
        n = _count_revisions(conn, lid)
    finally:
        conn.close()
    assert n == 3


def test_suggest_active_to_stable_fires(seeded_client):
    """A 90-day-old active lesson with revisions → suggests stable."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        lid = conn.execute("SELECT id FROM lessons_learned LIMIT 1").fetchone()[0]
        old = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE lessons_learned SET maturity = 'active', created_at = ? "
            "WHERE id = ?",
            (old, lid),
        )
        # Add revisions to satisfy the re-engagement criterion
        for i in range(4):
            conn.execute(
                "INSERT INTO record_versions "
                "(table_name, record_id, version, payload, saved_by, reason) "
                "VALUES ('lessons_learned', ?, ?, '{}', 'tester', 'r')",
                (lid, i + 1),
            )
        conn.commit()
    finally:
        conn.close()
    engine = MaturationEngine(db)
    suggestions = engine.suggest_transitions()
    assert any(s["to_state"] == "stable" and s["lesson_id"] == lid
               for s in suggestions)


def test_suggest_active_to_stable_requires_evidence(seeded_client):
    """An old active lesson without re-engagement evidence does NOT fire."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        lid = conn.execute("SELECT id FROM lessons_learned LIMIT 1").fetchone()[0]
        old = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE lessons_learned SET maturity = 'active', created_at = ?, "
            "last_revisited = NULL WHERE id = ?",
            (old, lid),
        )
        # No revisions inserted.
        # Clear any pre-existing rows that may have been created during seeding.
        conn.execute(
            "DELETE FROM record_versions WHERE table_name='lessons_learned' "
            "AND record_id = ?",
            (lid,),
        )
        conn.commit()
    finally:
        conn.close()
    engine = MaturationEngine(db)
    suggestions = engine.suggest_transitions()
    assert not any(s["to_state"] == "stable" and s["lesson_id"] == lid
                   for s in suggestions)


def test_suggest_stable_to_internalized(seeded_client):
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        lid = conn.execute("SELECT id FROM lessons_learned LIMIT 1").fetchone()[0]
        old = (datetime.utcnow() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE lessons_learned SET maturity = 'stable', created_at = ? "
            "WHERE id = ?",
            (old, lid),
        )
        conn.commit()
    finally:
        conn.close()
    engine = MaturationEngine(db)
    suggestions = engine.suggest_transitions()
    assert any(s["to_state"] == "internalized" and s["lesson_id"] == lid
               for s in suggestions)


def test_suggest_superseded_fires(seeded_client):
    """superseded_by set → suggest_transitions emits 'superseded'."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT id FROM lessons_learned ORDER BY id LIMIT 2"
        ).fetchall()
        a, b = int(rows[0][0]), int(rows[1][0])
        conn.execute(
            "UPDATE lessons_learned SET superseded_by = ?, maturity = 'active' "
            "WHERE id = ?",
            (b, a),
        )
        conn.commit()
    finally:
        conn.close()
    engine = MaturationEngine(db)
    suggestions = engine.suggest_transitions()
    assert any(s["to_state"] == "superseded" and s["lesson_id"] == a
               for s in suggestions)


def test_suggest_transitions_dry_run_no_write(seeded_client):
    """dry_run=True doesn't persist suggested_maturity."""
    db = seeded_client._knowledge_db
    _back_date(db, 1, 30)
    engine = MaturationEngine(db)
    engine.suggest_transitions(dry_run=True)
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT suggested_maturity FROM lessons_learned WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None


def test_apply_transition_invalid_state_raises(seeded_client):
    """apply_transition rejects an unknown maturity state."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        with pytest.raises(ValueError):
            apply_transition(conn, lesson_id=1, to_state="nonsense", actor="me")
    finally:
        conn.close()


def test_apply_transition_unknown_lesson_raises(seeded_client):
    """apply_transition raises ValueError on missing lesson row."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        with pytest.raises(ValueError):
            apply_transition(conn, lesson_id=999999, to_state="stable", actor="me")
    finally:
        conn.close()
