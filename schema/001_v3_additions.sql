-- RunawayContext v3 — additive schema layer
--
-- HR-4: ADD COLUMN / CREATE TABLE / CREATE VIEW / CREATE INDEX only.
-- No DROP. No incompatible type change. Idempotent re-application allowed.
--
-- Covers:
--   E2  soft-delete + record versioning
--   E3  six-state maturation curve + suggestion engine columns
--   E4  three-axis severity (blast_radius, frequency, reversibility)
--   E5  slug lifecycle (aliases, deprecation, merge)
--   E15 specialist agents
--   E16 cross-system data map (T2.5)
--   E17 trigger-based capture drafts inbox
--   E20 brief snapshots (preview/rollback)
--   E22 audit log (hash-chained)
--   E23 author identity + opaque author_id
--   E24 visibility ACL scaffolding

PRAGMA foreign_keys = ON;

-- ============================================================
-- E2 — soft-delete + record versioning
-- ============================================================
-- knowledge_chunks
ALTER TABLE knowledge_chunks ADD COLUMN deleted_at DATETIME;
ALTER TABLE knowledge_chunks ADD COLUMN deleted_by TEXT;
ALTER TABLE knowledge_chunks ADD COLUMN deletion_reason TEXT;
ALTER TABLE knowledge_chunks ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE knowledge_chunks ADD COLUMN previous_version_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_chunks_deleted ON knowledge_chunks(deleted_at);

-- lessons_learned
ALTER TABLE lessons_learned ADD COLUMN deleted_at DATETIME;
ALTER TABLE lessons_learned ADD COLUMN deleted_by TEXT;
ALTER TABLE lessons_learned ADD COLUMN deletion_reason TEXT;
ALTER TABLE lessons_learned ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE lessons_learned ADD COLUMN previous_version_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_ll_deleted ON lessons_learned(deleted_at);

-- Versioned-content archive (HR-3: every write is recoverable)
CREATE TABLE IF NOT EXISTS record_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL CHECK (table_name IN ('knowledge_chunks', 'lessons_learned')),
    record_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,          -- JSON snapshot
    saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    saved_by TEXT,
    reason TEXT,
    UNIQUE(table_name, record_id, version)
);
CREATE INDEX IF NOT EXISTS idx_rv_record ON record_versions(table_name, record_id);

-- Active views (filter soft-deleted)
CREATE VIEW IF NOT EXISTS knowledge_chunks_active AS
    SELECT * FROM knowledge_chunks WHERE deleted_at IS NULL;
CREATE VIEW IF NOT EXISTS lessons_learned_active AS
    SELECT * FROM lessons_learned WHERE deleted_at IS NULL;

-- ============================================================
-- E3 — six-state maturation curve
--      scar -> active -> stable -> internalized -> superseded -> archived
-- ============================================================
ALTER TABLE lessons_learned ADD COLUMN maturity TEXT DEFAULT 'active';
ALTER TABLE lessons_learned ADD COLUMN suggested_maturity TEXT;
ALTER TABLE lessons_learned ADD COLUMN suggested_maturity_reason TEXT;
ALTER TABLE lessons_learned ADD COLUMN suggested_at DATETIME;
ALTER TABLE lessons_learned ADD COLUMN maturity_changed_at DATETIME;
ALTER TABLE lessons_learned ADD COLUMN maturity_changed_by TEXT;
ALTER TABLE lessons_learned ADD COLUMN maturity_locked INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_ll_maturity ON lessons_learned(maturity);

-- Allowed maturity states (enforced by trigger below)
CREATE TABLE IF NOT EXISTS maturity_states (
    state TEXT PRIMARY KEY,
    description TEXT
);
INSERT OR IGNORE INTO maturity_states (state, description) VALUES
    ('scar',          'Freshly written, just-burned, high priority in briefs'),
    ('active',        'In rotation, normal priority'),
    ('stable',        'Repeatedly affirmed, lower brief weight'),
    ('internalized',  'Behavior has taken hold; drops from briefs but stays queryable'),
    ('superseded',    'Replaced by another lesson (see superseded_by)'),
    ('archived',      'Retired from rotation entirely');

-- HR-9: Engine writes suggested_maturity only; Client.mature_lesson() updates maturity.
CREATE TRIGGER IF NOT EXISTS ll_maturity_valid_state
BEFORE UPDATE OF maturity ON lessons_learned
WHEN NEW.maturity IS NOT NULL
 AND NEW.maturity NOT IN (SELECT state FROM maturity_states)
BEGIN
    SELECT RAISE(ABORT, 'invalid maturity state');
END;

-- ============================================================
-- E4 — three-axis severity
--      blast_radius / frequency / reversibility (each 1..5)
-- ============================================================
ALTER TABLE lessons_learned ADD COLUMN blast_radius INTEGER;
ALTER TABLE lessons_learned ADD COLUMN frequency INTEGER;
ALTER TABLE lessons_learned ADD COLUMN reversibility INTEGER;

