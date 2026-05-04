-- RunawayContext v2 — knowledge.db base schema (the Knowledge Store)
--
-- knowledge.db holds the BRAIN: facts (chunks), lessons (LL), per-project
-- manifests (project_context_card), and junctions linking them.
--
-- Conversation transcripts live in a SEPARATE file — sessions.db. The two are
-- linked by conversation_id (TEXT). When you need full transcript content, ATTACH:
--
--   ATTACH '/path/to/sessions.db' AS s;
--   SELECT s.session_logs.full_transcript
--   FROM lessons_learned l
--   JOIN s.session_logs sl ON sl.conversation_id = l.source_conversation_ref
--   WHERE l.id = ?;
--
-- Apply on a fresh DB or v1 DB — idempotent.

-- ===== Tier 4: Knowledge Store chunks =====
-- A "chunk" is one curated, addressable piece of knowledge. KS#N references this row.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,                       -- canonical project slug (the primary owner)
    project_tags TEXT DEFAULT '[]',     -- JSON array — additional projects this applies to
    topic TEXT NOT NULL,                -- short slug for the chunk
    title TEXT NOT NULL,                -- display name
    body TEXT NOT NULL,                 -- the actual content
    tags TEXT DEFAULT '[]',             -- JSON array of free-form tags
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
