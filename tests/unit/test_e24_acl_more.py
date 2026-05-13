"""E24 — ACL — extra branches."""
from __future__ import annotations

import sqlite3
import types

import pytest

from runaway_context import acl

pytestmark = pytest.mark.feature


def test_current_visibility_level_non_string_tier():
    """Non-string tier defaults to private (HR-1 closed-by-default)."""
    cfg = types.SimpleNamespace(tier=None)
    assert acl.current_visibility_level(cfg) == "private"
    cfg = types.SimpleNamespace(tier=5)
    assert acl.current_visibility_level(cfg) == "private"


def test_current_visibility_level_t5_returns_org():
    cfg = types.SimpleNamespace(tier="T5")
    assert acl.current_visibility_level(cfg) == "org"


def test_visibility_filter_requires_config():
    with pytest.raises(ValueError):
        acl.VisibilityFilter(None)


def test_visibility_filter_allowed_method():
    f = acl.VisibilityFilter(types.SimpleNamespace(tier="T4"))
    assert "org" in f.allowed()


def test_filter_rows_skips_non_dict():
    """Non-dict entries get dropped silently."""
    rows = [{"id": 1, "visibility": "private"}, "not a dict", None]
    out = acl.filter_rows(rows, allowed=["private"])
    assert len(out) == 1


def test_allowed_for_max_unknown_fallback():
    assert acl._allowed_for_max("unknown") == ["private"]


def test_set_visibility_invalid_table(seeded_client):
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            acl.set_visibility(conn, "bad_table", 1, "private", actor="me")
    finally:
        conn.close()


def test_set_visibility_empty_level(seeded_client):
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            acl.set_visibility(conn, "lessons_learned", 1, "", actor="me")
    finally:
        conn.close()


def test_set_visibility_missing_row(seeded_client):
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(LookupError):
            acl.set_visibility(conn, "lessons_learned", 999999, "team",
                               actor="me")
    finally:
        conn.close()


def test_level_is_valid_missing_table(tmp_path):
    """_level_is_valid returns False when the visibility_levels table is absent."""
    db = tmp_path / "x.db"
    conn = sqlite3.connect(str(db))
    try:
        # No tables exist at all
        assert acl._level_is_valid(conn, "private") is False
    finally:
        conn.close()