-- Range checks via trigger (cannot ALTER ADD CHECK in SQLite)
CREATE TRIGGER IF NOT EXISTS ll_axes_range_insert
BEFORE INSERT ON lessons_learned
WHEN (NEW.blast_radius IS NOT NULL AND (NEW.blast_radius < 1 OR NEW.blast_radius > 5))
  OR (NEW.frequency     IS NOT NULL AND (NEW.frequency     < 1 OR NEW.frequency     > 5))
  OR (NEW.reversibility IS NOT NULL AND (NEW.reversibility < 1 OR NEW.reversibility > 5))
BEGIN
    SELECT RAISE(ABORT, 'severity axis out of range (1..5)');
END;
CREATE TRIGGER IF NOT EXISTS ll_axes_range_update
BEFORE UPDATE OF blast_radius, frequency, reversibility ON lessons_learned
WHEN (NEW.blast_radius IS NOT NULL AND (NEW.blast_radius < 1 OR NEW.blast_radius > 5))
  OR (NEW.frequency     IS NOT NULL AND (NEW.frequency     < 1 OR NEW.frequency     > 5))
  OR (NEW.reversibility IS NOT NULL AND (NEW.reversibility < 1 OR NEW.reversibility > 5))
BEGIN
    SELECT RAISE(ABORT, 'severity axis out of range (1..5)');
END;

-- Back-compat derived severity (critical/warning/info) from axes
CREATE VIEW IF NOT EXISTS lessons_derived_severity AS
    SELECT id,
        CASE
            WHEN blast_radius IS NULL AND frequency IS NULL AND reversibility IS NULL
                THEN severity
            WHEN COALESCE(blast_radius,1) >= 4 OR COALESCE(reversibility,1) >= 4
                THEN 'critical'
            WHEN COALESCE(blast_radius,1) >= 2 OR COALESCE(frequency,1) >= 3
                THEN 'warning'
            ELSE 'info'
        END AS derived_severity
    FROM lessons_learned;

-- ============================================================
-- E5 — slug lifecycle (alias, deprecate, merge)
-- ============================================================
CREATE TABLE IF NOT EXISTS slug_registry (
    slug TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deprecated', 'merged', 'alias')),
    canonical_slug TEXT,           -- non-null when alias/merged → points to active slug
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deprecated_at DATETIME,
    merged_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_slugs_status ON slug_registry(status);

CREATE TABLE IF NOT EXISTS slug_aliases (
    alias TEXT PRIMARY KEY,
    canonical TEXT NOT NULL REFERENCES slug_registry(slug) ON DELETE CASCADE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- E15 — specialist agents (first-class concept)
-- ============================================================
CREATE TABLE IF NOT EXISTS specialists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    description TEXT,
    md_path TEXT,
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS specialist_knowledge (
    specialist_id INTEGER NOT NULL REFERENCES specialists(id) ON DELETE CASCADE,
    table_name TEXT NOT NULL CHECK (table_name IN ('knowledge_chunks', 'lessons_learned')),
    record_id INTEGER NOT NULL,
    PRIMARY KEY (specialist_id, table_name, record_id)
);

-- ============================================================
-- E16 — cross-system data map (T2.5)
-- ============================================================
CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system TEXT NOT NULL,            -- e.g., 'vista', 'procore', 'monday'
    name TEXT NOT NULL,              -- table / endpoint
    kind TEXT NOT NULL CHECK (kind IN ('table', 'view', 'endpoint', 'file', 'queue', 'other')),
    description TEXT,
    project TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(system, name)
);

CREATE TABLE IF NOT EXISTS data_source_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_source INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    to_source   INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    join_on TEXT,
    notes TEXT,
    UNIQUE(from_source, to_source, join_on)
);

-- ============================================================
-- E17 — trigger-based capture: drafts inbox
-- ============================================================
CREATE TABLE IF NOT EXISTS lesson_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    what_happened TEXT,
    why TEXT,
    the_fix TEXT,
    prevention_rule TEXT,
    project_tags TEXT DEFAULT '[]',
    source_conversation_ref TEXT,
    proposed_by TEXT,
    proposed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'merged')),
    approved_lesson_id INTEGER REFERENCES lessons_learned(id) ON DELETE SET NULL,
    reviewed_at DATETIME,
    reviewed_by TEXT,
    review_notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON lesson_drafts(status);

-- ============================================================
-- E20 — brief snapshots (preview + rollback)
-- ============================================================
CREATE TABLE IF NOT EXISTS brief_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    md_path TEXT NOT NULL,
    content TEXT NOT NULL,
    line_count INTEGER NOT NULL,
    saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    saved_by TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_project ON brief_snapshots(project, saved_at DESC);

-- ============================================================
-- E22 — audit log (hash-chained, append-only)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    actor TEXT,
    action TEXT NOT NULL,
    target_table TEXT,
    target_id INTEGER,
    details TEXT,           -- JSON
    previous_hash TEXT,
    this_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(occurred_at DESC);

