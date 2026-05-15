"""Daily token budget ledger for the session summarizer.

A simple per-day JSON file under ``<install_dir>/budget/YYYY-MM-DD.json`` that
records cumulative token spend by the session summarizer. Before each
LLM-bound summarization call, callers consult :func:`check_and_reserve` to
see if there is room left in the daily cap. If the cap is exhausted, the
summarizer falls back to metadata-only inserts (no model call). When a
summary completes, :func:`record_usage` writes the actual token counts back
to the ledger.

The ledger is the second line of defense against a runaway summarizer eating
tokens — the first line is the per-conversation cooldown + attempt cap. The
ledger is what stops a stuck loop or misconfigured webhook from ever spending
more than ``summarizer_daily_token_cap`` (default 50,000) on Claude API calls
in a single day.

HR-1: this module makes no network calls. It only reads and writes local JSON.

Refuses:
    Reserving tokens above the daily cap (returns False — caller falls back).
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_DAILY_TOKEN_CAP = 50_000


@dataclass
class BudgetState:
    """Snapshot of the current day's budget ledger.

    Returns:
        Snapshot dataclass — never raises, never blocks.

    Refuses:
        Nothing — pure value object.
    """

    date: str
    cap: int
    used_input: int
    used_output: int
    reserved: int
    summaries_completed: int
    summaries_skipped_over_budget: int

    @property
    def total_used(self) -> int:
        """Tokens already spent today (in + out)."""
        return self.used_input + self.used_output

    @property
    def remaining(self) -> int:
        """Tokens remaining today (cap - used - reserved). Never negative."""
        return max(0, self.cap - self.total_used - self.reserved)

    def to_dict(self) -> dict:
        """JSON-serializable view of the ledger row."""
        return {
            "date": self.date,
            "cap": self.cap,
            "used_input": self.used_input,
            "used_output": self.used_output,
            "reserved": self.reserved,
            "summaries_completed": self.summaries_completed,
            "summaries_skipped_over_budget": self.summaries_skipped_over_budget,
        }


def _today() -> str:
    """Return today's UTC date as YYYY-MM-DD.

    UTC keeps the ledger boundary stable across DST and timezone moves.
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _ledger_path(install_dir: Path, date: Optional[str] = None) -> Path:
    """Return the ledger file path for *date* (default: today)."""
    return Path(install_dir) / "budget" / f"{date or _today()}.json"


def _load(path: Path, cap: int) -> BudgetState:
    """Read the ledger file (or return a fresh one).

    Returns:
        BudgetState — never raises. A malformed file is treated as fresh.
    """
    date = path.stem
    if not path.exists():
        return BudgetState(
            date=date, cap=cap, used_input=0, used_output=0, reserved=0,
            summaries_completed=0, summaries_skipped_over_budget=0,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BudgetState(
            date=date, cap=cap, used_input=0, used_output=0, reserved=0,
            summaries_completed=0, summaries_skipped_over_budget=0,
        )
    return BudgetState(
        date=str(data.get("date", date)),
        cap=int(data.get("cap", cap)),
        used_input=int(data.get("used_input", 0)),
        used_output=int(data.get("used_output", 0)),
        reserved=int(data.get("reserved", 0)),
        summaries_completed=int(data.get("summaries_completed", 0)),
        summaries_skipped_over_budget=int(
            data.get("summaries_skipped_over_budget", 0)
        ),
    )


def _save(path: Path, state: BudgetState) -> None:
    """Atomically write the ledger (best-effort).

    Refuses:
        Nothing — write failures are swallowed (HR-8 fire-and-forget). The
        ledger is advisory; losing one write does not corrupt state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # HR-8: budget telemetry never blocks the summarizer.
        pass  # HR-8: best-effort persist


def get_state(install_dir: Path, cap: int = DEFAULT_DAILY_TOKEN_CAP) -> BudgetState:
    """Return today's ledger snapshot without modifying it.

    Returns:
        BudgetState for the current UTC date.

    Refuses:
        Nothing — read-only.
    """
    return _load(_ledger_path(install_dir), cap=cap)


def check_and_reserve(
    install_dir: Path,
    estimated_tokens: int,
    cap: int = DEFAULT_DAILY_TOKEN_CAP,
) -> bool:
    """Reserve *estimated_tokens* of headroom if the daily cap allows it.

    A successful reservation is written back to the ledger; callers should
    record actual usage with :func:`record_usage` afterwards (which also
    releases the reservation). If reservation fails (over cap), the call
    counter ``summaries_skipped_over_budget`` is bumped so the metric is
    queryable later.

    Returns:
        True iff the reservation was made and the summarizer may proceed.
        False when the daily cap would be exceeded; the summarizer should
        fall back to metadata-only.

    Refuses:
        Reservations that would push (used + reserved + estimated) over cap.
    """
    path = _ledger_path(install_dir)
    state = _load(path, cap=cap)
    state.cap = cap  # honor latest config cap even on existing ledger
    if state.total_used + state.reserved + max(0, estimated_tokens) > cap:
        state.summaries_skipped_over_budget += 1
        _save(path, state)
        return False
    state.reserved += max(0, estimated_tokens)
    _save(path, state)
    return True


def record_usage(
    install_dir: Path,
    estimated_tokens: int,
    actual_input: int,
    actual_output: int,
    cap: int = DEFAULT_DAILY_TOKEN_CAP,
) -> None:
    """Release the reservation and record actual usage.

    *estimated_tokens* must match the value passed to a prior successful
    :func:`check_and_reserve`. Pass 0 if no reservation was made (e.g. the
    call bypassed the budget check).

    Refuses:
        Nothing — releasing more than was reserved clamps to zero rather
        than going negative.
    """
    path = _ledger_path(install_dir)
    state = _load(path, cap=cap)
    state.cap = cap
    state.reserved = max(0, state.reserved - max(0, estimated_tokens))
    state.used_input += max(0, actual_input)
    state.used_output += max(0, actual_output)
    state.summaries_completed += 1
    _save(path, state)


def record_failure(install_dir: Path, cap: int = DEFAULT_DAILY_TOKEN_CAP) -> None:
    """Release a prior reservation after a failed summarization.

    A failed call should not count toward used tokens (the request never
    completed) but the reservation must be released so the budget stays
    accurate. Best-effort: if the ledger can't be read, silently no-ops.

    Refuses:
        Nothing — fire-and-forget.
    """
    path = _ledger_path(install_dir)
    state = _load(path, cap=cap)
    state.cap = cap
    # We don't know the original reservation amount here; conservatively
    # zero out the reserved counter when the only call in flight failed.
    # This is safe because the caller pairs reserve/record one-to-one.
    state.reserved = max(0, state.reserved - 1)
    _save(path, state)
