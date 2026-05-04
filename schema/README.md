# Schema

Two SQLite files. Each is bootstrapped from one or two SQL files in this directory.

```
~/_knowledge/
├── knowledge.db    ← the BRAIN (chunks, lessons, project briefs, junctions)
└── sessions.db     ← the LOG (conversation transcripts + summaries)
```

## Why two files?

- **knowledge.db** is curated and durable. Small. Backed up regularly.
- **sessions.db** is append-only and heavy (transcripts). Backed up less often, retained longer.
- They link via `conversation_id` (TEXT) — `lessons_learned.source_conversation_ref` and `chunk_sessions.conversation_id` point at `session_logs.conversation_id`. Use `ATTACH` to JOIN at query time.
- You can disable session capture entirely by not creating `sessions.db` — knowledge.db works standalone.

## Applying on a fresh install

```bash
KS_DIR=~/_knowledge
mkdir -p "$KS_DIR"

# Knowledge Store
sqlite3 "$KS_DIR/knowledge.db" < schema/000_knowledge_db.sql
sqlite3 "$KS_DIR/knowledge.db" < schema/001_lessons_learned_v2.sql

# Conversation log (optional — skip if you don't want session capture)
sqlite3 "$KS_DIR/sessions.db" < schema/002_sessions_db.sql
```

Or wrapped in one Python call (the v2 setup helper handles errors and re-runs cleanly):

```bash
python3 lib/setup_db.py --ks-dir ~/_knowledge
```

## Upgrading from v1

v1 had a single `sessions.db` containing everything (knowledge_chunks, lessons_learned, sessions). To upgrade:

```bash
KS_DIR=~/_knowledge

# 1. Back up first
cp "$KS_DIR/sessions.db" "$KS_DIR/sessions.db.v1.bak"

# 2. Split: lift knowledge tables out into a new knowledge.db
sqlite3 "$KS_DIR/knowledge.db" <<EOF
ATTACH '$KS_DIR/sessions.db.v1.bak' AS old;
CREATE TABLE knowledge_chunks AS SELECT * FROM old.knowledge_chunks;
CREATE TABLE lessons_learned  AS SELECT * FROM old.lessons_learned;
DETACH old;
EOF

# 3. Apply v2 migrations on the new knowledge.db
sqlite3 "$KS_DIR/knowledge.db" < schema/000_knowledge_db.sql
sqlite3 "$KS_DIR/knowledge.db" < schema/001_lessons_learned_v2.sql

# 4. The old sessions.db.v1.bak still has the v1 sessions table; rename
#    + apply the new sessions schema to the live sessions.db (preserves data)
mv "$KS_DIR/sessions.db" "$KS_DIR/sessions.db.v1.original"
sqlite3 "$KS_DIR/sessions.db" <<EOF
ATTACH '$KS_DIR/sessions.db.v1.original' AS old;
CREATE TABLE session_logs AS SELECT * FROM old.sessions;  -- v1 'sessions' becomes v2 'session_logs'
DETACH old;
EOF
sqlite3 "$KS_DIR/sessions.db" < schema/002_sessions_db.sql
```

Or use the helper:

```bash
python3 lib/migrate_v1_to_v2.py --v1-db ~/_knowledge/sessions.db
```

## What each file does

| File | Database | Adds |
|------|----------|------|
| `000_knowledge_db.sql` | `knowledge.db` | `knowledge_chunks` + FTS5 |
| `001_lessons_learned_v2.sql` | `knowledge.db` | `lessons_learned` v2, `project_context_card`, `lesson_chunks`, `chunk_sessions` |
| `002_sessions_db.sql` | `sessions.db` | `session_logs` + FTS5 |

## Cross-DB queries

When you need session content while looking at a lesson:

```sql
ATTACH '/path/to/sessions.db' AS s;

-- Full transcript for a lesson's source session
SELECT s.session_logs.full_transcript
FROM lessons_learned l
JOIN s.session_logs sl ON sl.conversation_id = l.source_conversation_ref
WHERE l.id = 42;

-- Sessions where a chunk came up
SELECT s.session_logs.session_date, s.session_logs.summary
FROM chunk_sessions cs
JOIN s.session_logs sl ON sl.conversation_id = cs.conversation_id
WHERE cs.chunk_id = 17;

DETACH s;
```

## Sanity check after install

```sql
.tables
SELECT 'chunks',   COUNT(*) FROM knowledge_chunks
UNION ALL SELECT 'lessons',  COUNT(*) FROM lessons_learned
UNION ALL SELECT 'cards',    COUNT(*) FROM project_context_card;

-- Connected to sessions.db too?
ATTACH '/path/to/sessions.db' AS s;
SELECT 'sessions', COUNT(*) FROM s.session_logs;
```
