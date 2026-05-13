"""E24 — visibility ACL filter (T4 unlock)."""
from __future__ import annotations

import sqlite3
import types

import pytest

from runaway_context import acl

pytestmark = pytest.mark.feature


def _cfg(tier: str):
    return types.SimpleNamespace(tier=tier)


def test_e24_current_visibility_level_per_tier():
    """E24: tier resolves to its max visibility level."""
    assert acl.current_visibility_level(_cfg("T1")) == "private"
    assert acl.current_visibility_level(_cfg("T2")) == "private"
    assert acl.current_visibility_level(_cfg("T3")) == "team"
    assert acl.current_visibility_level(_cfg("T4")) == "org"


def test_e24_filter_rows_drops_higher_levels():
    """E24: filter_rows hides rows above the current ceiling."""
    rows = [
        {"id": 1, "visibility": "private"},
        {"id": 2, "visibility": "team"},
        {"id": 3, "visibility": "org"},
    ]
    out = acl.filter_rows(rows, allowed=["private"])
    assert [r["id"] for r in out] == [1]
    out2 = acl.filter_rows(rows, allowed=["private", "team"])
    assert [r["id"] for r in out2] == [1, 2]


def test_e24_filter_rows_treats_missing_visibility_as_private():
    """E24: rows with no visibility field are treated as private (closed by default)."""
    rows = [{"id": 1}, {"id": 2, "visibility": "team"}]
    out = acl.filter_rows(rows, allowed=["private"])
    assert [r["id"] for r in out] == [1]


def test_e24_visibility_filter_class():
    """E24: VisibilityFilter wraps the Config-aware filter."""
    f = acl.VisibilityFilter(_cfg("T3"))
    rows = [
        {"id": 1, "visibility": "private"},
        {"id": 2, "visibility": "org"},
    ]
    out = f.filter(rows)
    # T3 sees private + team, but not org
    assert {r["id"] for r in out} == {1}


def test_e24_set_visibility_writes_audit(seeded_client):
    """E24: set_visibility updates ACL and writes an audit row."""
    lessons = seeded_client.list_lessons(project="tooling")
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        acl.set_visibility(
            conn, "lessons_learned", lessons[0]["id"], "team",
            actor="tester",
        )
        after = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert after > before
    finally:
        conn.close()


def test_e24_set_visibility_rejects_unknown_level(seeded_client):
    """E24: unknown visibility level is refused before the SQL trigger fires."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            acl.set_visibility(
                conn, "lessons_learned", 1, "nonsense",
                actor="tester",
            )
    finally:
        conn.close()
