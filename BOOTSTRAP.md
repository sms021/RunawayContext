# Bootstrap — The Per-Tier Walkthrough (T0..T5)

This is the procedural guide. For each tier, you will see: what to install, what files to create, what commands to run, what to expect when it works, and what the promotion gate to the next tier looks like.

If you want to skip the walkthrough and have your AI do everything, see [INSTALL_PROMPT.md](INSTALL_PROMPT.md). If you want to understand what is being built and why, this is the file.

The full theory and reference is in [RUNAWAYCONTEXT.md](RUNAWAYCONTEXT.md). The hard rules charter is in [docs/HARD_RULES.md](docs/HARD_RULES.md). The Client API is in [docs/PYTHON_API.md](docs/PYTHON_API.md). The MCP surface is in [docs/MCP.md](docs/MCP.md).

---

## Before You Start

You need:
- Python ≥ 3.8 on the install machine.
- SQLite ≥ 3.31 (FTS5 included).
- Read+write access to a working directory for `knowledge.db`, `sessions.db`, and config.
- An AI coding assistant (Claude Code, Cursor, Copilot, Codex, Aider, Windsurf, or anything that can read markdown).

You do **not** need:
- Network access for the default install (HR-1).
- A vector DB service (sqlite-vec is local).
- An SSO provider (T1..T4 work without it).
- Docker (except for the clean-install test in CI — your local install is just Python).

---

## Tier 0 — Hello World

**Who:** anyone with an AI assistant and a filesystem. No database. No persistent memory across machines.

**What it gives you:**
- Work-type templates that get your project's first `CLAUDE.md` / `.cursorrules` / `AGENTS.md` to a reasonable starting state.
- A paste-once retrieval template ([RETRIEVAL.md](RETRIEVAL.md)) you put in your project root and your AI references in its system prompt.

**Install:**

```bash
# 1. Clone (or download) this repo
git clone https://github.com/sms021/RunawayContext.git ~/RunawayContext_v3

# 2. Copy the retrieval template to your project root
cp ~/RunawayContext_v3/RETRIEVAL.md /path/to/your/project/

# 3. Pick a work-type template
ls ~/RunawayContext_v3/templates/
# application, data-pipeline, automation, dashboard, research, infrastructure, general

# 4. Copy the template's CLAUDE.md (or equivalent) into your project root
cp ~/RunawayContext_v3/templates/application/CLAUDE.md /path/to/your/project/
```

**What's on:** templates + RETRIEVAL.md.
**What's off:** everything else.
**Network egress:** zero bytes.
**Resource budget:** ~50 KB disk; 0 MB RAM (nothing runs in the background).

