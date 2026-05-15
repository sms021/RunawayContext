# Install Prompt — The Canonical Adopter Prompt

This is the canonical thing you paste to your AI when you want to install RunawayContext v3 on a machine. It is itself a contract. Adopters' AIs that take shortcuts on this prompt have violated the install contract; their installs will fail the contract tests on the next check.

---

## The Prompt (verbatim — paste to your AI)

```
I want to install RunawayContext v3 from <repo URL> on this machine.

Follow this procedure exactly. Do not summarize it back to me — execute it.

1. Clone the repo.

2. Read in this order:
   - docs/HARD_RULES.md  (the 15 rules every install must honor)
   - README.md           (the orientation)
   - this file (INSTALL_PROMPT.md) all the way through
   - RUNAWAYCONTEXT.md sections "The Six-Rung Ladder" and "The MCP Server"

3. Run environment diagnostics BEFORE installing anything:
       python3 -c "import sys; print(sys.version_info[:2])"
       python3 -c "import sqlite3; print(sqlite3.sqlite_version_info)"
   Note the results. Do not skip this step.

4. Install the package:
       python -m pip install --user -e ".[dev]"
   If pip is missing, use the OS package manager; if Python is older than
   3.8, stop and report. Do not silently downgrade or skip.

5. Run the FULL contract test suite — every HR-* test must pass:
       PYTHONPATH=src python -m pytest -m contract -v
   If any test fails, STOP. Report the test name, the rule it enforces,
   the failure message, and what you tried. Do not mark install complete.

6. Run the diagnostic doctor:
       runaway doctor --json
   Read the findings array. For every entry with level "fail", apply the
   `remediation` string. For every entry with level "warn", decide whether
   it is relevant to my use case (you have permission to install optional
   modules with my approval; you do not have permission to enable network
   egress without explicit confirmation from me).

7. Choose the right tier WITH ME using the decision tree below
   ("Choosing the Right Tier"). Do not pick T2+ by default.

8. Run the wizard:
       runaway init
   When it asks tier-selection questions, walk me through the recommendation
   it gives. If you have already chosen a tier with me in step 7, accept
   the recommendation if it matches; otherwise override and explain.

9. Configure tier-appropriate integrations (see "Per-Tier Integration
   Checklist" below). Do not enable features for a tier higher than mine.

10. Re-run `runaway doctor` once everything is wired up. Confirm zero
    FAIL findings before declaring the install complete. WARN findings
    are acceptable if I have explicitly accepted them.

11. Report: my chosen tier, the next promotion gate, and any WARN findings
    I should know about.

Hard rules of the install:
- Never weaken or work around an HR-* rule.
- If a contract test fails, STOP and report — do not push forward.
- Never enable a network-capable module without my explicit confirmation.
- Never add TODO/FIXME comments to "come back later" — the v3 plan calls
  this out as a closed loophole (HR-13).
- If the install can't complete, leave it incomplete and tell me — do not
  fake completion.
```

That is the prompt. Copy it. Paste it. Replace `<repo URL>` with the actual repository URL. Send it to your AI.

---

## Already have RunawayContext v1 on this machine?

v1 was the single-file release (everything — knowledge + transcripts — in one `~/_knowledge/sessions.db`). The v3 migrator detects v1 directly; no v1→v2→v3 detour is needed.

1. **Detection.** `runaway doctor --install-dir ~/_knowledge --json` emits:
   ```
   [V1_DB_UNUPGRADED] v1 single-file install detected at ~/_knowledge/knowledge.db
                      — knowledge + transcripts in one DB
       → Run `runaway db migrate` to upgrade in place. The migrator auto-detects
         v1, copies transcripts into a new sessions.db (non-destructive —
         original file kept), then applies the v3 additive layer. HR-4 guarantees
         no rows lost.
   ```

2. **What the migrator does on a v1 file:**
   - Snapshots the original DB.
   - Creates `sessions.db` and the v3 `session_logs` table.
   - Copies every row from v1's `sessions` table into `session_logs` (preserving conversation_id; synthesizing one if v1 used integer rowids).
   - **Leaves v1's `sessions` table in place** in the original file (HR-4 — no DROP).
   - Applies the v3 additive layer to the original file: ADD COLUMN for the v3-new lessons/chunks columns, CREATE TABLE IF NOT EXISTS for new v3 tables (`slug_registry`, `audit_log`, `lesson_drafts`, `brief_snapshots`, `specialists`, `data_sources`, `record_versions`, `authors`, `visibility_levels`, `maturity_states`).
   - Bumps `schema_version` to `(3, 0, 0)`.

