# Migration v2 → v3

This is the end-to-end walkthrough for migrating an existing RunawayContext v2 install to v3. The migration is **non-destructive** by HR-4: every v2 column, table, and row is preserved.

If anything goes wrong, you have a backup. If the migrator detects that anything would be lost, it aborts and restores from backup before exiting. There is no partial migration.

---

## What changes

| v2 surface | v3 status |
|---|---|
| `knowledge_chunks` table | Preserved + new columns added (ADD COLUMN only) |
| `lessons_learned` table | Preserved + new columns added |
| `project_context_card` table | Preserved + new columns added |
| `slug_registry` | New table created; v2's `_project_slugs.py` constants are imported as canonical rows |
| `lesson_chunks`, `chunk_sessions` junction tables | Preserved unchanged |
| `session_logs` (sessions.db) | Preserved unchanged |
| `severity` column | Preserved for back-compat; new code writes `blast_radius/frequency/reversibility` and the view `lessons_derived_severity` projects back to v2-style severity |
| `status` column (v2 lessons lifecycle) | Preserved; new `maturity` column replaces it for v3 lifecycle, with status backfilled |
| v1 `lesson` / `context` columns | Still preserved (originally preserved in v2 migration) |
| `prevention_rule` / `what_happened` | Preserved |
| Hand-edited Tier 1 (Constitution) | Untouched (your file, your control) |
| Hand-edited Tier 2 (MEMORY.md) | Untouched |
| Auto-generated Tier 3 briefs | Re-rendered post-migration to include new sections (PRESERVE block survives) |

| New v3 tables (all created via `CREATE TABLE IF NOT EXISTS`) |
|---|
| `record_versions` — soft-delete archive (HR-3) |
| `maturity_states` — canonical six |
| `slug_aliases` — slug lifecycle |
| `specialists`, `specialist_knowledge` — specialist agents |
| `data_sources`, `data_source_mappings` — cross-system map |
| `lesson_drafts` — trigger capture inbox |
| `brief_snapshots` — brief preview/rollback |
| `audit_log` — hash-chained, append-only |
| `authors` — opaque author identity |
| `visibility_levels` — canonical three |

---

## The Migration Workflow

### Step 1 — Back up first

The migrator backs up automatically. Back up manually too. Paranoia compounds in your favor.

```bash
# Backup v2 databases
cp ~/_knowledge/knowledge.db ~/_knowledge/knowledge.db.pre-v3.bak
cp ~/_knowledge/sessions.db  ~/_knowledge/sessions.db.pre-v3.bak

# Backup hand-edited files (in case you want to compare diffs later)
cp ~/.claude/CLAUDE.md                  ~/.claude/CLAUDE.md.pre-v3.bak
cp ~/.claude/memory/MEMORY.md           ~/.claude/memory/MEMORY.md.pre-v3.bak

# Verify backups exist and are non-zero
ls -la ~/_knowledge/*.pre-v3.bak ~/.claude/*.pre-v3.bak
```

### Step 2 — Install v3

```bash
git clone https://github.com/sms021/RunawayContext.git ~/RunawayContext_v3
cd ~/RunawayContext_v3
pip install -e .
```

Verify the install:

```bash
runaway --version
# expected: runaway 3.0.0
```

### Step 3 — Run the migrator

```bash
runaway migrate --from v2 --to v3 \
    --knowledge-db ~/_knowledge/knowledge.db \
    --sessions-db  ~/_knowledge/sessions.db \
    --verify
```

The migrator does, in order:

