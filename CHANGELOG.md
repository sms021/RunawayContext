# Changelog

All notable changes to RunawayContext are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — major version bumps signal breaking architectural changes.

---

## [2.0.0] — 2026-05-04

Major architectural upgrade. v1's policy-only constraints quietly drifted over six months of real-world use; v2 moves the discipline into the schema, the CLI write guards, a regenerator with a hard line cap, and a drift detector. The conceptual 4-tier model is unchanged. What changed is **how the discipline is enforced**.

### Added

- **`project_context_card` table** — a new manifest layer. One row per project, holds `top_warnings` + `active_lesson_ids` + `active_chunk_ids` + `md_path` + `md_line_cap`. Auto-rebuilt from any tagged source row. Your AI's first action when entering a project is now to query this card.
- **Auto-generated Tier 3 (Project Briefs)** — the project's CLAUDE.md (or equivalent) is now a generated artifact, written by `lib/md_writer.py` from `project_context_card`. A small `<!-- PRESERVE_START --> ... <!-- PRESERVE_END -->` block holds the human-curated overview and survives every regen; everything else is overwritten. Hard line cap (default 150) is enforced by the writer.
- **Required project tagging on every write** — `lib/propose_knowledge.py` and `lib/ll_brief.py --log-lesson` both require a `--project` slug validated against `CANONICAL_PROJECT_SLUGS` in `lib/_project_slugs.py`. Typos rejected at the CLI; untagged content cannot reach the DB.
- **Auto-tagged miner support** — `slug_from_path()` returns `None` for junk paths (`node_modules`, `vendor/`, `.bak`, `.backup.`, `__pycache__`, etc.). Mining scripts that route through it can't pollute the KS with backup-directory rows.
- **Auto-stamped `source_user`** — every write captures `$SUDO_USER`/`$USER`. Audit trail in multi-user setups.
- **Drift detector**:
  - `bin/check_md_drift.sh` — Stop hook for Claude Code CLI and similar
  - `bin/md_drift_watcher.sh` — cron / launchd watcher for VS Code Claude extension and other Stop-hook-less tools
  - Both walk `project_context_card` rows and warn when any registered file exceeds its line cap
- **Split-database design** — `knowledge.db` (curated, small, frequently backed up) and `sessions.db` (heavy transcripts, retained longer). Linked via `conversation_id` (TEXT). ATTACH at query time when joins are needed.
- **Lessons-learned lifecycle** — new columns: `severity` (critical/warning/info), `status` (active/superseded/archived), `superseded_by` (self-ref). Briefs auto-drop superseded lessons on rebuild.
- **Junction tables** — `lesson_chunks` (LL ↔ knowledge_chunks) and `chunk_sessions` (chunks ↔ session_logs). Replaces fragile JSON arrays for cross-references. Enables real cascading deletes within knowledge.db.
- **Multi-user setup helper** — `bin/setup_user_protections.sh` provisions Stop hook + seeded MEMORY.md across every Claude user on a shared host (Mac `/Users/*` or Linux `/home/*`). Backs up existing files in place (`*.pre-rc-v2.YYYYMMDD.bak`) before any modification. Idempotent, cross-platform, dry-run by default.
- **Dated DB snapshots** — `bin/backup_db.sh` uses SQLite's `.backup` (consistent online snapshot) and auto-prunes to last 30 backups. Works while DB is in active use.
- **Configurable install location** — `RC_KS_DIR` env var overrides the default `~/_knowledge/`. All scripts honor it.
- **`lib/_db.py`** — shared helper for opening connections with `PRAGMA foreign_keys = ON` and ATTACHing sessions.db on demand.
- **CHANGELOG.md** (this file).

### Changed

- **README.md** — bumped to v2, added "What's New in v2" table, updated the tier table with enforced budgets (`≤150 lines (enforced)` for Tier 3), expanded the file tree, refined Background to explain why v2 exists.
- **BOOTSTRAP.md** — Tier 3 section rewritten to describe auto-generation rather than hand-editing. Tier 4 section rewritten for split-DB design. New "Write Guards" and "Drift Detection" sections. "Keeping It Alive" updated to reflect tooling-enforced discipline.
- **RUNAWAYCONTEXT.md** — v2 preamble at top, table of contents extended (sections 13-16 added). Tier 3 and Tier 4 sections rewritten in-place with v1 → v2 callouts. Four new sections at the end: 13 (v1→v2 upgrade), 14 (Write Guards), 15 (Drift Detection), 16 (Multi-User Setup). v1 narrative preserved as foundational context.
- **run_RunawayContext.md (executable prompt)** — Phase 2 dropped the Level 1 markdown option; uses `setup_db.py` + slug taxonomy editing instead. Phase 3 rewritten as `--rebuild-brief` / `--rebuild-md` flow rather than hand-writing per-project files. Phase 6 wires the drift detector (Stop hook on Claude Code, cron watcher elsewhere).