3. **Concrete v1-upgrade prompt** (drop-in alternative):

   ```
   I have RunawayContext v1 installed at ~/_knowledge on this machine (one
   .db file with everything in it). Upgrade to v3 from <repo URL>.

   1. Clone v3 to a fresh location.
   2. cd into the v3 checkout and `pip install --user -e ".[dev]"`.
   3. Read docs/HARD_RULES.md and MIGRATION_V2_TO_V3.md (the v1 path is
      covered there too).
   4. Run `runaway doctor --install-dir ~/_knowledge --json`.
      You should see [V1_DB_UNUPGRADED] as one FAIL. Expected.
   5. Run `runaway db migrate --knowledge-db ~/_knowledge/knowledge.db`.
      (You can omit --sessions-db; the migrator places sessions.db next to it.)
      Confirm the report contains `v1_split:copied_N_session_rows` in
      steps_applied, then the rest of the additive schema steps.
   6. Re-run `runaway doctor`. V1_DB_UNUPGRADED must be gone.
   7. Run `pytest -m contract` from the v3 checkout.
   8. Report: row counts before/after for knowledge_chunks, lessons_learned,
      and the new session_logs in sessions.db.
   ```

After v1 upgrade, the install behaves exactly like a v2 upgrade — same tier recommender, same opt-in policy for MCP / telemetry / semantic.

---

## Already have RunawayContext v2 on this machine?

The install prompt above works for v2 upgrades too. Here's what changes under the hood:

1. **Detection.** At step 8 (`runaway init`), the wizard probes the install dir for v2 tables (`knowledge_chunks`, `lessons_learned`) and a missing `schema_version` row. If both conditions hold, it announces:

   ```
   v2 install detected at ~/_knowledge
     - knowledge_chunks: 142 row(s)
     - lessons_learned:  87 row(s)
   The v3 migrator is non-destructive (HR-4): existing rows and columns
   are preserved; new v3 tables/columns are added.
   Upgrade this v2 install in place to v3? [Y/n]:
   ```

2. **Doctor signal.** Before that, `runaway doctor` will emit a single specific FAIL:

   ```
   [V2_DB_UNUPGRADED] v2 install detected at ~/_knowledge/knowledge.db — schema_version row missing
       → Run `runaway db migrate` to upgrade in place. HR-4 guarantees this is
         non-destructive: every v2 row and column is preserved; only new v3
         columns/tables are added.
   ```

   The AI walks this remediation automatically — it routes to `runaway db migrate`, which:

   - Snapshots the existing DB to `knowledge.db.pre-v3.bak` first.
   - Applies the additive v3 schema (every `ALTER` is `ADD COLUMN`; every `CREATE` is `IF NOT EXISTS`).
   - Verifies row counts and column lists before/after. If anything dropped, it aborts and restores from the backup.
   - Bumps `schema_version` to `(3, 0, 0)`.

3. **Tier picking.** For an existing v2 user, the recommender usually lands at T2 (active multi-project solo) — but the wizard still walks the decision tree because team composition may have changed since v2 was installed.

4. **What v3 keeps from your v2 install**

   | v2 surface | v3 status |
   |---|---|
   | `knowledge_chunks`, `lessons_learned` | Preserved + new columns added |
   | `project_context_card` | Preserved + new columns added |
   | `lesson_chunks`, `chunk_sessions` junctions | Preserved unchanged |
   | `session_logs` in sessions.db | Preserved unchanged |
   | v2 `severity` column | Preserved (write-side back-compat); the new three-axis severity coexists |
   | v2 `status` column on lessons | Preserved; the new six-state `maturity` column adds the modern lifecycle |
   | v1 `lesson` / `context` columns | Still preserved (back-compat all the way to v1) |
   | Hand-edited Constitution / MEMORY.md | Untouched (your file, your control) |
   | Auto-generated Tier 3 briefs | Re-rendered to include new sections; the PRESERVE block survives |

5. **What's new but inert until you opt in.** MCP, semantic retrieval, telemetry, audit log, specialist agents, cross-system map, visibility ACLs — these v3 capabilities are present in the schema but **off by default** for a v2 upgrader. Enable them by promoting to T2+ (or by editing `~/_knowledge/config.json`).

