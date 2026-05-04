-- RunawayContext v2 — sessions.db (the conversation log)
--
-- A SEPARATE SQLite file from knowledge.db. Holds rich session content + full
-- transcripts. Linked to knowledge.db via conversation_id (TEXT, no cross-DB FK).
--
-- Apply on: a fresh sessions.db OR a v1 sessions.db that has session_logs already.
-- Idempotent — safe to re-run.
--
-- Why separate from knowledge.db?
--   * Transcripts are heavy (often hundreds of KB each); keeping them in a
--     dedicated file means knowledge.db backups stay small and quick.
--   * Different lifecycle — sessions are append-only and ephemeral; knowledge
--     is curated and durable. Different backup cadences make sense.
--   * Easy to disable session capture entirely (don't create sessions.db) without
--     breaking knowledge.db.

-- ===== session_logs =====
-- One row per archived conversation.
CREATE TABLE IF NOT EXISTS session_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL UNIQUE,    -- the cross-DB join key
    session_date DATE,
    project TEXT,                            -- canonical project slug (best-effort)
    summary TEXT,                            -- 1-3 sentence summary
    work_completed TEXT,
    technical_decisions TEXT,
    known_issues TEXT,
    key_context TEXT,
    files_modified TEXT,                     -- JSON array of file paths
    full_transcript TEXT,                    -- the actual transcript text (optional)
    log_file_path TEXT,                      -- pointer to external file if transcript not inline
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON session_logs(project);
CREATE INDEX IF NOT EXISTS idx_sessions_date    ON session_logs(session_date DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_conv    ON session_logs(conversation_id);

-- ===== FTS5 over summary fields (NOT full_transcript — too large to index) =====
CREATE VIRTUAL TABLE IF NOT EXISTS session_logs_fts USING fts5(
    summary, work_completed, technical_decisions, known_issues, key_context,
    content=session_logs, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS sl_fts_insert AFTER INSERT ON session_logs BEGIN
    INSERT INTO session_logs_fts(rowid, summary, work_completed, technical_decisions, known_issues, key_context)
    VALUES (new.id, new.summary, new.work_completed, new.technical_decisions, new.known_issues, new.key_context);
END;
CREATE TRIGGER IF NOT EXISTS sl_fts_delete AFTER DELETE ON session_logs BEGIN
    INSERT INTO session_logs_fts(session_logs_fts, rowid, summary, work_completed, technical_decisions, known_issues, key_context)
    VALUES ('delete', old.id, old.summary, old.work_completed, old.technical_decisions, old.known_issues, old.key_context);
END;
CREATE TRIGGER IF NOT EXISTS sl_fts_update AFTER UPDATE ON session_logs BEGIN
    INSERT INTO session_logs_fts(session_logs_fts, rowid, summary, work_completed, technical_decisions, known_issues, key_context)
    VALUES ('delete', old.id, old.summary, old.work_completed, old.technical_decisions, old.known_issues, old.key_context);
    INSERT INTO session_logs_fts(rowid, summary, work_completed, technical_decisions, known_issues, key_context)
    VALUES (new.id, new.summary, new.work_completed, new.technical_decisions, new.known_issues, new.key_context);
END;
INSERT INTO session_logs_fts(session_logs_fts) VALUES ('rebuild');