1. **Snapshot the v2 schema.** `PRAGMA table_info()` for every v2 table is recorded.
2. **Internal backup.** Writes `~/_knowledge/knowledge.db.pre-v3.internal.bak` and `~/_knowledge/sessions.db.pre-v3.internal.bak`. Aborts if these cannot be written.
3. **Apply the additive layer.** `schema/001_v3_additions.sql` is applied. Every ALTER is `ADD COLUMN`; every new object is `CREATE TABLE IF NOT EXISTS` / `CREATE VIEW IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. No DROP.
4. **Backfill `slug_registry`.** Every distinct project tag in `knowledge_chunks.project_tags`, `lessons_learned.project_tags`, and v2's `_project_slugs.CANONICAL_PROJECT_SLUGS` is inserted into `slug_registry` with `status='active'`.
5. **Backfill `maturity`.** Existing v2 `status` values map: `active` → `active`, `superseded` → `superseded`, `archived` → `archived`. New v3 states (`scar`, `stable`, `internalized`) are not retroactively assigned; lessons stay at `active` until the engine suggests and a human approves.
6. **Backfill `version`.** Every existing row gets `version = 1`.
7. **Recompute schema check.** `PRAGMA table_info()` is run again on every v2 table. The migrator asserts every v2 column from step 1 is still present and has the same type.
8. **If any column is missing**: abort. The internal backup is restored. The migration is reported as failed and the install is left at v2.
9. **Update `schema_version`.** Sets `major=3, minor=0, patch=0`.
10. **Audit the migration.** A single row is appended to `audit_log` recording the migration with the schema diff, the row counts before/after, and the operator id.

If `--verify` is set (and it should be), the migrator additionally runs:

```bash
pytest -m contract --override-knowledge-db ~/_knowledge/knowledge.db
```

If any contract test fails, the migrator reports the failure and **does not mark the migration complete**.

### Step 4 — Verify

```bash
# Tier check
runaway tier check
# Expected: T2 (or T1, depending on your v2 surface)

# Schema version
runaway db schema-version
# Expected: 3.0.0

# Audit chain is intact
runaway audit verify
# Expected: chain_intact = true

# Contract tests
pytest -m contract -v
```

### Step 5 — Re-render the briefs

Briefs are auto-generated; v3 may produce slightly different output (because new fields like `derived_severity` and `maturity` are rendered). Run a regen:

```bash
runaway brief regen-all
# or per-slug
runaway brief regen --slug accounting
```

The PRESERVE block in each brief is untouched. Everything else is rewritten.

If any brief overflows (a v2 install accumulating chunks past the cap), the writer refuses (HR-5) and reports the slug and computed line count. You then have two options:

1. Bump the cap globally (`runaway config set brief.line_cap 200` — applies to all projects).
2. Use `runaway drift suggest --slug <slug>` to find prune candidates (low-utilization lessons, deeply nested chunks, etc.).

### Step 6 — Wire up MCP (if you want T2)

If your v2 install was T1-equivalent (no MCP), you can stay there and enjoy the new schema. If you want the MCP surface:

```bash
runaway tier promote --to T2 --check
runaway tier promote --to T2
runaway mcp register --tool claude-code
```

See [BOOTSTRAP.md § Tier 2](BOOTSTRAP.md#tier-2--solo-power-the-most-common-target) for MCP wiring details.

---

## Rollback

If anything looks wrong post-migration, you have two rollback paths:

### Soft rollback — stay on v3 schema, ignore v3 features

This is rarely needed. v3 features are opt-in (MCP, semantic, telemetry); you can disable them in `runaway config` and keep the v2 surface unchanged.

```bash
runaway config set mcp.enabled false
runaway config set telemetry.enabled false
runaway config set semantic.enabled false
runaway tier demote --to T1
```

The schema columns remain in the DB (HR-4 forbids dropping them) but the code paths do not exercise them.

### Hard rollback — restore the v2 backup

```bash
# Stop any running MCP server, drift watchers, etc.
runaway mcp stop || true
crontab -l | grep -v md_drift_watcher | crontab -

# Restore the v2 backups
cp ~/_knowledge/knowledge.db.pre-v3.bak ~/_knowledge/knowledge.db
cp ~/_knowledge/sessions.db.pre-v3.bak  ~/_knowledge/sessions.db

# Uninstall v3 from the active Python env
pip uninstall runaway-context

# Reinstall v2
cd ~/RunawayContext_v2
pip install -e .