See [MIGRATION_V2_TO_V3.md](MIGRATION_V2_TO_V3.md) for the full upgrade walkthrough.

**Concrete v2-upgrade prompt** (drop-in alternative to the canonical prompt above):

```
I have RunawayContext v2 installed at ~/_knowledge on this machine. I want
to upgrade to v3 from <repo URL>.

1. Clone v3 to a fresh location (do NOT overwrite my v2 install).
2. cd into the v3 checkout and `pip install --user -e ".[dev]"`.
3. Read docs/HARD_RULES.md and MIGRATION_V2_TO_V3.md.
4. Run `runaway doctor --install-dir ~/_knowledge --json`.
   You should see [V2_DB_UNUPGRADED] as one FAIL finding. That is expected.
5. Run `runaway db migrate --knowledge-db ~/_knowledge/knowledge.db \
       --sessions-db ~/_knowledge/sessions.db \
       --metrics-db ~/_knowledge/metrics.db`.
   Confirm the migrator reports `aborted_reason: None` and shows the new
   columns it added per table.
6. Re-run `runaway doctor`. The V2_DB_UNUPGRADED finding must be gone.
7. Run `runaway init --non-interactive` so the wizard registers my opaque
   install_id and writes a minimal config.json (no overwrites).
   Then run `runaway init` interactively only if I tell you to walk the
   tier-recommendation flow with me.
8. Run `pytest -m contract` from the v3 checkout to verify the contracts hold.
9. Report: row counts before/after for knowledge_chunks and lessons_learned,
   the doctor's current state, and the recommended tier.
```

---

## Choosing the Right Tier

Tiers are progressive (T0 → T5). Each adds capability AND a small bit of setup. Don't over-pick — promotion is one command (`runaway tier promote --to T<n>`) once your situation outgrows the current rung.

### The decision tree

```
              ┌───────────────────────────────────┐
              │ How many people will write to     │
              │ this install? (humans, not bots)  │
              └───────────────────────────────────┘
                         │
            ┌────────────┼─────────────┐
            ▼            ▼             ▼
        1 person      2–5         5+ people
            │            │             │
            ▼            ▼             ▼
   ┌────────────────┐  ┌─────┐   ┌──────────────────┐
   │ Have you logged│  │ T3  │   │ Do you have an   │
   │  any lessons   │  │     │   │ established      │
   │     yet?       │  └─────┘   │ review process?  │
   └────────────────┘            └──────────────────┘
       │       │                       │       │
      NO      YES                     NO      YES
       │       │                       │       │
       ▼       ▼                       ▼       ▼
   ┌─────┐  ┌──────────────────┐    ┌─────┐  ┌──────────────────┐
   │ T0  │  │ Multiple projects?│    │ T3  │  │ Do you have SSO  │
   │ md  │  └──────────────────┘    └─────┘  │ + 20+ users?     │
   │only │     │            │                └──────────────────┘
   └─────┘    NO          YES                    │       │
              │             │                   NO      YES
              ▼             ▼                    │       │
            ┌─────┐      ┌─────┐                 ▼       ▼
            │ T1  │      │ T2  │              ┌─────┐  ┌─────┐
            │solo │      │solo-│              │ T4  │  │ T5  │
            └─────┘      │pwr  │              │team │  │ org │
                         └─────┘              └─────┘  └─────┘
```

### Plain-English version

| Situation | Tier | Why |
|---|---|---|
| "I haven't logged anything; I just want a paste-once template" | **T0** | Markdown only, no DB, no install footprint |
| "I'm solo on one project" | **T1** | Full v2 surface: DB, FTS5, drift detector, write guards |
| "I'm solo across multiple projects; I want my AI to use this via MCP" | **T2** | T1 + MCP + telemetry + semantic + specialists + multi-project stacking |
| "There are 2–5 of us collaborating" | **T3** | T2 + author attribution + JSON export/import + conflict reporter |
| "There are 5–20 of us with a review process" | **T4** | T3 + visibility ACLs + audit log + governance |
| "We're an org of 20+ with SSO" | **T5** | T4 + federation + SSO bindings + OTLP export |

### Don't over-pick

- T2 is the most common starting point for an active solo developer. **Most adopters belong at T1 or T2.**
- T3+ require a second human author writing to the install. Picking T3 solo gives you no benefit; everything T3 unlocks (attribution, conflict reporter) needs >=2 author_ids.
- T4 requires a review process. Picking T4 without one means the visibility ACL stays dormant.
- T5 requires SSO + federation. Without those, T5 is just T4 with extra documentation.