**Promotion gate to T1:** you have accumulated 5+ project-specific notes manually (in CLAUDE.md, in scratchpad files, in your AI's session memory). At that point a database starts paying for itself.

**Rollback:** delete the markdown files. Done.

---

## Tier 1 — Solo

**Who:** single developer on one machine, ready to put memory in a database.

**What it gives you (on top of T0):**
- `knowledge.db` (curated, small, frequently backed up).
- `sessions.db` (heavy transcripts, retained longer).
- Slug registry — every write is project-tagged at the boundary (HR-2).
- Auto-generated project briefs at ≤150 lines (HR-5).
- Drift detector (stop hook + cron watcher).
- Hand-edited Tier 1 (Constitution) and Tier 2 (Living Memory, pointer-only).
- All v2 surface preserved (HR-4).

**Install:**

```bash
# 1. Install the Python package
cd ~/RunawayContext_v3
pip install -e .

# 2. Run the init wizard
runaway init
# Asks: install location? work type? main projects?

# 3. Apply the base schema (and the additive v3 layer if migrating)
runaway db apply --schema schema/000_knowledge_db.sql
runaway db apply --schema schema/002_sessions_db.sql
runaway db apply --schema schema/001_v3_additions.sql

# 4. Register your project slugs
runaway slug register --slug accounting --description "Construction-accounting tools"
runaway slug register --slug procore     --description "Procore integrations"
# etc.

# 5. Create your Constitution (Tier 1)
$EDITOR ~/.claude/CLAUDE.md       # or AGENTS.md / .cursor/rules / etc.
# Keep ≤200 lines.

# 6. Create the Tier 2 pointer file
$EDITOR ~/.claude/memory/MEMORY.md
# Keep ≤50 lines. Each line is one pointer: LL#N — short hook
# See "Pointer-MD contract" below — MEMORY.md and its siblings hold
# pointers only; detail content lives in knowledge.db.

# 7. Wire up the drift detector
ln -s ~/RunawayContext_v3/bin/check_md_drift.sh ~/.claude/hooks/Stop/check_md_drift.sh
# Or, for VS Code and other Stop-hook-less tools:
crontab -l | grep md_drift_watcher  # add a */10 entry
```

**Expected outputs after init:**
```
$ runaway tier check
Current tier: T1
Next tier: T2
Requirements for T2:
  - 30+ days of operation     [pending: 0 days]
  - ≥10 lessons across ≥2 projects [pending: 0 lessons]
  - ≥1 drift warning logged   [pending: 0 warnings]
```

**Network egress:** zero bytes.
**Resource budget:** ~80 MB RAM during CLI; ~150 MB disk steady-state.

**Promotion gate to T2:** see above — the system tells you the exact requirements.

**Rollback:** disable MCP, telemetry, semantic via config. T1 capabilities remain unchanged. The schema columns for T2+ remain in the DB per HR-4 but unused.

---

## Tier 2 — Solo Power (the most common target)

**Who:** active multi-project developer who wants the full RunawayContext surface.

**What it gives you (on top of T1):**
- MCP server (13 tools, stdio transport). See [docs/MCP.md](docs/MCP.md).
- Local telemetry (`metrics.db`) — fire-and-forget (HR-8).
- Soft-delete + record versioning (HR-3).
- Predictive drift rules (5 rules).
- Multi-project session stacking + `/runaway:project` switch.
- Six-state maturation curve (HR-9) + suggestion engine.
- Three-axis severity (blast_radius / frequency / reversibility).
- Slug lifecycle (alias / deprecate / merge).
- Brief preview + rollback.
- Trigger-based capture (`propose_lesson_draft`).
- Specialist agents.
- Cross-system data map (`data_sources` + `data_source_mappings`).
- Optional semantic retrieval (local ONNX or sqlite-vec).
- `runaway stats` CLI.

**Install:**

```bash
# 1. Promote to T2
runaway tier promote --to T2

# 2. (Optional) Add semantic retrieval
pip install -e '.[semantic]'
runaway db apply --schema schema/003_semantic_sidecar.sql
runaway embeddings backfill --provider local-onnx

# 3. Apply the metrics DB (T2 telemetry)
runaway db apply --schema schema/004_metrics_db.sql

# 4. Register the MCP server with your AI tool
runaway mcp register --tool claude-code
# or
runaway mcp register --tool cursor

# 5. Verify
runaway tier check       # should now say T2
runaway mcp serve --once # verifies the server starts cleanly
runaway stats            # shows initial state
```

**Wiring MCP to Claude Code:**

```bash
# Option A: skill-based (auto-loaded)
cp -r ~/RunawayContext_v3/skills/runaway-context ~/.claude/skills/

# Option B: explicit MCP server config
$EDITOR ~/.claude/mcp_servers.json
# Add an entry pointing at: runaway mcp serve
```

**Wiring MCP to Cursor:**

```bash
# Copy the cursor rule
cp ~/RunawayContext_v3/.cursor/rules/runaway-retrieval.md /path/to/your/project/.cursor/rules/

# Add MCP server to your cursor settings
# Cursor reads MCP servers from .cursor/mcp.json or settings — see docs/MCP.md
```

**Expected outputs:**
```
$ runaway tier check
Current tier: T2
Next tier: T3
Requirements for T3:
  - second author_id with an approved lesson in last 30 days [pending: 1 author]

$ runaway stats
== RunawayContext stats ==
Tier: T2  · Schema: 3.0.0  · Audit chain: intact (4831 rows)
Lessons: 487 active | 32 stable | 14 internalized | 8 superseded | 3 archived | 2 scar
Chunks: 1,203 | Drafts pending: 2
Briefs:  18 projects | 0 over cap | 14 within 90% of cap
Drift:   1 warning (last 7d) — see `runaway drift list`
Retrieval health (last eval): MRR@5 = 0.81
Telemetry rows (last 7d): 142 ops, p95=4ms
Network egress: disabled (HR-1)
```

**Network egress:** zero bytes default. Opt-in (remote embeddings) requires explicit config flag + per-request egress logging.

**Resource budget:** ~150 MB RAM (with MCP + embedding model resident); ~250 MB disk.

**Promotion gate to T3:** a second `author_id` has logged at least one approved lesson in the last 30 days. Until then, T3 features remain inert (schema columns are present but the code paths refuse).

**Rollback to T1:** `runaway tier demote --to T1`. MCP, telemetry, and semantic are disabled in config; schema columns remain (HR-4).

---

## Tier 3 — Pair / Squad

**Who:** 2..5 collaborators.

**What it gives you (on top of T2):**
- Author attribution on every write (HR-6: opaque `author_id`).
- Git-based JSON export/import.
- Conflict reporter on import.
- Opt-in `author_display` (one-way mapping; never reverses to email).

**Install:**

```bash
# 1. Promote
runaway tier promote --to T3 --check
runaway tier promote --to T3

# 2. Initialize the shared knowledge repo
mkdir ~/team_knowledge
cd ~/team_knowledge
git init
runaway export-json --path .

# 3. Push to your team's remote (each team member clones it)
git remote add origin git@github.com:yourteam/team_knowledge.git
git push -u origin main

# 4. Each team member pulls and imports
git pull
runaway import-json --path . --conflict-strategy report
# Review conflicts:
runaway conflicts list
runaway conflicts resolve --id N --strategy <keep_local|take_remote|merge>
```

**Expected after import:**
- Each team member's writes carry their opaque `author_id`.
- The knowledge repo is the *exchange format*, not the live store. The live store is each person's local `knowledge.db`.
- `runaway stats --by-author` shows contributions.

**Network egress:** zero. The git remote is the only network — and git is operated by the user, not by RunawayContext.

**Promotion gate to T4:** team has resolved ≥5 import conflicts via the documented workflow AND has designated ≥1 `runaway_admin` AND has 30 days of operation under T3.

**Rollback to T2:** drop the knowledge-repo + revert config. Lessons exported to the team repo remain in the local DB (no data loss — HR-3).

---

## Tier 4 — Team

**Who:** 5..20 users with an established review process.

**What it gives you (on top of T3):**
- Visibility ACLs enforced in retrieval (`private/team/org`).
- Promotion gate for new contributors (30-day probationary visibility).
- Multi-tenant rollout helper (each user's install gets the same canonical slug list).
- Garbage-tagger detection (alerts on contributors whose drafts are repeatedly rejected).
- Audit log clean-period requirement (30 consecutive days of `runaway audit verify` passing).
- Knowledge-repo CI templates (pre-merge: run `pytest -m contract`).

**Install:**

```bash
# 1. Confirm promotion gate
runaway tier promote --to T4 --check
runaway tier promote --to T4

# 2. Configure the visibility default per slug
runaway slug visibility --slug accounting --default team
runaway slug visibility --slug experiments --default private

# 3. Provision the canonical slug list across users
# See docs/specs/MULTI_TENANT_ROLLOUT.md — adopter's AI builds this against
# the local OS / user-management stack.

# 4. Add the contract-test CI hook to the team knowledge repo
# See docs/specs/MULTI_TENANT_ROLLOUT.md for the GitHub Actions / GitLab CI template.

# 5. Configure probationary visibility
runaway acl probation --days 30
```

**Network egress:** zero by default. The audit verifier and SSO bindings (S1) are still opt-in / spec-only — you decide whether to wire them.

**Promotion gate to T5:** SSO provider configured AND federation source identified AND audit log verified clean for 30 consecutive days.

**Rollback to T3:** visibility ACLs become advisory (filtered out of retrieval but still in the DB).

---

## Tier 5 — Org / Enterprise

**Who:** 20+ users across teams.

**What it gives you (on top of T4):**
- Federation (read-only upstream sources) — see [docs/specs/FEDERATION.md](docs/specs/FEDERATION.md).
- SSO / identity bindings — see [docs/specs/SSO_INTEGRATION.md](docs/specs/SSO_INTEGRATION.md).
- OpenTelemetry export (opt-in) — see [docs/specs/OTLP_EXPORT.md](docs/specs/OTLP_EXPORT.md).
- Fine-grained grants — see [docs/specs/FINE_GRAINED_GRANTS.md](docs/specs/FINE_GRAINED_GRANTS.md).
- SLO instrumentation.

T5 is the level where most of the *spec-only* artifacts (S1..S6) become live. **Your AI builds the integration; our contract tests verify it conforms.** We do not ship vendor-specific SSO code, federation refresh workers, OTLP collectors, or compliance bindings — those are all the adopter's responsibility, per the AI-native OSS model.

**Install (the spec-driven path):**

```bash
# 1. Read each spec relevant to your environment
ls docs/specs/

# 2. For each spec, paste the spec contents to your AI and ask:
#    "Build the integration described in this spec for our stack. The
#     contract tests at tests/spec/<spec_name>/ must pass. Do not
#     interpret loosely."

# 3. The AI builds the bindings against your local SSO / federation / OTLP.

# 4. Run the spec's contract tests
pytest tests/spec/sso_integration -v
pytest tests/spec/federation -v
# etc.

# 5. Once all relevant spec tests pass, promote to T5
runaway tier promote --to T5 --check
runaway tier promote --to T5
```

**Network egress:** opt-in only. If federation is configured, the federation refresh worker reaches the configured upstream(s). If OTLP is configured, the exporter reaches the configured collector. Both are logged on every emission. Default install (no S1..S6 wired) remains zero-egress (HR-1).

**Promotion gate:** none. T5 is the top.

**Rollback to T4:** federation sources stay in the DB but refresh stops; SSO bindings stay but provider integration is disabled. No data lost.

---

## Per-Tier File Inventory

| Tier | Files added | Files always-loaded by AI |
|---|---|---|
| **T0** | `RETRIEVAL.md`, templates/* | Just the template's CLAUDE.md |
| **T1** | `knowledge.db`, `sessions.db`, slug registry, drift hooks | Constitution + MEMORY.md + per-project briefs (on entry) |
| **T2** | `metrics.db`, optional `semantic.db`, MCP server config, specialist briefs | Same as T1 + brief specialist headers |
| **T3** | `team_knowledge/` git repo | Same as T2 |
| **T4** | Visibility-enforced retrieval; audit log requirement | Same |
| **T5** | Federation sources, identity bindings, OTLP config | Same |

Always-loaded volume scales linearly with project count, never with corpus size. The G1 guarantee (≤3K tokens always-loaded) is a function of T1+T2 caps; it does not change at higher tiers.

---

## Verifying the Install

At every tier, run:

```bash
# All hard rules pass
pytest -m contract -v

# Tier reports honestly
runaway tier check

# Audit log starts clean
runaway audit verify

# Network behavior is local-only (default)
runaway config show --network
# Expected: "network egress: disabled (HR-1)"
```

If any of these fail, the tier is not honored. The AI's install report at that point should say what failed and what was tried — not "everything is working."

---

## Pointer-MD contract (Tier 2 + every per-project memory dir)

**Rule:** every `MEMORY.md` and every sibling MD inside a `~/.claude/projects/<proj>/memory/` directory holds **pointers only** — no detail content. Detail lives in `knowledge.db`.

A pointer index line looks like:

```
- LL#467 — Editing config.php rewrites the ACL mask; chmod 640 + chgrp www-data + setfacl
- rule#15665 — AHJ writers must use eh_ctx_patch(), never UPDATE resolved_context directly
- KC#15689 — AHJ orchestrator BG worker pattern (setsid + 8-phase pipeline)
```

A pointer **stub file** (a sibling MD that was once a Claude Code auto-memory file and has been ingested) looks like:

```yaml
---
name: no-mocks-in-integration-tests
description: don't mock the DB in integration tests
metadata:
  type: pointer
  db_table: lessons_learned
  db_row_id: 467
---
See knowledge.db `lessons_learned` row 467.
```

**How to keep the contract:**

- Sibling MDs that Claude Code's auto-memory subsystem writes (`feedback_*.md`, `project_*.md`, `reference_*.md`, `user_*.md`) get ingested with `runaway memory ingest`. The importer rewrites each one as a pointer stub; the body is now in the DB.
- The doctor check `MEMORY_ORPHANS` flags any non-pointer sibling. Run `runaway memory ingest --dry-run` to preview, then drop `--dry-run` to apply.
- `MEMORY.md` itself is regenerated from the DB with `runaway brief rewrite-pointers`. The command refuses to touch a hand-edited file (HR-5 no-clobber — looks for an `AUTO-GENERATED` marker in the first 256 bytes).
- Detail edits happen in the DB via `runaway log-lesson` / `propose-knowledge` / the MCP `propose_lesson_draft` flow. Never hand-edit pointer stubs to add content — the edit is invisible to search and the next ingest pass would overwrite it.

**Why it matters:** without the contract, content lives in two places (file + DB) and drifts. Search returns one version, the brief generator another. The contract makes the DB authoritative and the files visible-but-thin pointers.

---

## Keeping It Alive (operational discipline)

After install:

1. **Run `runaway audit verify` at least weekly.** If the chain breaks, investigate immediately — it means something or someone wrote to the audit log outside the framework.
2. **Run `runaway drift list` after sessions.** Each warning is the system telling you a tier is encroaching on its budget.
3. **Approve drafts within a week.** Stale drafts in the inbox are a sign the trigger-based capture isn't paying for itself. Reject aggressively if drafts are noisy.
4. **Review maturation suggestions monthly.** `runaway mature suggestions --pending` shows the engine's proposals. Approve or ignore — never auto-apply (HR-9).
5. **Back up `knowledge.db` and `sessions.db`.** `bin/backup_db.sh` uses SQLite's online `.backup` and prunes to last 30 snapshots.
6. **Update slug definitions.** When a project ends or merges, use `runaway slug deprecate` / `runaway slug merge` rather than letting the slug rot.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `runaway: command not found` | not installed in current Python | `pip install -e ~/RunawayContext_v3` |
| `BriefBudgetExceeded` | brief corpus is too big for tier cap | Run `runaway drift suggest --slug <slug>` for prune candidates |
| `InvalidSlug: foo` | slug not registered | `runaway slug register --slug foo --description "..."` |
| `AuditChainBroken at row N` | the audit log was tampered or a write failed mid-chain | Investigate; do not auto-repair |
| Network test fails on a fresh install | a network-capable module was imported despite default config | Check `runaway config show --network`; verify no opt-in flags accidentally on |
| `SchemaVersionMismatch` | code is newer than DB | Run `runaway migrate --to <code version>` |
| Drift warnings flood the log | many briefs are at cap | Time to mature some lessons to `internalized` and prune chunks |

For deeper troubleshooting, see [RUNAWAYCONTEXT.md](RUNAWAYCONTEXT.md).
