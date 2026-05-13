# RunawayContext — Full Theory and Reference Guide (v3)

> v3 is a contract-enforced rewrite of v2. The architectural goal is the same: minimize the always-loaded surface, route context retrieval intelligently, keep the system honest over time. The change is in *how the honesty is enforced* — by named, machine-checkable contracts that fail the build when broken.

This document is the comprehensive reference for the design, the theory, the rationale, and the operational reality of RunawayContext v3. It is long. If you want to install, see [BOOTSTRAP.md](BOOTSTRAP.md). If you want the canonical rules in one page, see [docs/HARD_RULES.md](docs/HARD_RULES.md). If you want the architecture diagram, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [The Compact (what v3 promises)](#2-the-compact-what-v3-promises)
3. [The Seven Pillars](#3-the-seven-pillars)
4. [The Six-Rung Ladder (T0..T5)](#4-the-six-rung-ladder-t0t5)
5. [The Hard Rules (HR-1..HR-15)](#5-the-hard-rules-hr-1hr-15)
6. [Contract Enforcement Architecture](#6-contract-enforcement-architecture)
7. [The Maturation Curve](#7-the-maturation-curve)
8. [Three-Axis Severity](#8-three-axis-severity)
9. [Slug Lifecycle](#9-slug-lifecycle)
10. [The MCP Server](#10-the-mcp-server)
11. [Trigger-Based Capture](#11-trigger-based-capture)
12. [Specialist Agents](#12-specialist-agents)
13. [Cross-System Data Map (T2.5)](#13-cross-system-data-map-t25)
14. [The Audit Log](#14-the-audit-log)
15. [Visibility ACL](#15-visibility-acl)
16. [The AI-Native OSS Model](#16-the-ai-native-oss-model)
17. [Anti-Loophole Provisions](#17-anti-loophole-provisions)
18. [Operational Discipline](#18-operational-discipline)
19. [Glossary](#19-glossary)

---

## 1. The Problem

Every AI coding session starts from zero. The naive fix — one giant instruction file — creates four problems:

1. **Drift.** The file grows. The 200-line file becomes a 2,000-line file. Important rules in the middle get ignored. The file becomes a memory dump.
2. **Token waste.** A 2,000-line always-loaded file eats 5..15K tokens before you have asked a question. At scale, it doubles your API bills and halves your usable context window.
3. **Fragmentation.** Cursor reads `.cursorrules`. Copilot reads `.github/copilot-instructions.md`. Codex reads `AGENTS.md`. Claude reads `CLAUDE.md`. The same project ends up with four files saying overlapping things, drifting independently.
4. **Loss.** When you swap machines, change roles, or the team turns over, the knowledge evaporates. The corrections you made over six months disappear in a `git clone`.

v1 (2026-04) addressed problems 1 and 2 with a four-tier architecture: always-loaded tiny files, on-demand-loaded big files, a SQLite knowledge store. It worked for a quarter. Then drift returned in a subtler form — *file-level* drift was prevented, but *process-level* drift sneaked back in. "While I'm here, let me note this" became normal.

v2 (2026-05) moved discipline from policy to code. The brief regenerator refused to write past 150 lines. The CLI rejected writes without a project tag. A drift detector watched the always-loaded files. The schema enforced canonical slugs. This worked for a year. Then drift returned in a yet-subtler form — *code-enforced rules* held, but *new code* could weaken them silently. A telemetry feature added a network call "for diagnostics." A maturation engine started auto-archiving "because the suggestion was obviously right." A test got marked `@pytest.mark.skip` because it was "flaky."

v3 closes the remaining holes with **contracts**: rules that cannot be weakened without a versioned PR titled `RULE-CHANGE: HR-N — <summary>` and a maintainer signoff. Every rule has a named test. Every test is the rule. The build fails when a contract is violated. The release does not ship when a contract is in `pending` status.

---

## 2. The Compact (what v3 promises)

The five guarantees from v2 are preserved and re-bound to specific contracts:

| # | Guarantee | Bound by |
|---|---|---|
| **G1** | Always-loaded context ≤3,000 tokens regardless of corpus size | HR-5 |
| **G2** | Zero network egress by default at every tier | HR-1 |
| **G3** | Every write is recoverable (soft delete + versioning + audit) | HR-3, HR-7 |
| **G4** | Migration is non-destructive in perpetuity | HR-4 |
| **G5** | Measurement is built in, not bolted on | HR-8, HR-12 |

These are not aspirations. They are tested contracts. If a test breaks, the guarantee breaks, and the release does not ship.

Additionally, v3 introduces five new guarantees specific to the contract-enforcement model:

| # | Guarantee | Bound by |
|---|---|---|
| **G6** | The maturation engine never auto-applies state changes | HR-9 |
| **G7** | The audit log is append-only and tamper-evident | HR-7 |
| **G8** | Every public API method, MCP tool, and CLI command is documented | HR-14 |
| **G9** | No plan item ships in "deferred" status | HR-11 |
| **G10** | The reference implementation installs cleanly on a fresh machine | HR-15 |

---

## 3. The Seven Pillars

### P1 — Local-First by Physics

**Contract:** HR-1.

What this means in practice: even a misconfigured install cannot phone home. The opt-in paths (remote embeddings, OTLP export, federation refresh) exist behind explicit config flags and are themselves isolated to allowlisted modules. The build-time test greps every module for network imports; non-allowlisted matches fail.

What this does **not** permit: "anonymous usage stats" toggled on by default. "Crash reports sent to maintainer." Any phone-home, ever, in default config. If you find yourself writing one, stop.

### P2 — Budgets Enforced in Code

**Contract:** HR-5.

The regenerator literally refuses to produce briefs that exceed the cap. The drift detector watches everything always-loaded. You cannot exceed budget without code consciously raising the cap.

What this does **not** permit: "Just this one project gets 300 lines." Bigger caps are global config; per-project exceptions don't exist. If a brief overflows, the user is told *what to prune* — not given an opt-out.

### P3 — Tag at the Write

**Contract:** HR-2.

No write enters the DB without a valid canonical slug. Typos, omissions, and unregistered slugs are rejected at the write boundary. The validation happens in three places (Client `_guard_write`, SQL trigger, optional CLI guard) so bypass requires bypassing all three.

What this does **not** permit: "I'll tag this lesson later." Lessons are tagged at write or not written.

### P4 — Lifecycle-Aware Knowledge

**Contract:** HR-9 plus schema enforcement of the six-state maturation curve via CHECK constraints.

Knowledge ages. Mature installs shed cognitive load via the `internalized` state — lessons drop from briefs but stay queryable. Telemetry drives suggestions; humans approve transitions.

What this does **not** permit: Automatic archival. Automatic supersession. Any state change that happens without explicit approval.

### P5 — Tier-Progressive Scaling

**Contract:** Each tier (T0..T5) has a machine-checkable promotion gate. Climbing requires the gate test to pass.

Installs are honest about their tier. The system refuses to enable T4 features on a T2 install.

What this does **not** permit: Cherry-picking T5 features into a T2 install. Promotion is atomic — you climb the whole tier or you don't climb.

### P6 — Measurement-Driven Evolution

**Contract:** HR-8 + HR-12. Plus: every architectural decision documented in this plan or the code must reference a metric that would tell us if the decision was wrong.

Decisions are not "I think hybrid retrieval is better." They are "the eval harness shows hybrid retrieval has higher MRR@5 on the reference task set." Vibes are not evidence.

What this does **not** permit: Shipping a retrieval change because "it feels better." Shipping a UI change because "it looks cleaner." Telemetry or eval scores are the bar.

### P7 — Discipline Over Convenience

**Contract:** HR-10, HR-11, HR-13.

The system fails loudly when something is wrong. The plan is complete or it's not the plan. The code is shipped or it's not in the tree.

What this does **not** permit: "I'll come back to this." "It works most of the time." "We can fix this in a patch." "Let me just stub this out for now."

---

## 4. The Six-Rung Ladder (T0..T5)

Each tier has a precise capability set, a machine-checkable promotion gate, and a rollback contract. Tier is a property of the install detectable via `runaway tier check`.

### T0 — Hello World

- **Who:** anyone with an AI assistant and a file system.
- **What's on:** `templates/<work-type>/` content; `RETRIEVAL.md` paste-once template.
- **What's off:** everything else.
- **Resource budget:** 0 MB RAM, ~50 KB disk, 0 bytes network.
- **Promotion gate to T1:** user has accumulated 5+ project-specific notes manually.
- **Rollback:** delete the markdown files. Done.

### T1 — Solo

- **Who:** single developer, one machine.
- **What's on:** full v2 surface — `knowledge.db` + `sessions.db`, FTS5, slug registry, write guards, Stop-hook drift detector, auto-generated briefs with PRESERVE blocks.
- **What's off:** MCP, semantic retrieval, telemetry, record versioning beyond `superseded_by`.
- **Resource budget:** ~80 MB RAM during CLI; ~150 MB disk steady-state; 0 bytes network.
- **Promotion gate to T2:** `runaway tier promote --to T2 --check` passes if: install has been used for ≥30 days, has ≥10 lessons across ≥2 projects, has at least one drift warning logged.
- **Rollback:** disable MCP, telemetry, semantic via config. T1 capabilities remain unchanged.

### T2 — Solo Power

- **Who:** active multi-project developer (Parkway's current target tier).
- **What's on:** T1 + MCP server (13 tools) + local telemetry + record versioning + soft-delete + predictive drift + multi-project stacking + optional semantic retrieval + specialist agents + cross-system data map (T2.5) + trigger-based capture + maturation curve + three-axis severity + slug lifecycle + brief preview/rollback + `runaway stats` CLI.
- **What's off:** team modes, attribution exports, federation, SSO.
- **Resource budget:** ~150 MB RAM (with MCP + embedding model resident); ~250 MB disk; 0 bytes network default.
- **Promotion gate to T3:** a second `author_id` has logged at least one approved lesson in the last 30 days.
- **Rollback:** drop the second user's overlay; return to single-user `knowledge.db`. Schema columns for T3 remain present (per HR-4) but unused.

### T3 — Pair / Squad

- **Who:** 2..5 collaborators.
- **What's on:** T2 + author attribution + git-based JSON export/import + conflict reporting + opt-in `author_display`.
- **What's off:** enforced ACLs, SSO, federation, multi-tenant rollout.
- **Resource budget:** per-user same as T2; +50 MB disk for knowledge-repo working copy.
- **Promotion gate to T4:** team has resolved ≥5 import conflicts via the documented workflow AND has designated ≥1 `runaway_admin` AND has 30 days of operation under T3.
- **Rollback:** demote to T2 by dropping the knowledge-repo and reverting config. Lessons exported to the team repo remain in the local DB.

### T4 — Team

- **Who:** 5..20 users with established review process.
- **What's on:** T3 + visibility ACLs (`private/team/org` enforced in retrieval) + promotion gate + multi-tenant rollout + onboarding script + 30-day probationary visibility + garbage-tagger detection + audit log (hash-chained, append-only per HR-7) + knowledge-repo CI templates.
- **What's off:** federation, SSO, OTLP export, air-gapped install.
- **Resource budget:** same per-user as T2/T3; +200 MB disk for audit log + governance state.
- **Promotion gate to T5:** SSO provider configured AND federation source identified AND audit log verified clean for 30 consecutive days.
- **Rollback:** demote to T3. Visibility ACLs become advisory (filtered out of retrieval but still in DB).

### T5 — Org / Enterprise

- **Who:** 20+ users across teams.
- **What's on:** T4 + federation (read-only upstream sources) + SSO/identity bindings + OpenTelemetry export (opt-in) + fine-grained grants + SLO instrumentation.
- **What's off:** public sharing (RunawayContext stays self-hosted at every tier, by design).
- **Resource budget:** per-user same as T4; org-wide shared DB ~5 GB at 50 users; OTLP egress configurable.
- **Promotion gate:** N/A — T5 is the top.
- **Rollback:** demote to T4. Federation sources stay in the DB but refresh stops; SSO bindings stay but provider integration disabled.

---

## 5. The Hard Rules (HR-1..HR-15)

The full charter is in [docs/HARD_RULES.md](docs/HARD_RULES.md). Summary table:

| Rule | Topic | Enforcer | Test file |
|---|---|---|---|
| HR-1 | No network egress by default | Import allowlist + runtime module-graph walk | `test_hr_1_no_network.py` |
| HR-2 | Project-tagged writes at boundary | SQL trigger + Client `_guard_write` | `test_hr_2_writes_require_valid_slug.py` |
| HR-3 | All writes recoverable | Soft delete + record versioning + admin flag-gated escape | `test_hr_3_no_hard_delete_paths.py` |
| HR-4 | Migration non-destructive | `PRAGMA table_info()` diff + abort-and-restore | `test_hr_4_migration_preserves_v2_surface.py` |
| HR-5 | Tier budgets in code | `md_writer.write_brief()` line cap | `test_hr_5_regenerator_refuses_overflow.py` |
| HR-6 | Author identity opaque | Schema CHECK + trigger | `test_hr_6_author_id_no_pii.py` |
| HR-7 | Audit log append-only | Triggers + chain verifier | `test_hr_7_audit_chain_unbreakable.py` |
| HR-8 | Telemetry never blocks/raises | Try/except wrap + bounded queue | `test_hr_8_emit_never_raises.py` + `test_hr_8_emit_never_blocks.py` |
| HR-9 | Maturation requires approval | Engine writes `suggested_maturity` only | `test_hr_9_maturation_no_auto_apply.py` |
| HR-10 | No silent failures | Lint rule | `test_hr_10_no_silent_except.py` |
| HR-11 | No deferred work | Plan status parser | `test_hr_11_plan_status_complete.py` |
| HR-12 | No tests, no merge | CI coverage gate + introspection | `test_hr_12_public_api_coverage.py` |
| HR-13 | No TODO/FIXME in shipping code | Pre-commit + CI grep | `test_hr_13_no_todo_in_release.py` |
| HR-14 | Contracts documented | Docstring introspection | `test_hr_14_public_api_documented.py` |
| HR-15 | Clean install works end-to-end | Docker sandbox test | `test_hr_15_clean_install_works.py` |

Each rule is a contract — see [docs/HARD_RULES.md](docs/HARD_RULES.md) for the full statement of each rule, its enforcer mechanism, its named test, and its violation handler.

---

## 6. Contract Enforcement Architecture

The enforcement model has four layers:

### 6.1 Build time

- **Import allowlist** (HR-1): a Python script greps every module under `src/runaway_context/` for network-related imports. Modules not in the allowlist that import these fail the build.
- **No-TODO grep** (HR-13): a CI step greps source for `TODO`, `FIXME`, `HACK`, `XXX`. Matches fail the build.
- **No-silent-except lint** (HR-10): a custom lint rule flags `except Exception:` without re-raise or explicit log.
- **Plan status parser** (HR-11): on release branches, the parser reads the plan file and asserts every Status field is `done` or `removed_from_plan`.

### 6.2 Test time

The contract test suite lives under `tests/contract/`. Every HR-N rule has a `test_hr_N_*.py` file. The suite must pass three consecutive runs at the release gate (catches flakes).

Per-feature tests live under `tests/unit/`. Each engineering deliverable E1..E24 has at least one test. Coverage ≥85% on `src/runaway_context/` excluding `_*` modules.

### 6.3 Runtime

The Client refuses to start if:
- Network-capable modules are loaded without their opt-in config flag (HR-1).
- Schema version doesn't match the code's expected version (HR-4 violation = potential data loss).
- Audit log chain verification fails at startup (HR-7 = potential tampering).
- The `lessons_learned.maturity` column has values not in the canonical enum (HR-9 violation by external SQL).

These are not graceful degradations. They are hard refusals with diagnostic output.

### 6.4 Pre-commit

| Hook | Checks |
|---|---|
| `pre-commit-no-todo` | grep for TODO/FIXME/HACK/XXX in staged files (HR-13) |
| `pre-commit-no-skip` | grep for `@pytest.mark.skip` in `tests/contract/` (L10) |
| `pre-commit-docstring` | every public symbol has a non-empty docstring (HR-14) |
| `pre-commit-no-silent-except` | lint for `except:` without re-raise or log (HR-10) |
| `pre-commit-no-print` | no `print()` in `src/`; use `logging` |

Hooks are not optional. Adopters' AIs that disable them have left the contract.

---

## 7. The Maturation Curve

Lessons have a six-state lifecycle:

```
scar  →  active  →  stable  →  internalized
                                       ↓
                                  superseded  →  archived
```

| State | Meaning | Brief weight |
|---|---|---|
| `scar` | Freshly written, just-burned, high priority in briefs | high |
| `active` | In rotation, normal priority | normal |
| `stable` | Repeatedly affirmed, lower brief weight | low |
| `internalized` | Behavior has taken hold; drops from briefs but stays queryable | none (queryable only) |
| `superseded` | Replaced by another lesson (see `superseded_by`) | none |
| `archived` | Retired from rotation entirely | none |

### How states transition

**The engine proposes; humans approve.** This is HR-9.

- The maturation engine runs periodically (cron or manual). It examines each lesson's usage, age, repeat-error signal, and writes a value to `suggested_maturity` plus a reason to `suggested_maturity_reason`.
- The Client method `mature_lesson(id, to=...)` is the only path that updates `maturity`. It records the actor in the audit log and writes a `record_versions` snapshot before the transition.
- The engine **never** writes to `maturity` directly. The SQL trigger `ll_maturity_valid_state` accepts only canonical values; the engine bypassing the Client would fail validation.

### Engine signals

The maturation engine considers:

| Signal | Suggests |
|---|---|
| Lesson cited in retrieval ≥10 times across distinct conversations, never overridden | `active` → `stable` |
| Lesson cited in ≥30 conversations spanning ≥60 days with no errors | `stable` → `internalized` |
| Lesson never cited in the last 90 days | `active` → `internalized` (with low confidence — user verifies) |
| New lesson covers the same prevention rule (semantic match) as an existing lesson | old lesson → `superseded` (links new id) |
| Lesson explicitly marked outdated by the user | `archived` |

The user reviews suggestions via `runaway mature suggestions --pending`. Approval is one command per lesson.

### Brief weighting

The brief regenerator pulls lessons by maturity:

- `scar` lessons are pinned at the top.
- `active` lessons follow in priority order (frequency, recency).
- `stable` lessons appear if there is room.
- `internalized`, `superseded`, `archived` never appear in the brief by default. They remain queryable via `search_lessons` with explicit maturity filter.

This is how mature installs shed cognitive load: as lessons internalize, the brief shrinks without losing knowledge.

---

## 8. Three-Axis Severity

v2 had a single `severity` column with three values (`critical`/`warning`/`info`). v3 keeps that for back-compat but introduces three axes, each 1..5:

| Axis | Question | Examples |
|---|---|---|
| `blast_radius` | If this rule is violated, how many systems/projects are affected? | 5 = all installs; 1 = a single feature in a single project |
| `frequency` | How often does this scenario arise? | 5 = every session; 1 = once a year |
| `reversibility` | How recoverable is a violation? | 5 = data loss / external state corruption; 1 = trivial undo |

The view `lessons_derived_severity` maps the three axes back to v2's `critical/warning/info`:

```sql
CASE
    WHEN COALESCE(blast_radius,1) >= 4 OR COALESCE(reversibility,1) >= 4 THEN 'critical'
    WHEN COALESCE(blast_radius,1) >= 2 OR COALESCE(frequency,1)     >= 3 THEN 'warning'
    ELSE 'info'
END
```

This lets new code reason in three dimensions while every retrieval that expected `severity` still sees a value.

### Why three axes

Single-axis severity collapses three distinct concerns:

- A bug that happens once a year but corrupts a database is *critical-by-reversibility*.
- A typo that ships in every PR is *critical-by-frequency*.
- A configuration mistake that affects all 50 services is *critical-by-blast-radius*.

v2 treated all three as "critical." v3 lets the maturation engine and the retrieval scorer make finer-grained decisions — for example, prioritize `reversibility=5` lessons even when they are `frequency=1`.

---

## 9. Slug Lifecycle

A slug is the canonical project identifier (e.g., `accounting`, `procore`, `kiosks`). The slug registry has three lifecycle operations:

### `alias_slug(alias, canonical)`

Creates a non-canonical name for a canonical slug. Useful when:
- A project is referred to by two names ("AR" and "accounts-receivable").
- The team migrates the canonical name and wants old references to still resolve.

Lookups for `alias` resolve to `canonical`. Writes attempted with `alias` are rerouted to `canonical` and audited.

### `deprecate_slug(slug, canonical=None)`

Marks a slug deprecated. If `canonical` is given, future writes are rerouted; otherwise, writes are rejected. Existing rows are preserved (HR-3/HR-4).

### `merge_slugs(from_slug, into_slug)`

Merges all tags. Updates `project_tags` references in:
- `knowledge_chunks`
- `lessons_learned`
- `lesson_drafts`
- `project_context_card`
- `data_sources`

The `from_slug` becomes `status='merged'` with `canonical_slug = into_slug`. Every row update is audited.

### Why lifecycle matters

In v1/v2, dropping a slug was a manual process: rename rows, update the `_project_slugs.py` constants, hope nothing broke. v3's lifecycle operations are atomic, audited, and recoverable.

If a merge was wrong, the audit log shows every row that changed and from what to what — making rollback feasible (manual, but feasible).

---

## 10. The MCP Server

The MCP server is the canonical integration point for AI clients. Full reference at [docs/MCP.md](docs/MCP.md). Highlights:

- **13 tools.** Read-mostly with carefully gated writes.
- **Stdio transport with Content-Length framing.** Spec-correct framing per the MCP standard; newline-delimited (`ndjson`) framing is opt-in via `RC_MCP_FRAMING=ndjson` for debugging.
- **No port bound. No network surface.**
- **Process-level auth.** Filesystem permissions are the gate.
- **`isError: true` structured errors.** Every refusal carries a code, message, and data.
- **Tool names and shapes pinned.** Adding a tool is a minor bump; removing one is a major bump.

The MCP server does **not** expose:
- `hard-delete` (HR-3 admin escape — CLI-only).
- `migrate` (operator-only).
- `tier promote` (operator decision, audited).
- `slug merge`, `slug deprecate` (data-shape changes).
- `config set`.

The principle: MCP exposes the *day-to-day* surface. Operator-only paths are CLI-only and audited.

### Semantic retrieval and the reference embedding provider

Semantic retrieval (E12/E13) is **opt-in** and **local-first by default**. The reference implementation ships:

- **`LocalDeterministicProvider`** — a hash-derived 384-dim pseudo-embedding. **It is not a learned model.** Its purpose is to make the contract surface end-to-end testable without an ML dependency, without a model download, and without network. It is the reference of "what a Provider looks like," not the reference of "what good retrieval quality looks like." Adopters who want real semantic retrieval plug in their own implementation conforming to `runaway_context.semantic.encoder.Provider`.
- **Opt-in network providers** (`openai`, `voyage`, `ollama`) — each gated behind `Config.network_opt_in[<name>] = True`. The import itself raises `ImportError` until the flag is set (HR-1). The local Ollama provider talks only to loopback.

This is the AI-native OSS shape: ship the principles, the schema, the contract, and a reference that proves the wiring; adopters ship the heavy ML pieces in their environment.

---

## 11. Trigger-Based Capture

v2's lesson-learning flow was: notice a mistake, manually log via CLI, hope the conversation context is still there. v3 adds a *trigger-based capture* path.

### The flow

1. **Mid-conversation,** the AI notices a scar-tissue moment (the user corrected it, the code crashed, the test exposed a hidden assumption). It calls the MCP tool `propose_lesson_draft` with what it has — title, what happened, the fix, the prevention rule, project tags.

2. **The draft enters `lesson_drafts`** with `status='pending'`. It is tagged at write (HR-2). It is **not yet** a real lesson — it does not appear in briefs, does not affect retrieval, does not feed the maturation engine.

3. **Later — typically end of session or end of day — the human reviews drafts.** `runaway drafts list` shows the inbox. `runaway drafts approve N` promotes to `lessons_learned`. `runaway drafts reject N --reason "..."` discards (but the row is preserved with `status='rejected'`).

### Why this design

The naive design (AI writes directly to `lessons_learned`) fails for three reasons:

1. **Noise.** AIs generate many false-positive "lessons" — interpretations of corrections that are actually unique to one conversation, not durable rules.
2. **Authority.** Knowledge that enters the brief shapes future AI behavior. The human should be the one authorizing that.
3. **Auditability.** The flow "AI proposed → human approved" is recorded; the actor on the audit log is the approving human, not the AI.

The draft inbox is the safety valve. Without it, you choose between "AI captures everything and the DB rots" or "AI captures nothing and you lose 90% of the signal." With it, you get the signal and filter the noise.

### What drafts cost

Drafts cost almost nothing:
- A row in `lesson_drafts` (small).
- An audit log entry (small).

They cost nothing if rejected. They cost a `record_versions` row + one INSERT into `lessons_learned` if approved.

The friction is the inbox-review session. If drafts accumulate without review, the system tells you: `runaway stats` shows pending drafts; if the count exceeds a threshold for >7 days, a drift warning fires.

---

## 12. Specialist Agents

A specialist agent is a *domain-focused* sub-brief. Examples from real-world Parkway installs:

| Specialist | Domain |
|---|---|
| `accounting` | Vista, JC, JCCP, JC_DETAIL, AFP, pay apps, GL |
| `kiosks` | Fully Kiosk Browser, Wayland cage, libinput |
| `monday` | Master Pipeline, board-relation columns, GraphQL quirks |
| `paycom` | Roster, position_family, termination_type |
| `field_staff` | Roster, super assignments, geo cache |
| `design_system` | PKWY-DS tokens, catalog, pkwy-head includes |

Each specialist owns:
- A `specialists` row (name, domain, description, md_path).
- A set of `specialist_knowledge` rows (table_name, record_id) linking the specialist to chunks and lessons.

The specialist's brief is auto-generated from the linked knowledge, capped at the same 150-line limit (HR-5), and loaded by the AI when the conversation touches the specialist's domain.

### Why specialists are first-class

In v1/v2, specialists existed as ad-hoc markdown files (`~/.claude/agents/<name>.md`) curated manually. v3 makes them first-class because:

- **Discoverability.** `runaway specialists list` shows all of them.
- **Consistency.** They follow the same regen / cap / preview / rollback flow as project briefs.
- **Auditability.** Adding or removing knowledge from a specialist is recorded.
- **Cross-project.** A specialist is *not* tied to one slug; it can span several (e.g., the `paycom` specialist spans `paycom`, `field_staff`, and `executive`).

---

## 13. Cross-System Data Map (T2.5)

A construction company's AI integration runs across Vista (SQL Server), Procore (REST API), Monday.com (GraphQL), FileMaker (XML), OPC (proprietary), and dozens of bespoke PHP tools. The join keys are scattered:

- `Vista.JC.Job_Number = Procore.projects.custom_field_jcjobnumber`
- `Monday.master_pipeline.JC# column = Vista.JC.Job_Number`
- `Paycom.eecode = company_roster.paycom_eecode (when reliable)`

v2 stored these in a long markdown file (`claude_database_map.md`) that was read on demand but never validated, never linked to specific lessons, never auditable. v3 makes the map first-class:

- **`data_sources` table.** One row per (system, name) pair. `kind` is `table` / `view` / `endpoint` / `file` / `queue` / `other`. Tagged with a project.
- **`data_source_mappings` table.** One row per (from, to, join_on). Captures the join key and notes.

Queries that need a join key can:

```sql
SELECT join_on, notes
FROM data_source_mappings dsm
JOIN data_sources from_src ON from_src.id = dsm.from_source
JOIN data_sources to_src   ON to_src.id   = dsm.to_source
WHERE from_src.name = 'JC' AND to_src.name = 'projects';
```

This is what T2.5 buys: cross-system queries become structured rather than mythology.

---

## 14. The Audit Log

`audit_log` is append-only and hash-chained (HR-7).

### Schema

```sql
CREATE TABLE audit_log (
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
```

`this_hash` is `sha256(previous_hash || occurred_at || actor || action || target_table || target_id || details)`. Tampering with any field of any row breaks the chain at that row and every row after it.

### Enforcement

Two triggers:
- `audit_log_no_update` — raises `ABORT` on UPDATE.
- `audit_log_no_delete` — raises `ABORT` on DELETE.

The verifier (`runaway audit verify`) walks the chain and reports the first broken row. Exit code 2 if the chain breaks.

### What gets audited

Every write that changes the install's semantic state:
- Lesson approved / rejected / matured / superseded / soft-deleted.
- Chunk written / superseded / soft-deleted.
- Slug aliased / deprecated / merged.
- Brief regenerated / rolled back.
- Specialist knowledge added / removed.
- Visibility level changed.
- Tier promoted / demoted.
- Migration applied.
- Hard-delete invoked (with both required flags).

Reads are **not** audited. Telemetry counts reads (HR-8) but does not append to the audit log.

---

## 15. Visibility ACL

Every chunk and lesson has a `visibility` column with three canonical values:

- `private` — visible only to the writing author.
- `team` — visible to anyone in the install's authenticated user pool.
- `org` — visible to anyone with read access to the install (typically a wider group).

At T1/T2/T3, the column is present but not enforced in retrieval. At T4, `Client.search_*` filters by visibility against the current user's identity (via SSO bindings from S1). At T5, fine-grained grants (S5) layer over.

### Why the column exists at every tier

v2 lacked visibility. When a v2 install grew to T3 (multi-user), retrofitting visibility required schema migrations under load. v3 ships visibility from day one: the column is `private` by default, the enforcement code is dormant at T1/T2/T3, and the activation at T4 is a config flip — no schema change.

This is HR-4 in action. Adding the column at every tier is non-destructive; not adding it means a future migration that *would* be destructive in spirit.

### Probationary visibility (T4)

When a new author is added to a T4 install, their writes default to `visibility='private'` for 30 days. After 30 days of activity without rejection events, they are promoted to writing `team`. This catches garbage-tagger patterns (a new contributor whose drafts are all rejected) before their content pollutes shared retrieval.

---

## 16. The AI-Native OSS Model

RunawayContext v3 is shipped as **principles + reference implementation + contracts + specs for what we don't build**.

### What we ship

| Artifact | Purpose |
|---|---|
| The principles (Part II, the Seven Pillars) | The non-negotiable architecture |
| The schema (Appendix A of the plan) | The data model that enforces the principles |
| The reference implementation (Python, ~12 weeks of work) | Proof the principles produce a working system |
| The contract tests (HR-1..HR-15 + per-feature tests) | Machine-checkable verification |
| The specs for what we don't build (Part IV / `docs/specs/`) | Integration points for adopters' AIs |

### What we do NOT ship

- **Turnkey integrations.** No SSO code. No federation worker. No OTLP collector. No cross-platform installer.
- **Platform-specific bundles.** No Windows installer. No macOS .pkg. No Linux .deb.
- **Vendor-specific SSO code.** No Okta SDK, no Azure AD SDK, no Auth0 SDK.
- **Dashboards as production tooling.** A reference FastAPI dashboard is in S9 but is spec-only.

**The adopter is expected to bring an AI.** That AI reads the principles, the schema, the reference, and the tests. It adapts the framework to its local environment, debugs install issues against the local machine, writes the SSO / audit / federation bindings against the local stack. We supply the model and the principles; the adopter's AI puts them into action.

### The ten specs (S1..S10)

| ID | Spec | What the adopter's AI builds |
|---|---|---|
| S1 | SSO_INTEGRATION | Identity provider binding to `author_id` |
| S2 | FEDERATION | Read-only upstream source refresh |
| S3 | OTLP_EXPORT | Opt-in OpenTelemetry export |
| S4 | AIR_GAPPED_INSTALL | Offline bundle composition |
| S5 | FINE_GRAINED_GRANTS | Per-(slug, action) grants layered on visibility |
| S6 | COMPLIANCE | SOC2 / GDPR control mapping |
| S7 | MULTI_TENANT_ROLLOUT | OS user provisioning |
| S8 | IMPORTERS | Mem0 / OpenMemory / raw-markdown ingest |
| S9 | DASHBOARD | Loopback-only FastAPI |
| S10 | CROSS_PLATFORM | Windows / macOS adaptation |

Each spec has the verbatim conformance clause:

> *This specification defines the contract. Implementations must pass the contract tests (named below). Implementations that pass the contract tests are conforming. Implementations that do not are not. There is no "partial conformance." There is no "spirit of the contract." The contract tests are the contract.*

---

## 17. Anti-Loophole Provisions (L1..L12)

These are known AI shortcut patterns and the enforcement against each.

| ID | Shortcut | Block |
|---|---|---|
| L1 | "Just this once" exception | No rule has an exception clause; named exceptions are codified |
| L2 | "Spirit not letter" | Rules are machine-checkable; the letter IS the spirit |
| L3 | "Test coming next commit" | HR-12 — PRs without tests for new behavior are rejected |
| L4 | "Deferred to next phase" | HR-11 — items are `done` or `removed_from_plan`, no `deferred` status |
| L5 | "Stubbing this out" | Lint rule flags `raise NotImplementedError` in non-abstract methods |
| L6 | "Catch and move on" | HR-10 — lint check on `except:` without re-raise or log |
| L7 | "Renaming the test" | Test names are fixed; assertions must reference HR number |
| L8 | "User didn't say not to" | Destructive actions require explicit allowlist flags |
| L9 | "Add a config flag to allow it" | New config flags weakening a rule require RULE-CHANGE PR |
| L10 | "Marked it skip" | No `@pytest.mark.skip` in contract suite (pre-commit) |
| L11 | "Documenting the limitation" | Limitations are removals; no "ships with known limitation" |
| L12 | "Subtle drift over many commits" | Rule text versioned; any change requires `RULE-CHANGE` PR |

---

## 18. Operational Discipline

After install, a healthy RunawayContext install requires:

### Weekly

- `runaway audit verify` — confirm the chain is intact.
- `runaway drift list` — review what is encroaching on its budget.
- `runaway stats` — sanity-check the metrics.

### Monthly

- `runaway mature suggestions --pending` — approve maturation transitions the engine has proposed.
- `runaway drafts list --status pending --older-than 30d` — clear stale drafts.
- `bin/backup_db.sh` — verify backups are happening and rotating correctly.

### Quarterly

- Review the canonical slug list. Slugs that have not seen a write in a year are candidates for `deprecate`.
- Run `runaway stats --by-author` — confirm contribution patterns are healthy at T3+.
- Re-run `pytest -m contract -v` against the live install — confirms no rule has silently weakened.

### Annually

- Re-read [docs/HARD_RULES.md](docs/HARD_RULES.md). Re-read this file. Identify any rule that has been "interpreted loosely" in practice and either re-tighten or open a `RULE-CHANGE` PR.

---

## 19. Glossary

| Term | Definition |
|---|---|
| **Adopter** | A user who installs RunawayContext on their own machine or team |
| **Author** | A person who writes lessons/chunks; identified by opaque `author_id` |
| **Brief** | The auto-generated Tier 3 markdown file for a project |
| **Chunk** | A row in `knowledge_chunks` — a reference, source, or fact |
| **Constitution** | Tier 1, the always-loaded routing file (`CLAUDE.md` / `AGENTS.md` / etc.) |
| **Contract** | A machine-checkable rule (HR-N) backed by a named test |
| **Draft** | A `lesson_drafts` row — proposed by AI, awaiting human approval |
| **Drift** | A file growing past its tier budget |
| **HR-N** | A hard rule numbered 1..15 — see [HARD_RULES.md](docs/HARD_RULES.md) |
| **Lesson** | A row in `lessons_learned` — a scar-tissue rule |
| **Living Memory** | Tier 2, the pointer-only index |
| **Maturation** | The six-state lifecycle: scar → active → stable → internalized → superseded → archived |
| **MCP** | Model Context Protocol — the stdio integration surface |
| **PRESERVE block** | The human-curated section in an auto-generated brief, between `<!-- PRESERVE_START -->` and `<!-- PRESERVE_END -->` |
| **Slug** | A canonical project identifier registered in `slug_registry` |
| **Specialist** | A domain-focused sub-brief (e.g., `accounting`, `kiosks`) |
| **Tier** | A capability level T0..T5 |

---

## References

- The hard rules: [docs/HARD_RULES.md](docs/HARD_RULES.md)
- The architecture diagram: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- The MCP surface: [docs/MCP.md](docs/MCP.md)
- The Python API: [docs/PYTHON_API.md](docs/PYTHON_API.md)
- The bootstrap walkthrough: [BOOTSTRAP.md](BOOTSTRAP.md)
- The install prompt: [INSTALL_PROMPT.md](INSTALL_PROMPT.md)
- The v2 → v3 migration: [MIGRATION_V2_TO_V3.md](MIGRATION_V2_TO_V3.md)
- The adopter specs: [docs/specs/](docs/specs/)

---

*"Knowledge that won't earn its keep doesn't stay. Knowledge that does, ages gracefully."*