The init wizard walks the decision tree automatically — your AI can answer on your behalf and explain its reasoning.

---

## Per-Tier Integration Checklist

When your AI completes step 9 of the prompt, it walks this checklist for your tier.

### T0 — Hello World

- [ ] Copy `RETRIEVAL.md` from the repo into your project root.
- [ ] Reference it from your AI tool's instructions file (CLAUDE.md, .cursorrules, AGENTS.md).
- [ ] Done. No DB, no install footprint.

### T1 — Solo

- [ ] `runaway db migrate` has applied the schema.
- [ ] At least one canonical project slug is registered (`runaway slug register <slug>`).
- [ ] At least one work-type template has been copied or referenced (templates/ in the repo).
- [ ] Drift hook wired (see "Drift Hook Wiring" below).
- [ ] `runaway doctor` reports zero FAIL findings.

### T2 — Solo Power

Everything from T1, plus:

- [ ] MCP server enabled in config (`mcp_enabled=True`).
- [ ] AI client wired to the MCP server (see "MCP Wiring" below).
- [ ] Telemetry enabled (`telemetry_enabled=True`) — local-only, never network.
- [ ] (Optional) semantic provider chosen and `Config.embeddings_enabled=True`. Default is the local hash-based reference provider; adopters who want real embeddings install sentence-transformers or another conforming Provider implementation.
- [ ] At least one specialist agent registered if your workflow has distinct knowledge domains.
- [ ] Cross-system data sources registered if you integrate with external systems.
- [ ] `runaway stats` shows non-zero counts.

### T3 — Pair / Squad

Everything from T2, plus:

- [ ] A second `author_id` has logged at least one approved lesson.
- [ ] A knowledge-repo (git) is set up for JSON export/import (`runaway export` / `runaway import`).
- [ ] Conflict reporter workflow documented in your team's README.

### T4 — Team

Everything from T3, plus:

- [ ] At least one `runaway_admin` designated (`UPDATE authors SET is_admin = 1 WHERE author_id = '...'`).
- [ ] Visibility ACLs in use (`runaway set-visibility --table ... --id ... --level team`).
- [ ] Audit log verified clean for 30 consecutive days (`runaway audit verify`).
- [ ] Multi-tenant rollout per `docs/specs/MULTI_TENANT_ROLLOUT.md`.

### T5 — Org / Enterprise

Everything from T4, plus:

- [ ] SSO integration per `docs/specs/SSO_INTEGRATION.md`. **Your AI builds this against the spec — we ship the contract, not the code.**
- [ ] Federation per `docs/specs/FEDERATION.md`.
- [ ] OTLP export per `docs/specs/OTLP_EXPORT.md` if you want to ship metrics to your existing observability stack.

---

## Drift Hook Wiring

The drift detector watches always-loaded files for HR-5 violations. There are two wiring options:

### Claude Code (CLI or VS Code)

Add `bin/check_md_drift.sh` to your Stop hook configuration. The path is in `~/.claude/settings.json`.

### Other tools (Cursor, Aider, Windsurf, Codex CLI)

Schedule `bin/md_drift_watcher.sh` via cron:

```cron
*/10 * * * * /path/to/RunawayContext_v3/bin/md_drift_watcher.sh
```

Or your platform's launchd / systemd timer equivalent.

---

## Session Capture (v3.1.0+)

Every Claude conversation is captured into `sessions.db.session_logs` so prior work is queryable. There are two complementary wiring options — `runaway doctor --fix-hook` installs option A; option B is opt-in.

**A. Stop hook (CLI Claude Code, default).** `runaway doctor --fix-hook` appends `bin/capture_session.sh` to the `Stop` array in `~/.claude/settings.json`. The script reads the event JSON on stdin, extracts `transcript_path`, and spawns `runaway sessions ingest` in the background so Claude never blocks waiting for summarization.

