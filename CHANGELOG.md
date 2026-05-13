# Changelog

All notable changes to RunawayContext are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — major version bumps signal breaking architectural changes (none in v3 — see HR-4).

---

## [3.0.0] — 2026-05-13

The contract-enforced rewrite. v3 is **non-destructive** vs. v2 (HR-4): every v2 column, table, and row is preserved. What changes is the *contract surface* — fifteen named hard rules with machine-checkable enforcers, a six-rung tier ladder with promotion gates, an MCP server, a maturation curve, three-axis severity, slug lifecycle, audit log, and ten written specs for the integrations adopters' AIs build themselves.

### Added

- **Hard Rules Charter (HR-1..HR-15).** Fifteen non-negotiable contracts with named enforcers, named tests, and named violation handlers. Documented in [docs/HARD_RULES.md](docs/HARD_RULES.md). Each rule maps to one or more files under `tests/contract/`.
- **Tier ladder T0..T5.** `runaway tier check` reports which tier the install is currently running. Each tier has a machine-checkable promotion gate. `runaway tier promote --to TN --check` runs the gate without applying the transition. See [RUNAWAYCONTEXT.md §Tier Ladder](RUNAWAYCONTEXT.md#the-six-rung-ladder-t0t5).
- **Six-state maturation curve (HR-9, E3).** Lessons progress through `scar → active → stable → internalized → superseded → archived`. The engine writes `suggested_maturity` only; `Client.mature_lesson(id, to=...)` is the only path that updates `maturity`. Internalized lessons drop from briefs but stay queryable.
- **Three-axis severity (E4).** `blast_radius`, `frequency`, `reversibility` (each 1..5). Derived `critical/warning/info` view preserves back-compat with v2 `severity`.
- **Slug lifecycle (E5).** `alias_slug`, `deprecate_slug`, `merge_slugs` Client methods. The `slug_registry` and `slug_aliases` tables preserve history. Writes to deprecated or merged slugs are rerouted with audit log entries.
- **MCP server (E11).** Stdio transport, 13 tools (`get_brief`, `search_chunks`, `search_lessons`, `propose_lesson_draft`, `approve_draft`, `reject_draft`, `list_drafts`, `regen_brief`, `brief_preview`, `brief_rollback`, `mature_lesson`, `list_specialists`, `audit_verify`). Canonical surface reference at [docs/MCP.md](docs/MCP.md).
- **Trigger-based capture (E17).** `propose_lesson_draft` accepts a draft mid-conversation; the inbox sits in `lesson_drafts` until a human approves or rejects. Approval atomically inserts into `lessons_learned` and records the approver in the audit log.
- **Specialist agents (E15).** First-class concept with `specialists` and `specialist_knowledge` tables. CLI: `runaway specialists list/show/regen`. Each specialist owns a domain (e.g., `accounting`, `kiosks`, `monday`) and an auto-loaded brief.
- **Cross-system data map (E16, T2.5).** `data_sources` and `data_source_mappings` tables capture join keys across systems (Vista, Procore, Monday, OPC, FileMaker). Replaces the loose markdown `claude_database_map.md` pattern with structured data.
- **Audit log (HR-7, E22).** Append-only, hash-chained. `audit_log_no_update` and `audit_log_no_delete` triggers raise `ABORT` on any UPDATE/DELETE attempt. `runaway audit verify` recomputes the chain and reports the first broken row id if tampering is detected.
- **Brief preview + rollback (E20).** Diff a generated brief against the current file before writing; `brief_snapshots` stores every regen for rollback.
- **`runaway stats` CLI (E21).** Terminal-first dashboard equivalent. Shows maturation distribution, top contributors, retrieval health, drift hotspots, audit chain status.
- **Soft delete + record versioning (HR-3, E2).** No CLI / API / MCP path hard-deletes. `record_versions` archives the JSON payload of every overwrite. `runaway db hard-delete --i-understand-this-is-permanent --backup-first` is the single admin-only escape and is logged to audit.
- **Telemetry (HR-1, HR-8, E6).** `metrics.db` records local counters and timers. `metrics.emit()` is fire-and-forget — never blocks, never raises, even with locked / deleted / permission-denied DBs.
- **Predictive drift rules (E7).** Five rules including stack-overload, brief-near-cap, low-utilization-lesson, slug-orphan, and ineligible-mature-suggestion.
- **`runaway init` wizard + 7 work-type templates (E8).** `application`, `data-pipeline`, `automation`, `dashboard`, `research`, `infrastructure`, `general`. Generates initial Constitution, MEMORY.md, project briefs.
- **Python `Client` API (E10).** Full surface, 25+ methods documented at [docs/PYTHON_API.md](docs/PYTHON_API.md). Every public method has a docstring with explicit `Returns:`, `Raises:`, `Refuses:` sections (HR-14).
- **sqlite-vec sidecar + embedding provider abstraction (E12).** Default `local-onnx`; optional `openai`, `voyage`, `ollama` providers behind opt-in flags. `reindex_embeddings.py` backfills.
- **Hybrid scoring (E13).** Combines FTS5 BM25 with cosine similarity. Eval harness (E18) drives the weighting via 10 synthetic tasks.
- **Multi-project session stacking (E14).** `/runaway:project <slug>` switches the active stack without losing the prior session context.
- **Automated curation (E19).** Dedup, dead-lesson, and supersession suggestions feed the maturation engine; suggestions never auto-apply (HR-9).
- **Visibility ACL scaffolding (E24).** `private/team/org` columns and triggers present at every tier. Enforcement in retrieval activates at T4.
- **JSON export + import + conflict reporter (E23).** `runaway export-json` / `import-json` for T3 git-based team workflows.
- **`runaway audit verify` (E22).** Recomputes the hash chain end-to-end. Exit code 0 = chain intact; exit code 2 = chain broken at row N (reported).
- **Schema version table.** `schema_version` row records the applied migration; the Client refuses to start if it does not match.
- **Author identity opaque (HR-6).** `author_id = sha256(install_id + local_username)[:12]`. The schema's `CHECK (author_id NOT LIKE '%@%' AND author_id NOT LIKE '%.%')` rejects email-shaped values. `author_display` is opt-in and explicitly marked one-way.
- **Ten adopter specs (S1..S10).** `docs/specs/` directory: SSO_INTEGRATION, FEDERATION, OTLP_EXPORT, AIR_GAPPED_INSTALL, FINE_GRAINED_GRANTS, COMPLIANCE, MULTI_TENANT_ROLLOUT, IMPORTERS, DASHBOARD, CROSS_PLATFORM. Each contains a verbatim conformance clause: implementations that pass the contract tests are conforming; implementations that do not are not.
- **`INSTALL_PROMPT.md`.** The canonical prompt adopters paste to their AI. Includes the Adopter's AI Contract.
- **`RETRIEVAL.md`.** Paste-once T0 template for users who do not yet have a DB.
- **`docs/HARD_RULES.md`.** Duplicate of Part 0 of the plan with links to each rule's enforcer file.
- **`docs/ARCHITECTURE.md`.** Full mermaid diagram + principle-to-enforcement map.
- **`docs/MCP.md` + `docs/PYTHON_API.md`.** Canonical surface references.

### Changed

- **Schema migration is additive-only (HR-4).** No DROP COLUMN, no DROP TABLE, no incompatible type change. Migrator runs `PRAGMA table_info()` before and after each step and aborts (restoring from backup) if any v2 column is lost. See [MIGRATION_V2_TO_V3.md](MIGRATION_V2_TO_V3.md).
- **Tier 2 (Living Memory) remains pointer-only.** v2.0.1 closed the "optional detail files" loophole; v3 retains the closure and tests for it (predictive drift rule `living_memory_not_pointer_only`).
- **Tier 3 (Project Briefs) writer refuses overflow (HR-5).** `md_writer.write_brief()` counts lines pre-write and raises `BriefBudgetExceeded` if over cap. No silent truncation, no policy-only "stay under 150."
- **Severity is now a derived view.** `lessons_derived_severity` projects `blast_radius/frequency/reversibility` into the legacy `critical/warning/info` axis. The legacy `severity` column remains for back-compat (HR-4) but new writes set the three axes.
- **Project tagging enforcement deepened (HR-2).** SQLite triggers `chunks_require_tags_ins` and `ll_require_tags_ins` reject NULL / empty / `[]` project_tags at the SQL layer. The Client's `_guard_write()` validates each tag against `slug_registry` before INSERT.
- **CHANGELOG.md** (this file) now references HR-* rules where relevant — per HR-14, every public-surface change must cite the contract it relates to.

### Deprecated

- **Direct schema writes from external scripts.** All writes should go through the `Client` API (or the CLI / MCP, both of which wrap the Client). The triggers will still raise on bypass attempts but the path is no longer documented.
- **v2's policy-only line caps.** The v2 README said "≤200 lines" and "≤150 lines" as guidance. v3 enforces them in code. Hand-edited files over cap will trip the drift detector and the writer will refuse to regenerate over cap.
- **v2's hand-edited project briefs.** v2.0 deprecated these; v3 documents the deprecation more aggressively. The PRESERVE block survives regen; everything else is overwritten.

### Migration

For existing v2 users:

```bash
# 1. Back up first (the migrator also backs up — be paranoid)
cp ~/_knowledge/knowledge.db ~/_knowledge/knowledge.db.pre-v3.bak
cp ~/_knowledge/sessions.db  ~/_knowledge/sessions.db.pre-v3.bak

# 2. Install v3
pip install -e .

# 3. Run the migrator (additive, verified)
runaway migrate --from v2 --to v3 --verify

# 4. Verify
runaway tier check
runaway audit verify
pytest -m contract
```

The migrator (HR-4):

1. Backs up both `knowledge.db` and `sessions.db` to `*.pre-v3.bak`.
2. Records the v2 `PRAGMA table_info()` for each table.
3. Applies `schema/001_v3_additions.sql` (ADD COLUMN / CREATE TABLE / CREATE VIEW / CREATE INDEX only).
4. Applies the semantic sidecar (`003_semantic_sidecar.sql`) if `sqlite-vec` is installed.
5. Applies the metrics DB (`004_metrics_db.sql`).
6. Recomputes the v3 `PRAGMA table_info()` and asserts every v2 column is still present.
7. Updates `schema_version` to `3.0.0`.
8. Aborts and restores from backup if any v2 column count drops.

See [MIGRATION_V2_TO_V3.md](MIGRATION_V2_TO_V3.md) for the end-to-end walkthrough including rollback procedure.

### Why v3 exists

v2 moved discipline from policy to code. It worked for a year. The drift returned in subtler form:

- "Just this once" exceptions in AI-generated PRs.
- Stubbed-out `NotImplementedError` paths shipped because the test was "coming later."
- Silent `except Exception: pass` blocks in worker threads.
- A telemetry feature that quietly added a network call "for diagnostics."
- A maturation engine that started auto-archiving lessons because "the suggestion was obviously right."

Each of these is the failure mode v2 prevented at the file level but did not prevent at the *process* level. v3 closes those holes with **contracts**:

- Every rule has a named test (HR-12).
- Every PR adds tests for new behavior (HR-12).
- Every public symbol has a documented refusal path (HR-14).
- Every release-gate check fails on any `pending` plan item (HR-11).
- Every destructive admin path requires explicit flags and logs to audit (HR-3).
- Every network-capable module is allowlisted at the build (HR-1).

The discipline is no longer a culture; it is a build-failing test.

---

## [2.0.1] — 2026-05-06

Documentation tightening. v2.0.0 left a loophole that Tier 2 (Living Memory) could carry "optional detail files." Real-world use revealed this loophole is the same drift v2 was meant to prevent. v2.0.1 closes it. v3 preserves the closure.

### Changed

- **Tier 2 spec is now pointer-only.** MEMORY.md holds one line per entry — a pointer (`LL#N` / `KS#N` / `rule#N`) plus a short hook.
- **BOOTSTRAP.md, RUNAWAYCONTEXT.md, run_RunawayContext.md** updated to reflect the pointer-only spec.

### No code changes

This was a documentation-only release. The schema, CLI write guards, drift detector, and v1→v2 migrator are unchanged from 2.0.0.

---

## [2.0.0] — 2026-05-04

Major architectural upgrade from v1. Policy-only constraints became code-enforced. See `RunawayContext_v2/CHANGELOG.md` for the full entry; the highlights are preserved here for continuity:

- **Added:** `project_context_card`, auto-generated Tier 3, required project tagging on every write, drift detector, split-DB design (`knowledge.db` + `sessions.db`), lessons-learned lifecycle (`severity` / `status` / `superseded_by`), junction tables, multi-user setup helper.
- **Changed:** README, BOOTSTRAP, RUNAWAYCONTEXT, run_RunawayContext for v2.
- **Deprecated:** hand-edited Tier 3 files, "Level 1: organized markdown files" Knowledge Store, v1 `lessons_learned.lesson` and `.context` columns (preserved for back-compat).
- **Migration:** `lib/migrate_v1_to_v2.py` from v1 single-DB to v2 split-DB.

---

## [1.1.0] — 2026-04-07

Project renamed from **SuperContext** to **RunawayContext** to avoid conflicts. AI-powered session summary safeguards added (batch limits, processed marker, attempt cap, lock file, no-retry rule).

---

## [1.0.0] — 2026-04-03

Initial release as **SuperContext** (renamed four days later). 4-tier knowledge architecture, session memory layer, tool-specific setup guides, BOOTSTRAP, RUNAWAYCONTEXT, run_RunawayContext, MIT license.
