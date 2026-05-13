"""HR-8 contract tests — emit() never blocks, never raises.

HR-8: telemetry is fire-and-forget. Even with a bad DB path / locked DB /
missing directory the call returns immediately and never propagates.
"""
from __future__ import annotations

import time

import pytest

from runaway_context import metrics

pytestmark = pytest.mark.contract


def test_hr_08_emit_never_raises(tmp_path, monkeypatch) -> None:
    """HR-8: emit() never propagates exceptions from broken inputs."""
    # Point metrics at a path that can never be written
    bad_path = tmp_path / "no_such_dir_xyz_abc" / "metrics.db"
    monkeypatch.setenv("RC_METRICS_DB", str(bad_path))
    metrics.configure(bad_path)
    # Every variant: missing values, bad types, locked-shaped paths.
    try:
        metrics.emit("kind", "name")
        metrics.emit("kind", "name", value_num=1.0)
        metrics.emit("kind", "name", value_text="ok")
        metrics.emit("kind", "name", labels={"bad": object()})  # non-JSON value
        metrics.emit(None, None)  # bad inputs
    except Exception as exc:  # noqa: BLE001 -- this is the HR-8 assertion
        pytest.fail(f"emit raised: {type(exc).__name__}: {exc}")


def test_hr_08_emit_never_blocks(tmp_path, monkeypatch) -> None:
    """HR-8: emit() returns in well under 500ms across 10,000 calls."""
    metrics_db = tmp_path / "metrics.db"
    monkeypatch.setenv("RC_METRICS_DB", str(metrics_db))
    metrics.configure(metrics_db)
    start = time.monotonic()
    for i in range(10_000):
        metrics.emit("k", "n", value_num=float(i))
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"emit blocked for {elapsed:.3f}s on 10k calls"
