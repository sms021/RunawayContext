-- RunawayContext v3 — semantic sidecar tables
--
-- E12: sqlite-vec sidecar integration. We define non-vec0 fallback tables here
-- so the schema applies on stock SQLite. When sqlite-vec is loaded, the
-- runtime code uses the vec0 virtual tables alongside these; when it isn't,
-- the system falls back to FTS5-only retrieval.
--
-- HR-1: pure schema, no network. Embedding generation requires explicit
-- opt-in (config flag + runaway_context.embeddings.providers.*).

PRAGMA foreign_keys = ON;

-- Embedding metadata sidecar (non-vec0; always present)
CREATE TABLE IF NOT EXISTS embedding_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL CHECK (table_name IN ('knowledge_chunks', 'lessons_learned')),
    record_id INTEGER NOT NULL,
    provider TEXT NOT NULL,         -- 'sentence-transformers-MiniLM-L6-v2', etc.
    dim INTEGER NOT NULL,
    norm REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(table_name, record_id, provider)
);

-- Raw embedding payload (BLOB float32 little-endian)
-- This is the fallback storage when vec0 is not loaded.
CREATE TABLE IF NOT EXISTS embedding_payload (
    embedding_meta_id INTEGER PRIMARY KEY
        REFERENCES embedding_meta(id) ON DELETE CASCADE,
    vec BLOB NOT NULL
);

-- Provider registry (multiple providers per record allowed)
CREATE TABLE IF NOT EXISTS embedding_providers (
    provider TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    is_network INTEGER DEFAULT 0,   -- 1 = network egress required, must be opt-in
    is_local INTEGER DEFAULT 1,
    description TEXT
);
INSERT OR IGNORE INTO embedding_providers (provider, dim, is_network, is_local, description) VALUES
    ('sentence-transformers-MiniLM-L6-v2', 384, 0, 1, 'Local CPU model, default'),
    ('sentence-transformers-mpnet-base-v2', 768, 0, 1, 'Local CPU model, higher quality'),
    ('ollama-nomic-embed-text', 768, 1, 1, 'Local Ollama server (loopback)'),
    ('openai-text-embedding-3-small', 1536, 1, 0, 'OpenAI API; opt-in only'),
    ('voyage-2', 1024, 1, 0, 'Voyage AI API; opt-in only');
