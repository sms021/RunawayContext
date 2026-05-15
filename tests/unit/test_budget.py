"""Unit tests for the daily token budget ledger."""
from __future__ import annotations

from pathlib import Path

import pytest

from runaway_context import budget


pytestmark = pytest.mark.feature


def test_fresh_ledger_is_empty(tmp_path: Path):
    state = budget.get_state(tmp_path, cap=1000)
    assert state.total_used == 0
    assert state.remaining == 1000


def test_reserve_then_record_advances_counters(tmp_path: Path):
    assert budget.check_and_reserve(tmp_path, 100, cap=1000) is True
    state = budget.get_state(tmp_path, cap=1000)
    assert state.reserved == 100
    budget.record_usage(tmp_path, estimated_tokens=100, actual_input=80, actual_output=20, cap=1000)
    state = budget.get_state(tmp_path, cap=1000)
    assert state.reserved == 0
    assert state.used_input == 80
    assert state.used_output == 20
    assert state.summaries_completed == 1


def test_reserve_refuses_above_cap(tmp_path: Path):
    assert budget.check_and_reserve(tmp_path, 500, cap=1000) is True
    # Second reservation would put us at 500+600=1100 > 1000 — refuses
    assert budget.check_and_reserve(tmp_path, 600, cap=1000) is False
    state = budget.get_state(tmp_path, cap=1000)
    assert state.summaries_skipped_over_budget == 1


def test_record_failure_releases_reservation(tmp_path: Path):
    budget.check_and_reserve(tmp_path, 100, cap=1000)
    budget.record_failure(tmp_path, cap=1000)
    state = budget.get_state(tmp_path, cap=1000)
    assert state.reserved < 100


def test_malformed_ledger_recovers_as_fresh(tmp_path: Path):
    """A corrupted ledger file is treated as fresh, not raised on."""
    path = tmp_path / "budget" / "2026-05-15.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not valid json", encoding="utf-8")
    # get_state must not raise; counters reset to zero
    state = budget.get_state(tmp_path, cap=999)
    assert state.cap == 999
    assert state.total_used == 0
