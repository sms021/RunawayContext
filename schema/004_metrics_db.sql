-- RunawayContext v3 — metrics.db schema (telemetry sidecar)
--
-- HR-1: local-only by default. HR-8: writes never block, never raise.
-- Lives in its own SQLite file so housekeeping can prune without touching
-- knowledge.db.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS metric_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    kind TEXT NOT NULL,             -- 'retrieval', 'brief_build', 'mature', 'drift', 'init', ...
    name TEXT NOT NULL,             -- short event name
    value_num REAL,
    value_text TEXT,
    labels TEXT,                    -- JSON
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
