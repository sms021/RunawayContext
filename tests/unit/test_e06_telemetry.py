"""E6 — telemetry (emit + flush + housekeeping + aggregate)."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context import metrics

pytestmark = pytest.mark.feature


def test_e06_emit_persists_after_flush(tmp_path, monkeypatch):
    """E6: emit() + flush() writes rows to metrics.db."""
    metrics_db = tmp_path / "metrics.db"
    monkeypatch.setenv("RC_METRICS_DB", str(metrics_db))
    metrics.configure(metrics_db)
    metrics.emit("test", "first", value_num=1.0, labels={"k": "v"})
    metrics.emit("test", "second")
    metrics.flush(timeout=2.0)

    conn = sqlite3.connect(str(metrics_db))
    try:
        rows = conn.execute(
            "SELECT kind, name FROM metric_events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    names = {r[1] for r in rows}
    assert {"first", "second"}.issubset(names)


def test_e06_housekeeping_returns_int(tmp_path, monkeypatch):
    """E6: housekeeping returns a row count (zero on empty DB)."""
    metrics_db = tmp_path / "metrics.db"
    monkeypatch.setenv("RC_METRICS_DB", str(metrics_db))
    metrics.configure(metrics_db)
    metrics.install_schema_if_missing(metrics_db)
    deleted = metrics.housekeeping(retention_days=0)
    assert isinstance(deleted, int)


def test_e06_aggregate_writes_buckets(tmp_path, monkeypatch):
    """E6: aggregate() produces (date, kind, name) buckets in metric_aggregates."""
    from datetime import date
    metrics_db = tmp_path / "metrics.db"
    monkeypatch.setenv("RC_METRICS_DB", str(metrics_db))
    metrics.configure(metrics_db)
    metrics.emit("agg", "alpha", value_num=2.0)
    metrics.flush(timeout=2.0)
    today = date.today().isoformat()
    written = metrics.aggregate(today)
    assert written >= 1


def test_e06_aggregate_bucket_uses_local_date_not_utc(tmp_path, monkeypatch):
    """E6: a metric emitted 'now' is bucketed under today's LOCAL civil date.

    Regression: when occurred_at (UTC) was compared via plain DATE(),
    callers west of UTC saw their late-evening metrics roll up under
    tomorrow's date and aggregate(date.today()) returned 0 buckets.
    """
    import sqlite3
    from datetime import date
    metrics_db = tmp_path / "metrics.db"
    monkeypatch.setenv("RC_METRICS_DB", str(metrics_db))
    metrics.configure(metrics_db)
    metrics.emit("tz", "beta", value_num=1.0)
    metrics.flush(timeout=2.0)
    written = metrics.aggregate(date.today().isoformat())
    assert written >= 1
    conn = sqlite3.connect(str(metrics_db))
    try:
        rows = conn.execute(
            "SELECT bucket, kind, name FROM metric_aggregates WHERE kind='tz'"
        ).fetchall()
    finally:
        conn.close()
    assert any(r[0] == date.today().isoformat() for r in rows), rows
