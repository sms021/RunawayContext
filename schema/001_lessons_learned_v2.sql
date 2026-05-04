-- RunawayContext v2 — lessons_learned + project_context_card + lesson_chunks + chunk_sessions
--
-- v1 had: lessons_learned(id, project, title, lesson, context, source_session_id)
-- v2 adds: severity, status, slug, supersession, structured what/why/fix/prevention,
--         project_tags, FTS5, source_conversation_ref (TEXT, points to sessions.db)
-- v2 NEW tables: project_context_card, lesson_chunks, chunk_sessions
--
-- v1 → v2 is additive and idempotent.

-- ===== Step 1: lessons_learned base shape (idempotent) =====
CREATE TABLE IF NOT EXISTS lessons_learned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,                            -- v1 column kept for back-compat
    title TEXT NOT NULL,
    lesson TEXT,                             -- v1 column kept for back-compat
    context TEXT,                            -- v1 column kept for back-compat
    source_session_id INTEGER,               -- v1 column kept; deprecated in favor of source_conversation_ref
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===== Step 2: ALTERs to add v2 columns =====
-- Duplicate-column errors on re-run are non-fatal in interactive sqlite3.
ALTER TABLE lessons_learned ADD COLUMN slug TEXT;
ALTER TABLE lessons_learned ADD COLUMN what_happened TEXT;
ALTER TABLE lessons_learned ADD COLUMN why TEXT;
ALTER TABLE lessons_learned ADD COLUMN the_fix TEXT;
ALTER TABLE lessons_learned ADD COLUMN prevention_rule TEXT;
ALTER TABLE lessons_learned ADD COLUMN severity TEXT DEFAULT 'warning';
ALTER TABLE lessons_learned ADD COLUMN status TEXT DEFAULT 'active';
ALTER TABLE lessons_learned ADD COLUMN superseded_by INTEGER;
ALTER TABLE lessons_learned ADD COLUMN project_tags TEXT DEFAULT '[]';
ALTER TABLE lessons_learned ADD COLUMN source_conversation_ref TEXT;  -- points to sessions.db.session_logs.conversation_id
ALTER TABLE lessons_learned ADD COLUMN date_learned DATE;
ALTER TABLE lessons_learned ADD COLUMN last_revisited DATETIME;

-- Backfill: copy v1 single `project` into project_tags JSON array
UPDATE lessons_learned
SET project_tags = json_array(project)
WHERE project IS NOT NULL
  AND (project_tags IS NULL OR project_tags = '[]');

-- Backfill: v1 lesson → prevention_rule (the do/don't statement)
UPDATE lessons_learned
SET prevention_rule = lesson
WHERE prevention_rule IS NULL AND lesson IS NOT NULL;

-- Backfill: v1 context → what_happened (the incident description)
UPDATE lessons_learned
SET what_happened = context
WHERE what_happened IS NULL AND context IS NOT NULL;

-- ===== Step 3: indexes + FTS5 =====
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
INSERT INTO lessons_learned_fts(lessons_learned_fts) VALUES ('rebuild');

-- ===== Step 4: project_context_card (NEW in v2) =====
-- The "manifest" tier — one row per project, holds all the active LL/chunk pointers.
-- Rebuilt by --rebuild-brief from any LL or chunk row tagged with this project's slug.
CREATE TABLE IF NOT EXISTS project_context_card (
    project_slug TEXT PRIMARY KEY,
    title TEXT,
    overview TEXT,
    owner TEXT,
    top_warnings TEXT DEFAULT '[]',
    active_lesson_ids TEXT DEFAULT '[]',
    active_chunk_ids TEXT DEFAULT '[]',
    recent_session_refs TEXT DEFAULT '[]',  -- JSON array of conversation_ids (point to sessions.db)
    md_path TEXT,                           -- where the project's CLAUDE.md / equivalent lives
    md_line_cap INTEGER DEFAULT 150,
    last_rebuilt_at DATETIME,
    last_md_written_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===== Step 5: junctions =====
-- lesson_chunks: which knowledge chunks does each lesson inform / derive from
CREATE TABLE IF NOT EXISTS lesson_chunks (
    lesson_id INTEGER NOT NULL REFERENCES lessons_learned(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    relationship TEXT,                       -- 'informs' | 'derived_from' | 'see_also'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (lesson_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_lc_chunk ON lesson_chunks(chunk_id);

-- chunk_sessions: which sessions did this chunk come up in.
-- conversation_id is TEXT (no cross-DB FK enforcement — sessions.db owns the
-- session_logs table). Resolution at query time via ATTACH.
CREATE TABLE IF NOT EXISTS chunk_sessions (
    chunk_id INTEGER NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL,
    PRIMARY KEY (chunk_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_cs_conv ON chunk_sessions(conversation_id);