**B. Cron watcher (VS Code, Cursor, anything that doesn't fire Stop hooks).** Schedule `bin/watch_sessions.sh` every 10 minutes:

```cron
*/10 * * * * /path/to/RunawayContext_v3/bin/watch_sessions.sh
```

The watcher and the Stop hook share the same guarded code path (`runaway_context.session_summary`), so running both simultaneously is safe — pending markers and processed markers de-duplicate.

### Token-budget guardrails

Every summarization request passes through nine guards before any model call:

| Guard | Default | Knob in `config.json` |
|---|---|---|
| Global flock with timeout | 300s | `summarizer_lock_timeout_sec` |
| Per-conversation cooldown | 300s | `summarizer_cooldown_sec` |
| Transcript char cap | 30_000 | `summarizer_char_cap` |
| Idle threshold (skip active sessions) | 1800s | `summarizer_idle_threshold_sec` |
| Attempt cap → permanent-fail marker | 3 | `summarizer_attempt_cap` |
| Daily token budget ledger | 50_000 | `summarizer_daily_token_cap` |
| Circuit breaker (consecutive fails) | 5 | `summarizer_circuit_break_after` |
| Circuit recovery window | 3600s | `summarizer_circuit_recovery_sec` |
| LLM provider gate | `"off"` (HR-1) | `summarizer_provider` |

`runaway sessions budget` prints today's ledger. When the daily cap is reached, the summarizer falls back to **metadata-only** inserts — every conversation still lands in `session_logs`; only the LLM-summary `notes` field stays empty until the next UTC day.

---

## MCP Wiring

The MCP server uses Content-Length framing by default (spec-correct per the MCP standard). For debugging, set `RC_MCP_FRAMING=ndjson` to switch to newline-delimited JSON.

| AI client | How to wire |
|---|---|
| **Claude Code** | `runaway doctor --fix-mcp` merges the entry into `~/.claude/mcp.json` (prompted, reversible). Or by hand: `{"mcpServers": {"runaway-context": {"command": "runaway", "args": ["mcp", "serve"]}}}` |
| **Cursor** | See `.cursor/rules/runaway-retrieval.md` |
| **Custom client** | Spawn `runaway mcp serve` as a subprocess; speak JSON-RPC 2.0 with Content-Length framing on stdin/stdout |

The full tool reference is at [docs/MCP.md](docs/MCP.md).

---

## The Adopter's AI Contract (longer form)

When you install RunawayContext, your AI agent enters a binding contract with the framework. The contract has seven clauses; each is independently checkable.

### Clause 1 — Read the Hard Rules

Before installing anything, the AI must read:

- [docs/HARD_RULES.md](docs/HARD_RULES.md) — the full charter, HR-1 through HR-15.
- The relevant sections of [RUNAWAYCONTEXT.md](RUNAWAYCONTEXT.md).
- The plan's Part 0 if available.

If the AI cannot access these files, it must stop and report the access failure. It must not proceed on a guess of what the hard rules say.

### Clause 2 — Never weaken or work around a hard rule

If during install the AI encounters a hard rule that appears to need changing, the correct action is:

1. Stop the install.
2. Document the proposed rule change in a versioned PR titled `RULE-CHANGE: HR-N — <summary>`.
3. Get the maintainer's review.

The wrong action is to silently work around the rule, add a config flag that exempts the install, or comment out the test.

### Clause 3 — Run the contract test suite

After install, the AI runs `pytest -m contract -v` and reports the result honestly. If any test fails, the install is not complete. The AI does not mark it complete on the basis of "most tests pass."

### Clause 4 — Run the doctor and act on its findings

After contract tests pass, the AI runs `runaway doctor --json`, parses the JSON, and walks the findings list. For every `fail` finding, the AI applies the `remediation` field. For every `warn` finding, the AI consults the user. The AI does not silently ignore warnings that are relevant to the user's tier.

### Clause 5 — Choose the tier with the user

The AI does not pick a tier unilaterally. It walks the decision tree above with the user, recommends a tier, and accepts an override. It does not enable features above the chosen tier.

### Clause 6 — Honest reporting

Final report: chosen tier, next promotion gate, any WARN findings the user accepted, list of optional integrations enabled. If anything is incomplete, the report says so explicitly.

### Clause 7 — No skipping

Every install step completes or the install does not complete. Partial installs are reported as partial, not marked done.

---

## The AI's permitted remediation actions

Your AI is permitted to:

- Install Python packages via `pip install --user` (`pip install -e .`, optional `[dev]` extras).
- Create directories under `~/_knowledge/` (or wherever `RC_KS_DIR` points).
- Apply the migrator (`runaway db migrate`).
- Register canonical slugs.
- Edit `~/_knowledge/config.json` to set non-network flags.
- Wire the drift hook (write to `~/.claude/settings.json` or add a crontab entry — with your confirmation).
- Copy work-type templates from the repo to your install dir.

Your AI is NOT permitted to (without explicit confirmation):

- Enable any `Config.network_opt_in[<provider>]` flag.
- Configure OTLP export endpoints.
- Set up federation sources.
- Run `runaway db hard-delete` (HR-3 — CLI-only, audit-logged).
- Disable or modify pre-commit hooks.
- Skip a failing contract test.

---

## If this isn't for you — the undo path

You can leave at any time. Nothing about RunawayContext is irreversible.

```
runaway uninstall --dry-run                     # see what would happen
runaway uninstall --export-markdown ~/my-notes  # dump lessons + chunks to .md first
runaway uninstall --yes                         # archive + remove
```

**What the undo does (in order):**

1. **Markdown export (optional).** If you pass `--export-markdown <DIR>`, every lesson, chunk, and project brief is dumped to a portable `.md` tree with YAML frontmatter. You can keep using these files directly with any AI tool — they don't need RunawayContext to be installed.
2. **Archive.** Unless you pass `--no-archive`, the **entire v3 footprint** is snapshotted to a timestamped `runaway-context-<UTC>.tar.gz`:
   - The install directory under `<install-dir-name>/` — DBs, config, manifest, install_id, copied templates.
   - **Every project brief v3 ever wrote** at user-controlled `project_context_card.md_path` locations (typically `<project>/CLAUDE.md`), preserved under `external/<original-absolute-path>/` inside the tarball.
   - **Every pre-install file v3 modified** (drift hook configs, etc., as recorded in `install_manifest.modified_files`), also under `external/`.
   - A top-level `EXTERNAL_FILES.json` index lists every external path and its original location.
   - **Safety rail:** files under system roots (`/etc/`, `/usr/`, `/var/`, `/bin/`, `/sbin/`, `/sys/`, `/proc/`) are skipped even if a malformed `project_context_card` row claims to live there — we will not sweep system files into a user archive.
3. **Revert (optional).** If you pass `--revert`, the install_manifest.json is read and any pre-install files we modified are restored to their original content.
4. **Remove.** The install dir is deleted. With `--keep-db`, only `config.json` and `install_manifest.json` are removed and your DBs stay in place.

**Restoring from the tarball** — just `tar xzf runaway-context-<UTC>.tar.gz`. The install dir comes back at its original name; external files live under `external/...` so you can copy them back to their original paths (the `EXTERNAL_FILES.json` index tells you where). No automatic write to user paths during extraction — you decide what to put back.

**Verbatim "UndoRunawayContext" prompt** (paste to your AI):

```
I want to uninstall RunawayContext from this machine. Please:

1. Read docs/HARD_RULES.md (HR-3 spirit — nothing destructive without a safety flag).
2. Run `runaway uninstall --dry-run` first; show me the report.
3. If I confirm I want to proceed, ask me whether I want my lessons/chunks
   exported to portable markdown first. If yes, choose a target directory and run:
       runaway uninstall \
           --export-markdown <TARGET_DIR> \
           --archive-dir ~/runaway-archives \
           --revert \
           --yes
4. Confirm to me: where the tarball is, whether the markdown export landed,
   how many lessons/chunks were preserved, and which pre-install files
   were restored to their original state.

Do not skip the dry-run. Do not omit --export-markdown unless I tell you my
content isn't worth keeping. Do not use --no-archive without my confirmation.
```

**Safety rails the uninstall enforces:**

- `--yes` is required for any destructive operation (CLI returns exit 2 otherwise).
- The install dir is never removed if it equals `$HOME` or a system path.
- `revert_modified_files` reads the install_manifest written by `runaway init`; if the manifest is missing, `--revert` refuses (we don't undo what we don't have a record of).
- Hard-delete of audit-logged data still requires `runaway db hard-delete --i-understand-this-is-permanent --backup-first` and is a separate path from uninstall.

---

## After install

Your AI reports:

```
RunawayContext v3 install complete.
Tier: T2
Next gate to T3: a second author_id must log an approved lesson within 30 days.

WARN findings accepted:
  [SQLITE_VEC] sqlite-vec not loaded — using FTS5-only retrieval.
                You can install it later via the upstream repo if you want vec0 acceleration.

Doctor: 0 fail, 1 warn, 10 ok.
Contract suite: 48 passed.

Try:
  runaway tier check
  runaway slug list
  runaway brief <your-first-slug>
```

If anything is incomplete, your AI says so.