### Deprecated

- **Hand-edited Tier 3 files** — still possible, but hand-edits outside the PRESERVE block are wiped on next regen. The path forward is to log knowledge to the DB and let the regenerator render the file.
- **"Level 1: organized markdown files" Knowledge Store** — dropped. There's now one path: SQLite + FTS5. The maintenance cost of supporting two paths everywhere outweighed the install simplicity savings.
- **v1 `lessons_learned.lesson` and `.context` columns** — preserved for back-compat. New code should write `prevention_rule` (replaces `lesson`) and `what_happened` (replaces `context`). The migrator backfills both directions.
- **v1 `lessons_learned.source_session_id`** — preserved for back-compat. New code should use `source_conversation_ref` (TEXT, points to `session_logs.conversation_id` in sessions.db).

### Migration

For existing v1 users:

```bash
# 1. Back up first (the migrator also backs up — be paranoid)
cp ~/_knowledge/sessions.db ~/_knowledge/sessions.db.manual.bak

# 2. Run the migrator
python3 lib/migrate_v1_to_v2.py --v1-db ~/_knowledge/sessions.db
```

The migrator:
1. Backs up the v1 file in place
2. Lifts `knowledge_chunks` + `lessons_learned` into a new `knowledge.db`
3. Renames the v1 `sessions` table to `session_logs` in a new `sessions.db`
4. Applies all v2 schema migrations to both DBs
5. Backfills v1 `project` → `project_tags`, `lesson` → `prevention_rule`, `context` → `what_happened`
6. Verifies row counts on both sides

After the migrator runs:
1. Edit `lib/_project_slugs.py` to set up your canonical slug list
2. `python3 lib/ll_brief.py --rebuild-brief <slug>` for each project
3. Set `md_path` on each `project_context_card` row
4. `python3 lib/ll_brief.py --rebuild-md <slug>` to write the auto-generated briefs
5. Wire up the drift detector (Stop hook or cron watcher per your AI tool)

See [README.md](README.md) and [RUNAWAYCONTEXT.md §13](RUNAWAYCONTEXT.md#13-v1--v2-upgrade) for the full upgrade story.

### Files added in v2

```
schema/
├── 000_knowledge_db.sql
├── 001_lessons_learned_v2.sql
├── 002_sessions_db.sql
└── README.md
lib/
├── _db.py
├── _project_slugs.py
├── ll_brief.py
├── md_writer.py
├── migrate_v1_to_v2.py
├── propose_knowledge.py
└── setup_db.py
bin/
├── backup_db.sh
├── check_md_drift.sh
├── md_drift_watcher.sh
└── setup_user_protections.sh
CHANGELOG.md
```

### Why v2 exists

v1 told you to keep your Constitution under 200 lines, your Living Memory under 50, your project brains "well-organized." Six months of daily use proved this was policy-only — the files drifted. Every session adds a "while I'm here, let me note this." The rules quietly stopped being enforced.

v2's enforcement isn't a bigger sign on the wall saying "keep it small." It's:

- A regenerator that won't write past 150 lines
- A CLI that rejects untagged writes
- A schema that requires `project_tags` and validates against a canonical list
- A drift detector that surfaces violations the moment they happen

The discipline is no longer your job. It's the system's.

---

## [1.1.0] — 2026-04-07

### Added

- AI-powered session summary safeguards (batch limits, processed marker, attempt cap, lock file, no-retry rule). Documented in BOOTSTRAP.md after a runaway-loop incident burned a third of a week's token budget.

### Changed

- Project renamed from **SuperContext** to **RunawayContext** to avoid conflicts with existing projects. GitHub auto-redirects the old URL.

---

## [1.0.0] — 2026-04-03

### Added

- Initial release as **SuperContext** (renamed to **RunawayContext** four days later).
- 4-tier knowledge architecture (Constitution / Living Memory / Project Brains / Knowledge Store).
- Session memory layer with SQLite + capture hook.
- Tool-specific setup guides for Claude Code, Cursor, Copilot, Codex, Aider, Windsurf.
- BOOTSTRAP.md (manual walkthrough), RUNAWAYCONTEXT.md (full theory + reference), run_RunawayContext.md (executable prompt).
- MIT license.
