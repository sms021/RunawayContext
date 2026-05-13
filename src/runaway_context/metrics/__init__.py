"""Telemetry emission (E6).

A fire-and-forget metrics writer that records events to ``metrics.db``.

Design contracts:

* **HR-1** — stdlib only. No network imports. The OTLP exporter lives in a
  separate, opt-in module (`runaway_context.metrics.otlp_exporter`, spec at
  ``docs/specs/OTLP_EXPORT.md``).
* **HR-8** — ``emit()`` never raises, never blocks. All bodies are wrapped in
  a broad ``try/except`` that drops on failure. The internal queue is bounded
  (``maxsize=10000``) and uses ``put_nowait``; if it's full, the event is
  dropped silently. The writer is a background daemon thread that drains the
  queue.

Public surface:
    emit, flush, housekeeping, aggregate, configure, install_schema_if_missing

Refuses:
    Nothing — this module is best-effort. Failures degrade silently.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

__all__ = [
    "emit",
    "flush",
    "housekeeping",
    "aggregate",
    "configure",
    "install_schema_if_missing",
]

_LOGGER = logging.getLogger(__name__)

# Maximum queued events before drop-on-full kicks in
_MAX_QUEUE = 10000

# Suppress repeated debug logs from telemetry failures; sample by N
_LOG_SAMPLE_EVERY = 500


def _default_metrics_db() -> Path:
    """Return the default ``metrics.db`` location.

    Resolution order:

    1. ``$RC_METRICS_DB`` environment variable.
    2. ``~/_knowledge/metrics.db``.

    Returns:
        Path to the metrics database (may not yet exist).

    Refuses:
        Nothing — pure resolution helper.
    """
    env = os.environ.get("RC_METRICS_DB")
    if env:
        return Path(env)
    return Path.home() / "_knowledge" / "metrics.db"


_SCHEMA_FILE_NAME = "004_metrics_db.sql"


def _schema_path() -> Path:
    """Locate the metrics schema file shipped with the package.

    Returns:
        Path to ``schema/004_metrics_db.sql`` inside the source tree.

    Refuses:
        Nothing.
    """
    # src/runaway_context/metrics/__init__.py -> repo/src/runaway_context/metrics
    here = Path(__file__).resolve().parent
    # Walk up to find the ``schema`` sibling of ``src``.
    for parent in [here, *here.parents]:
        candidate = parent / "schema" / _SCHEMA_FILE_NAME
        if candidate.exists():
            return candidate
    # Fall back to the path layout used by the v3 reference repo.
    return here.parent.parent.parent / "schema" / _SCHEMA_FILE_NAME


def install_schema_if_missing(metrics_db: Path) -> None:
    """Apply the metrics schema to ``metrics_db`` if its tables don't yet exist.

    This opens ``metrics_db`` directly (without touching ``knowledge.db``) and
    runs ``004_metrics_db.sql`` once. Safe to call repeatedly because every
    statement is ``IF NOT EXISTS``.

    Returns:
        None.

    Refuses:
        Nothing — caller is the writer thread which wraps in try/except.
    """
    metrics_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(metrics_db))
    try:
        # Cheap idempotency probe — skip work if the table is already there.
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metric_events'"
        )
        if cur.fetchone() is not None:
            return
        sql_path = _schema_path()
        if not sql_path.exists():
            # Fallback minimal schema if shipped SQL is missing.
            conn.executescript(_FALLBACK_SCHEMA)
        else:
            conn.executescript(sql_path.read_text())
        conn.commit()
    finally:
        conn.close()


_FALLBACK_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS metric_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    labels TEXT,
    install_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_metric_time ON metric_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_metric_kind ON metric_events(kind, name);
CREATE TABLE IF NOT EXISTS metric_aggregates (
    bucket DATE NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    sum_value REAL,
    PRIMARY KEY (bucket, kind, name)
);
"""


