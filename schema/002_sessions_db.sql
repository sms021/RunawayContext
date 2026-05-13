-- RunawayContext v3 — sessions.db schema
--
-- sessions.db is a separate file. Holds raw conversation transcripts.
-- Linked to knowledge.db via session_logs.conversation_id (TEXT).
--
-- Cross-DB query:
--   ATTACH '/path/to/sessions.db' AS s;
--   SELECT l.title, sl.full_transcript
--   FROM lessons_learned l
--   JOIN s.session_logs sl ON sl.conversation_id = l.source_conversation_ref;

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS session_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL UNIQUE,    -- foreign key from knowledge.db
    tool TEXT,                                -- 'claude-code' / 'cursor' / 'aider' / ...
    machine TEXT,
    project_hint TEXT,                        -- best-guess project at start
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    full_transcript TEXT,                     -- raw text
    token_in INTEGER,
    token_out INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_sl_started ON session_logs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sl_project ON session_logs(project_hint);
