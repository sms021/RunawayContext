-- RunawayContext v3 — knowledge.db base schema
--
-- knowledge.db holds curated knowledge:
--   - knowledge_chunks (facts / references)
--   - lessons_learned  (scar-tissue / behavioral rules)
--   - project_context_card (per-project manifest)
--   - junctions (lesson_chunks, chunk_sessions)
-- Conversation transcripts live in a SEPARATE file: sessions.db (see 002_sessions_db.sql).
--
-- v3 is ADDITIVE over v2 (HR-4: migration is non-destructive). Apply on fresh OR v2 DB.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ===== Schema version registry =====
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    major INTEGER NOT NULL,
    minor INTEGER NOT NULL,
    patch INTEGER NOT NULL,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);
INSERT OR IGNORE INTO schema_version (id, major, minor, patch, notes)
VALUES (1, 3, 0, 0, 'v3.0.0 baseline');

-- ===== knowledge_chunks =====
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,                       -- canonical project slug (primary owner)
    project_tags TEXT DEFAULT '[]',     -- JSON array
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project, topic)
);
CREATE INDEX IF NOT EXISTS idx_chunks_project ON knowledge_chunks(project);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    title, body, tags, content=knowledge_chunks, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(rowid, title, body, tags)
    VALUES (new.id, new.title, new.body, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, title, body, tags)
    VALUES ('delete', old.id, old.title, old.body, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, title, body, tags)
    VALUES ('delete', old.id, old.title, old.body, old.tags);
    INSERT INTO knowledge_chunks_fts(rowid, title, body, tags)
    VALUES (new.id, new.title, new.body, new.tags);
END;

-- ===== lessons_learned =====
CREATE TABLE IF NOT EXISTS lessons_learned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,                            -- v1/v2 column kept
    title TEXT NOT NULL,
    lesson TEXT,                             -- v1 back-compat
    context TEXT,                            -- v1 back-compat
    source_session_id INTEGER,               -- v1 back-compat
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    slug TEXT,
    what_happened TEXT,
    why TEXT,
    the_fix TEXT,
    prevention_rule TEXT,
    severity TEXT DEFAULT 'warning',
    status TEXT DEFAULT 'active',
    superseded_by INTEGER,
    project_tags TEXT DEFAULT '[]',
    source_conversation_ref TEXT,
    date_learned DATE,
    last_revisited DATETIME
);
CREATE INDEX IF NOT EXISTS idx_ll_status   ON lessons_learned(status);
CREATE INDEX IF NOT EXISTS idx_ll_severity ON lessons_learned(severity);
CREATE INDEX IF NOT EXISTS idx_ll_date     ON lessons_learned(date_learned DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS lessons_learned_fts USING fts5(
    title, what_happened, why, prevention_rule, project_tags,
    content=lessons_learned, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS ll_fts_insert AFTER INSERT ON lessons_learned BEGIN
    INSERT INTO lessons_learned_fts(rowid, title, what_happened, why, prevention_rule, project_tags)
    VALUES (new.id, new.title, new.what_happened, new.why, new.prevention_rule, new.project_tags);
END;
CREATE TRIGGER IF NOT EXISTS ll_fts_delete AFTER DELETE ON lessons_learned BEGIN
    INSERT INTO lessons_learned_fts(lessons_learned_fts, rowid, title, what_happened, why, prevention_rule, project_tags)
    VALUES ('delete', old.id, old.title, old.what_happened, old.why, old.prevention_rule, old.project_tags);
END;
CREATE TRIGGER IF NOT EXISTS ll_fts_update AFTER UPDATE ON lessons_learned BEGIN
    INSERT INTO lessons_learned_fts(lessons_learned_fts, rowid, title, what_happened, why, prevention_rule, project_tags)
    VALUES ('delete', old.id, old.title, old.what_happened, old.why, old.prevention_rule, old.project_tags);
    INSERT INTO lessons_learned_fts(rowid, title, what_happened, why, prevention_rule, project_tags)
    VALUES (new.id, new.title, new.what_happened, new.why, new.prevention_rule, new.project_tags);
END;

-- ===== project_context_card (one row per project) =====
CREATE TABLE IF NOT EXISTS project_context_card (
    project TEXT PRIMARY KEY,
    top_warnings TEXT DEFAULT '[]',         -- JSON array of LL ids
    active_lesson_ids TEXT DEFAULT '[]',    -- JSON array
    active_chunk_ids TEXT DEFAULT '[]',     -- JSON array
    md_path TEXT,
    md_line_cap INTEGER DEFAULT 150,
    last_rebuilt DATETIME,
    notes TEXT
);

-- ===== junctions =====
CREATE TABLE IF NOT EXISTS lesson_chunks (
    lesson_id INTEGER NOT NULL REFERENCES lessons_learned(id) ON DELETE CASCADE,
    chunk_id  INTEGER NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    PRIMARY KEY (lesson_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_lc_chunk ON lesson_chunks(chunk_id);

CREATE TABLE IF NOT EXISTS chunk_sessions (
    chunk_id INTEGER NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL,
    PRIMARY KEY (chunk_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_cs_conv ON chunk_sessions(conversation_id);