-- HR-7: append-only — never UPDATE, never DELETE
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (HR-7)');
END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (HR-7)');
END;

-- ============================================================
-- E23 — authors + opaque author_id (HR-6)
-- ============================================================
CREATE TABLE IF NOT EXISTS authors (
    author_id TEXT PRIMARY KEY
        CHECK (author_id NOT LIKE '%@%' AND author_id NOT LIKE '%.%'),
    display_name TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME
);

-- Reject email-shaped display names
CREATE TRIGGER IF NOT EXISTS authors_display_no_email_ins
BEFORE INSERT ON authors
WHEN NEW.display_name LIKE '%@%.%'
BEGIN
    SELECT RAISE(ABORT, 'display_name must not look like an email (HR-6)');
END;
CREATE TRIGGER IF NOT EXISTS authors_display_no_email_upd
BEFORE UPDATE OF display_name ON authors
WHEN NEW.display_name LIKE '%@%.%'
BEGIN
    SELECT RAISE(ABORT, 'display_name must not look like an email (HR-6)');
END;

-- Author identity bindings on writes
ALTER TABLE knowledge_chunks ADD COLUMN author_id TEXT;
ALTER TABLE lessons_learned ADD COLUMN author_id TEXT;

-- ============================================================
-- E24 — visibility ACL scaffolding (T4-gated; default private)
-- ============================================================
CREATE TABLE IF NOT EXISTS visibility_levels (
    level TEXT PRIMARY KEY
);
INSERT OR IGNORE INTO visibility_levels (level) VALUES
    ('private'), ('team'), ('org');

-- ALTER ADD COLUMN cannot include REFERENCES or CHECK in SQLite, so we use
-- triggers to validate the value against visibility_levels.
ALTER TABLE knowledge_chunks ADD COLUMN visibility TEXT DEFAULT 'private';
ALTER TABLE lessons_learned ADD COLUMN visibility TEXT DEFAULT 'private';

CREATE TRIGGER IF NOT EXISTS chunks_vis_valid_ins
BEFORE INSERT ON knowledge_chunks
WHEN NEW.visibility IS NOT NULL
 AND NEW.visibility NOT IN (SELECT level FROM visibility_levels)
BEGIN
    SELECT RAISE(ABORT, 'invalid visibility level');
END;
CREATE TRIGGER IF NOT EXISTS chunks_vis_valid_upd
BEFORE UPDATE OF visibility ON knowledge_chunks
WHEN NEW.visibility IS NOT NULL
 AND NEW.visibility NOT IN (SELECT level FROM visibility_levels)
BEGIN
    SELECT RAISE(ABORT, 'invalid visibility level');
END;
CREATE TRIGGER IF NOT EXISTS ll_vis_valid_ins
BEFORE INSERT ON lessons_learned
WHEN NEW.visibility IS NOT NULL
 AND NEW.visibility NOT IN (SELECT level FROM visibility_levels)
BEGIN
    SELECT RAISE(ABORT, 'invalid visibility level');
END;
CREATE TRIGGER IF NOT EXISTS ll_vis_valid_upd
BEFORE UPDATE OF visibility ON lessons_learned
WHEN NEW.visibility IS NOT NULL
 AND NEW.visibility NOT IN (SELECT level FROM visibility_levels)
BEGIN
    SELECT RAISE(ABORT, 'invalid visibility level');
END;

-- ============================================================
-- HR-2 — project_tags validation at write time
-- ============================================================
-- The CLI / Client layer is the primary enforcer (validates against slug_registry).
-- This trigger is the secondary guard: reject blatantly empty/null project_tags
-- on the boundary tables.
CREATE TRIGGER IF NOT EXISTS chunks_require_tags_ins
BEFORE INSERT ON knowledge_chunks
WHEN NEW.project_tags IS NULL
  OR NEW.project_tags = ''
  OR NEW.project_tags = '[]'
BEGIN
    SELECT RAISE(ABORT, 'knowledge_chunks.project_tags required (HR-2)');
END;
CREATE TRIGGER IF NOT EXISTS ll_require_tags_ins
BEFORE INSERT ON lessons_learned
WHEN NEW.project_tags IS NULL
  OR NEW.project_tags = ''
  OR NEW.project_tags = '[]'
BEGIN
    SELECT RAISE(ABORT, 'lessons_learned.project_tags required (HR-2)');
END;

-- ============================================================
-- E3 reference-counter — second re-engagement signal for the
-- maturation engine's active → stable rule. The plan permits
-- ``record_versions`` OR ``last_revisited``; this column gives
-- the engine a real, per-read counter so both signals exist.
-- ============================================================
ALTER TABLE lessons_learned ADD COLUMN reference_count INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_ll_refcount ON lessons_learned(reference_count);

-- Schema version bump
UPDATE schema_version SET major = 3, minor = 0, patch = 0,
    applied_at = CURRENT_TIMESTAMP,
    notes = 'v3.0.0 additive layer applied'
WHERE id = 1;