# Verify
~/RunawayContext_v2/bin/check_md_drift.sh
python3 ~/RunawayContext_v2/lib/ll_brief.py --rebuild-md --all
```

Any v3-only writes (drafts, audit log entries, snapshots, specialist registrations) made *after* the migration are lost on hard rollback. The v2 data — every chunk, every lesson, every project card — is fully preserved.

---

## Common Migration Issues

| Symptom | Cause | Fix |
|---|---|---|
| `Migration aborted: column lost` | A v2 column is missing post-migration (should never happen — the migrator's job is to prevent this) | The migrator restored from backup. Report the bug. Do not retry without investigation. |
| `BriefBudgetExceeded for slug <slug>` on first regen | Your v2 install had a brief at the cap, and v3 adds a few extra rendered lines | Either bump the cap or prune via `runaway drift suggest` |
| `InvalidSlug: <slug>` on a write that worked in v2 | Slug was in v2's `_project_slugs.CANONICAL_PROJECT_SLUGS` but the migrator backfill missed it | `runaway slug register --slug <slug>` |
| `AuditChainBroken at row 0` | The audit table was created during migration but no first row was written | Reapply the schema; the migration's audit row should be row 0 |
| Telemetry not recording | T1 install — telemetry is T2 only | Promote to T2 or accept telemetry stays off |
| MCP server fails to start | Schema version mismatch | Run `runaway migrate --to v3 --verify` again |
| Drift watcher fires on previously-clean files | v3 caps are slightly stricter on some edge cases (e.g., trailing whitespace counted) | Run `runaway brief regen-all` |

---

## Verifying Non-Destructiveness (HR-4 in your face)

If you want to prove to yourself the migration was non-destructive:

```bash
# Diff row counts before and after
sqlite3 ~/_knowledge/knowledge.db.pre-v3.bak \
    "SELECT name, (SELECT COUNT(*) FROM sqlite_master WHERE type='table') AS table_count FROM sqlite_master WHERE type='table';"

sqlite3 ~/_knowledge/knowledge.db \
    "SELECT name, (SELECT COUNT(*) FROM sqlite_master WHERE type='table') AS table_count FROM sqlite_master WHERE type='table';"

# Compare row counts on the v2 tables
for tbl in knowledge_chunks lessons_learned project_context_card lesson_chunks chunk_sessions; do
    cnt_v2=$(sqlite3 ~/_knowledge/knowledge.db.pre-v3.bak "SELECT COUNT(*) FROM $tbl")
    cnt_v3=$(sqlite3 ~/_knowledge/knowledge.db          "SELECT COUNT(*) FROM $tbl")
    echo "$tbl: v2=$cnt_v2  v3=$cnt_v3"
done

# Every line should show identical counts.
```

If any count differs, the migration is not non-destructive. Report it as a critical bug.

---

## Migration Audit Trail

The migration itself is audited. After migration:

```bash
runaway audit list --action migration
```

You will see a row with:

- `actor` — the operator who ran the migration.
- `action` — `migration:v2->v3`.
- `details` — JSON with row counts before/after, schema diff, and time taken.
- `this_hash` — the first row of your v3-era audit chain.

This row is the genesis of your audit log. Every subsequent write chains from here (HR-7). Tampering with this row breaks `runaway audit verify`.

---

## Post-Migration Checklist

After the migration, verify each of the following:

- [ ] `runaway --version` returns 3.0.0.
- [ ] `runaway db schema-version` returns 3.0.0.
- [ ] `runaway tier check` reports a sensible tier.
- [ ] `runaway audit verify` returns chain intact.
- [ ] `pytest -m contract -v` passes all HR-* tests.
- [ ] Row counts on `knowledge_chunks`, `lessons_learned`, `project_context_card` are unchanged from v2.
- [ ] `runaway brief regen-all` completes without `BriefBudgetExceeded` for any project. (If one fails, you knew it was at cap; resolve before declaring migration complete.)
- [ ] The hand-edited Constitution and MEMORY.md are untouched (diff against the `.pre-v3.bak` copies).
- [ ] If you wired up MCP: `runaway mcp serve --once` starts cleanly.
- [ ] No process is making network calls (verify with `runaway config show --network`).

When every box is checked, the migration is complete. Until then, do not declare it done — per the Adopter's AI Contract in [INSTALL_PROMPT.md](INSTALL_PROMPT.md).