class _MetricsWriter:
    """Background queue + daemon thread that drains events to ``metrics.db``.

    One writer per configured DB path. Created lazily on first emit. The
    writer is a daemon thread; ``atexit`` flushes the queue on normal exit.

    Refuses:
        Nothing — every public method is fail-soft (HR-8).
    """

    def __init__(self, metrics_db: Path) -> None:
        self._metrics_db = metrics_db
        self._queue: queue.Queue = queue.Queue(maxsize=_MAX_QUEUE)
        self._shutdown = threading.Event()
        self._schema_installed = False
        self._schema_lock = threading.Lock()
        self._drop_counter = 0
        self._fail_counter = 0
        self._thread = threading.Thread(
            target=self._run,
            name="runaway-context-metrics-writer",
            daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------ enqueue

    def enqueue(self, event: dict) -> None:
        """Drop-on-full enqueue. Never raises.

        Returns:
            None.

        Refuses:
            Nothing.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # HR-8: drop silently. Sampled debug log.
            self._drop_counter += 1
            if self._drop_counter % _LOG_SAMPLE_EVERY == 0:
                _LOGGER.debug(
                    "metrics queue full, dropped %d events", self._drop_counter
                )

    # ------------------------------------------------------------------ flush

    def flush(self, timeout: float = 5.0) -> int:
        """Block until the queue drains (or timeout). Returns events flushed.

        Returns:
            Count of events actually committed to disk during this call.

        Refuses:
            Nothing — broad swallow on internal failure.
        """
        deadline = time.monotonic() + timeout
        start_unfinished = self._queue.unfinished_tasks
        try:
            while time.monotonic() < deadline:
                if self._queue.unfinished_tasks == 0:
                    break
                time.sleep(0.01)
            end_unfinished = self._queue.unfinished_tasks
            return max(0, start_unfinished - end_unfinished)
        except Exception:
            return 0

    # ------------------------------------------------------------------ shutdown

    def shutdown(self, timeout: float = 5.0) -> None:
        """Signal the writer thread to stop and wait briefly.

        Returns:
            None.

        Refuses:
            Nothing.
        """
        try:
            self._shutdown.set()
            try:
                self._queue.put_nowait(None)  # sentinel to wake the thread
            except queue.Full:
                pass
            self._thread.join(timeout=timeout)
        except Exception:
            return

    # ------------------------------------------------------------------ thread loop

    def _ensure_schema(self) -> bool:
        """Apply the metrics schema once per writer. Idempotent.

        Returns:
            True if the schema is available, False on failure.

        Refuses:
            Nothing.
        """
        if self._schema_installed:
            return True
        with self._schema_lock:
            if self._schema_installed:
                return True
            try:
                install_schema_if_missing(self._metrics_db)
                self._schema_installed = True
                return True
            except Exception:
                self._fail_counter += 1
                if self._fail_counter % _LOG_SAMPLE_EVERY == 1:
                    _LOGGER.debug("metrics schema install failed (sampled)")
                return False

    def _run(self) -> None:
        """Drain the queue, writing to SQLite. Never propagates exceptions."""
        while not self._shutdown.is_set() or not self._queue.empty():
            try:
                batch = []
                # Block briefly for the first event so we don't spin.
                try:
                    first = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if first is None:
                    # sentinel
                    self._queue.task_done()
                    continue
                batch.append(first)
                # Drain whatever else is immediately available, up to 200 at a time.
                while len(batch) < 200:
                    try:
                        nxt = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        self._queue.task_done()
                        continue
                    batch.append(nxt)

                self._write_batch(batch)
                for _ in batch:
                    self._queue.task_done()
            except Exception:
                # Any unexpected failure — drop the batch silently (HR-8).
                self._fail_counter += 1
                if self._fail_counter % _LOG_SAMPLE_EVERY == 1:
                    _LOGGER.debug("metrics writer loop swallowed an error (sampled)")

    def _write_batch(self, batch: list) -> None:
        """Persist a batch of events. Schema is lazy-installed.

        Refuses:
            Nothing.
        """
        if not batch:
            return
        if not self._ensure_schema():
            return
        conn = None
        try:
            conn = sqlite3.connect(str(self._metrics_db), timeout=2.0)
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT INTO metric_events "
                "(kind, name, value_num, value_text, labels, install_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.get("kind"),
                        e.get("name"),
                        e.get("value_num"),
                        e.get("value_text"),
                        e.get("labels"),
                        e.get("install_id"),
                    )
                    for e in batch
                ],
            )
            conn.commit()
        except Exception:
            self._fail_counter += 1
            if self._fail_counter % _LOG_SAMPLE_EVERY == 1:
                _LOGGER.debug("metrics write_batch failed (sampled)")
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# Module-level singleton state -------------------------------------------------

_writer_lock = threading.Lock()
_writer: Optional[_MetricsWriter] = None
_configured_db: Optional[Path] = None
_atexit_registered = False


def configure(metrics_db: Path) -> None:
    """Override the metrics DB location.

    Shuts down any existing writer and rebinds to the new path. Useful for
    tests that want an isolated DB per case.

    Returns:
        None.

    Refuses:
        Nothing — fail-soft. A path that cannot be opened simply means later
        ``emit()`` calls quietly drop.
    """
    global _writer, _configured_db
    with _writer_lock:
        if _writer is not None:
            try:
                _writer.shutdown(timeout=2.0)
            except Exception:
                pass
            _writer = None
        _configured_db = Path(metrics_db)


def _get_writer() -> Optional[_MetricsWriter]:
    """Lazy-create the singleton writer. Never raises."""
    global _writer, _atexit_registered
    if _writer is not None:
        return _writer
    with _writer_lock:
        if _writer is not None:
            return _writer
        try:
            db_path = _configured_db if _configured_db is not None else _default_metrics_db()
            _writer = _MetricsWriter(db_path)
            if not _atexit_registered:
                atexit.register(_atexit_flush)
                _atexit_registered = True
            return _writer
        except Exception:
            return None


def _atexit_flush() -> None:
    """Flush the queue on interpreter shutdown. Best-effort."""
    try:
        if _writer is not None:
            _writer.flush(timeout=2.0)
            _writer.shutdown(timeout=2.0)
    except Exception:
        return


def emit(
    kind: str,
    name: str,
    value_num: Optional[float] = None,
    value_text: Optional[str] = None,
    labels: Optional[dict] = None,
) -> None:
    """Record a telemetry event. Fire-and-forget.

    HR-8 guarantees this function never raises and never blocks. Any internal
    failure (queue full, DB locked, disk error) results in a silent drop.

    Returns:
        None.

    Refuses:
        Nothing — every error path swallows.
    """
    try:
        # Cheap argument sanity — if the caller passed something un-encodable
        # for labels we still want to try other fields.
        labels_json = None
        if labels is not None:
            try:
                labels_json = json.dumps(labels, sort_keys=True, separators=(",", ":"))
            except Exception:
                labels_json = None
        event = {
            "kind": str(kind) if kind is not None else "unknown",
            "name": str(name) if name is not None else "unnamed",
            "value_num": value_num,
            "value_text": value_text,
            "labels": labels_json,
            "install_id": None,
        }
        writer = _get_writer()
        if writer is None:
            return
        writer.enqueue(event)
    except Exception:
        # HR-8: absolutely never raise from emit().
        return


def flush(timeout: float = 5.0) -> int:
    """Block until queued events are written. Test/diagnostic helper.

    Returns:
        Count of events flushed during this call.

    Refuses:
        Nothing.
    """
    try:
        writer = _get_writer()
        if writer is None:
            return 0
        return writer.flush(timeout=timeout)
    except Exception:
        return 0


def housekeeping(retention_days: int = 90) -> int:
    """Delete metric_events older than ``retention_days``.

    Returns:
        Number of rows deleted.

    Refuses:
        Nothing — returns 0 on any failure.
    """
    if retention_days < 0:
        retention_days = 0
    db = _configured_db if _configured_db is not None else _default_metrics_db()
    try:
        install_schema_if_missing(db)
    except Exception:
        return 0
    conn = None
    try:
        conn = sqlite3.connect(str(db), timeout=5.0)
        cur = conn.execute(
            "DELETE FROM metric_events "
            "WHERE occurred_at < datetime('now', ? || ' days')",
            (f"-{int(retention_days)}",),
        )
        deleted = cur.rowcount or 0
        conn.commit()
        return deleted
    except Exception:
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def aggregate(bucket_date: str) -> int:
    """Roll up metric_events for ``bucket_date`` into metric_aggregates.

    Idempotent: re-running for the same date replaces existing buckets via
    ``INSERT ... ON CONFLICT DO UPDATE``.

    Args:
        bucket_date: ISO-format YYYY-MM-DD.

    Returns:
        Number of (kind, name) pairs written.

    Refuses:
        Nothing — returns 0 on any failure.
    """
    db = _configured_db if _configured_db is not None else _default_metrics_db()
    try:
        install_schema_if_missing(db)
    except Exception:
        return 0
    conn = None
    try:
        conn = sqlite3.connect(str(db), timeout=5.0)
        rows = conn.execute(
            "SELECT kind, name, COUNT(*) AS c, "
            "       SUM(COALESCE(value_num, 0)) AS s "
            "FROM metric_events "
            "WHERE DATE(occurred_at) = ? "
            "GROUP BY kind, name",
            (bucket_date,),
        ).fetchall()
        written = 0
        conn.execute("BEGIN IMMEDIATE")
        for kind, name, c, s in rows:
            conn.execute(
                "INSERT INTO metric_aggregates (bucket, kind, name, count, sum_value) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(bucket, kind, name) DO UPDATE SET "
                "    count = excluded.count, sum_value = excluded.sum_value",
                (bucket_date, kind, name, c, s),
            )
            written += 1
        conn.commit()
        return written
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
