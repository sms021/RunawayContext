"""E4 — three-axis severity (blast_radius / frequency / reversibility)."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.severity import (
    SEVERITY_LEVELS, axis_summary, derive_severity, validate_axes,
)

pytestmark = pytest.mark.feature


def test_e04_severity_levels_canonical():
    """E4: SEVERITY_LEVELS exposes the canonical three buckets."""
    assert SEVERITY_LEVELS == ("critical", "warning", "info")


def test_e04_validate_axes_accepts_in_range():
    """E4: 1..5 axes pass validation."""
    validate_axes(blast_radius=1, frequency=3, reversibility=5)


def test_e04_validate_axes_rejects_out_of_range():
    """E4: out-of-range axes raise ValueError."""
    with pytest.raises(ValueError):
        validate_axes(blast_radius=6)
    with pytest.raises(ValueError):
        validate_axes(frequency=0)


def test_e04_validate_axes_rejects_booleans():
    """E4: boolean rejected because True coerces to 1 silently."""
    with pytest.raises(ValueError):
        validate_axes(blast_radius=True)


def test_e04_derive_severity_edge_cases():
    """E4: derive_severity returns correct buckets for boundary axes."""
    assert derive_severity(None, None, None) == "warning"
    assert derive_severity(4, 1, 1) == "critical"
    assert derive_severity(1, 1, 4) == "critical"
    assert derive_severity(2, 1, 1) == "warning"
    assert derive_severity(1, 3, 1) == "warning"
    assert derive_severity(1, 1, 1) == "info"


def test_e04_axis_summary_one_liner():
    """E4: axis_summary formats axes for logs."""
    assert "blast=4" in axis_summary(4, 3, 2)
    assert "blast=-" in axis_summary(None, 3, None)


def test_e04_db_trigger_rejects_out_of_range(fresh_db):
    """E4: DB trigger refuses axis values outside 1..5."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lessons_learned (title, project_tags, blast_radius) "
                "VALUES (?, ?, ?)",
                ("bad axis", '["tooling"]', 99),
            )
    finally:
        conn.close()
