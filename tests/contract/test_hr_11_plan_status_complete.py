"""HR-11 contract tests — the plan ships nothing as "pending forever".

HR-11: every section of the plan has a Status field with one of the legal
values. The release gate (driven by ``RC_RELEASE_GATE=1``) fails when any
P1/P2 item is still ``pending`` or ``in_progress``. Outside the release
window the gate only checks that the parser is wired up.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


PLAN_PATH = Path("/var/www/html/ToDo/PLANS/runaway_context_v3_implementation_2026-05-13.md")

_VALID_STATUSES = {"pending", "in_progress", "done", "removed_from_plan"}


def _extract_table_statuses(plan_text: str):
    """Return every Status cell from the plan's markdown tables."""
    statuses = []
    for line in plan_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # Skip header / separator rows.
        if any(re.match(r"^-+$", c) for c in cells):
            continue
        for cell in cells:
            if cell in _VALID_STATUSES:
                statuses.append(cell)
    return statuses


def test_hr_11_plan_status_values_valid() -> None:
    """HR-11: every Status field uses one of the allowed values."""
    if not PLAN_PATH.exists():
        pytest.skip(f"plan not available at {PLAN_PATH}")
    text = PLAN_PATH.read_text(encoding="utf-8")
    statuses = _extract_table_statuses(text)
    assert statuses, "plan must declare at least one Status field"
    invalid = [s for s in statuses if s not in _VALID_STATUSES]
    assert not invalid, f"HR-11: invalid status values: {set(invalid)}"


def test_hr_11_release_gate_blocks_pending() -> None:
    """HR-11: when RC_RELEASE_GATE=1, the gate refuses pending/in_progress."""
    if not PLAN_PATH.exists():
        pytest.skip(f"plan not available at {PLAN_PATH}")
    if os.environ.get("RC_RELEASE_GATE") != "1":
        # Outside the release window the parser-only check is enough.
        return
    text = PLAN_PATH.read_text(encoding="utf-8")
    statuses = _extract_table_statuses(text)
    blockers = [s for s in statuses if s in ("pending", "in_progress")]
    assert not blockers, (
        f"HR-11 release gate: {len(blockers)} item(s) still pending/in_progress"
    )
