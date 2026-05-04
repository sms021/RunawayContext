# RunawayContext: A Universal AI Memory System
### Give Your AI Assistant a Brain That Grows

*Version 2.0 — May 2026*

> **What's new in v2** (full migration notes in [CHANGELOG.md](CHANGELOG.md)):
> - Tier 3 (Project Briefs) is now **auto-generated** from the database, not hand-edited. Hand-edits outside a small PRESERVE block are wiped on next regen.
> - Every write into the Knowledge Store **requires a project slug** validated against a canonical list. Typos and untagged content are rejected at the CLI.
> - A **drift detector** (Stop hook + cron / launchd watcher) surfaces any project brief that grows past its line cap.
> - Knowledge Store is now **two SQLite files** — `knowledge.db` (curated, small, frequently backed up) and `sessions.db` (heavy transcripts, retained longer). Linked via `conversation_id` and ATTACH at query time.
> - **Lessons learned** gain `severity`, `status`, and `superseded_by` for graceful lifecycle.
> - **`project_context_card`** — a new manifest layer your AI queries first when entering a project. One row per project, auto-rebuilt from any tagged source row.
>
> v1 users: see [Section 13: v1 → v2 Upgrade](#13-v1--v2-upgrade). v2 is additive — no destructive changes.

> **Renamed from SuperContext.** If you found us through the old repo, you're in the right place. Same system, new name.

---

## What This Is

This is a step-by-step guide to building a **persistent, tiered knowledge system** for any AI coding assistant. When implemented, your AI will:

- Remember lessons across conversations instead of starting from zero every time
- Know your project's business rules, schemas, and gotchas without being told twice
- Route different types of knowledge to the right place at the right depth
- Get smarter over time as it accumulates experience with your codebase
- Avoid repeating mistakes it (or you) already solved
- **(v2)** Stop drifting back into the giant-instruction-file shape it's trying to escape

This system was developed over hundreds of real-world sessions building construction management software. v2's enforcement-first design came from watching v1's policy-only constraints quietly drift over six months of use — the fix was to put the discipline into the schema, the CLI write guards, and a drift detector, not into a "be careful" line in the docs.

It draws on research from academic papers, open-source projects (Mem0, OpenMemory, Brain-Agent), industry practice (Manus, Spotify, OpenAI Codex), and hard-won lessons from daily use.

**It works with**: Claude Code, Cursor, GitHub Copilot, OpenAI Codex CLI, Aider, Windsurf, or any AI that reads instruction files. The core principles are tool-agnostic. v2 adds optional Stop hook + cron watcher integration for tools that support them.

---

## Table of Contents

1. [The Core Idea](#1-the-core-idea)
2. [Architecture Overview](#2-architecture-overview)
3. [Tier 1: The Constitution](#3-tier-1-the-constitution) — Always-loaded global instructions
4. [Tier 2: Living Memory](#4-tier-2-living-memory) — Cross-session behavioral learning
5. [Tier 3: Project Briefs](#5-tier-3-project-briefs) — Auto-generated per-project knowledge ⚡ v2
6. [Tier 4: The Knowledge Store](#6-tier-4-the-knowledge-store) — `knowledge.db` + `sessions.db` ⚡ v2
7. [Session Memory](#7-session-memory) — Cross-conversation continuity
8. [Tool-Specific Setup](#8-tool-specific-setup) — Claude Code, Cursor, Copilot, Codex, Aider
9. [Scaling Up](#9-scaling-up) — Specialists, auto-mining, consolidation
10. [Anti-Patterns](#10-anti-patterns) — What NOT to do
11. [Quick Start](#11-quick-start) — Get running in 15 minutes
12. [Templates](#12-templates) — Copy-paste starter files
13. [v1 → v2 Upgrade](#13-v1--v2-upgrade) — Migration recipe ⚡ v2
14. [Write Guards](#14-write-guards-v2) — Project tagging, slug validation, source attribution ⚡ v2
15. [Drift Detection](#15-drift-detection-v2) — Stop hook, cron watcher, snapshot file ⚡ v2
16. [Multi-User Setup](#16-multi-user-setup-v2) — Shared hosts, Stop hook rollout ⚡ v2

---

## 1. The Core Idea

Every AI conversation starts with a **context window** — a limited amount of text the AI can "see" at once. Without a memory system, every conversation starts from scratch. The AI doesn't know your codebase conventions, your database quirks, the bug you fixed last week, or why you made that architectural decision.

**The fix is simple in concept**: give the AI files to read that contain what it needs to know. But the hard part is **what goes where** and **how much**. Too little context and the AI makes uninformed mistakes. Too much and it drowns — research shows AI accuracy drops when context exceeds ~32K tokens, with important instructions in the middle being ignored entirely (the "lost-in-the-middle" problem).

**RunawayContext solves this with tiers**:

```
┌─────────────────────────────────────────────┐
│  Tier 1: CONSTITUTION (always loaded)       │  ~200 lines
│  Rules, preferences, routing instructions   │  Loaded: EVERY session
├─────────────────────────────────────────────┤
│  Tier 2: LIVING MEMORY (always loaded)      │  ~50 lines + detail files
│  Behavioral gotchas, corrections, lessons   │  Loaded: EVERY session
├─────────────────────────────────────────────┤
│  Tier 3: PROJECT BRAINS (loaded per-task)   │  No limit per file
│  Business rules, schemas, changelogs        │  Loaded: When working in that project
├─────────────────────────────────────────────┤
│  Tier 4: KNOWLEDGE STORE (queried on-demand)│  Unlimited
│  Reference data, terminology, full schemas  │  Loaded: When the AI searches for it
└─────────────────────────────────────────────┘
```

**Small things load always. Big things load only when needed.** That's the whole trick.

---

## 2. Architecture Overview

### The Four Tiers

| Tier | What | Size Limit | When Loaded | Contains |
|------|------|-----------|-------------|----------|
| **1. Constitution** | Global instruction file | ~200 lines | Every session | Your preferences, routing rules, tool configs, behavioral mandates |
| **2. Living Memory** | Auto-memory index + detail files | ~50 line index | Every session | Corrections, gotchas, patterns the AI keeps getting wrong |
| **3. Project Brains** | Per-project instruction files | No hard limit | When you work in that directory | Business rules, schemas, API docs, changelogs, decision logs |
| **4. Knowledge Store** | Searchable database or file collection | Unlimited | On-demand via search/query | Full schemas, reference tables, terminology, metrics |

### Key Principles

1. **Every piece of knowledge has exactly one home.** Never duplicate information across tiers.
2. **Search before saving.** Always check if the information already exists before adding it.
3. **Route correctly.** If it's project-specific, it goes in a project brain — not the constitution.
4. **Smaller is better for always-loaded tiers.** Every line in Tier 1-2 competes for attention.
5. **Focus on what the AI would get wrong without the file.** Don't restate common knowledge.
6. **Imperative statements over explanations.** "Always use TRIM(field)" beats a paragraph about why.
7. **Update, don't append forever.** Regularly prune stale entries. Dead knowledge is worse than no knowledge.

### What NOT to Store (Critical)

Do not store things the AI can derive by reading the current state of the code:
- Code patterns visible in the source files
- File structure (the AI can list directories)
- Git history (the AI can run git log)
- Things already documented in README files
- Standard language/framework conventions

**Store only what the AI can't figure out on its own**: business context, past mistakes, non-obvious gotchas, decisions and their rationale, cross-system relationships, and your personal preferences.

---

## 3. Tier 1: The Constitution

The Constitution is the most important file in your system. It's loaded into **every single conversation** automatically. It tells the AI who you are, how to behave, where to find things, and what rules to follow.

### Where It Lives

| Tool | File Location |
|------|--------------|
| Claude Code | `CLAUDE.md` in your project root (or `~/.claude/CLAUDE.md` for global) |
| Cursor | `.cursor/rules/constitution.mdc` with `alwaysApply: true` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| OpenAI Codex | `AGENTS.md` in your project root |
| Aider | Referenced via `.aider.conf.yml` or added manually |
| Any tool | A markdown file you paste into the system prompt |

### What Goes In It

The Constitution should contain **only** these categories:

#### 1. Identity Block (5-10 lines)
Who you are, what this project is, what environment you're in.

```markdown
## About
- **Project**: [Your project name]
- **Purpose**: [One-line description]
- **Primary Language**: [Language/framework]
- **Working Directory**: [Path]
```

#### 2. Behavioral Rules (10-20 lines)
How you want the AI to act. Focus on corrections to its default behavior.

```markdown
## Rules
- Never assume my intent — ask before making changes if unsure
- Be concise — don't show full diffs unless I ask
- Don't add features beyond what I asked for
- Prefer editing existing files over creating new ones
- [Your preferences here]
```

#### 3. Knowledge Routing Table (15-25 lines)
This is the **most important section**. It tells the AI where different types of information belong.

```markdown
## Knowledge Architecture

| Tier | Location | What belongs here |
|------|----------|-------------------|
| 1. Constitution | This file | Routing rules, preferences, behavioral mandates |
| 2. Living Memory | MEMORY.md | Cross-session gotchas, corrections (2-3 lines each) |
| 3. Project Brains | Per-project CLAUDE.md files | Business rules, schemas, changelogs |
| 4. Knowledge Store | [your location] | Reference data, full schemas, terminology |

### Before Saving Anything
1. Search first — check if the info already exists
2. Route correctly — project-specific data goes in the project brain, not here
3. Never put in this file: database schemas, API endpoint lists, project-specific rules
4. Never put in Living Memory: anything longer than 3 lines, reference data
```

#### 4. Project Map (10-30 lines)
A table mapping directories to project names, so the AI knows which project brain to load.

```markdown
## Project Map

| Directory | Project Name |
|-----------|-------------|
| `src/auth/` | Authentication |
| `src/api/` | API Layer |
| `src/dashboard/` | Dashboard |
| `tools/deploy/` | Deployment |
```

#### 5. Tool Configurations (10-30 lines)
Credentials, API keys, environment-specific settings. Only what's needed in every session.

```markdown
## Tools
- **Database**: PostgreSQL at localhost:5432, database "myapp"
- **API Base URL**: https://api.example.com/v1
- **CI/CD**: GitHub Actions, deploy via `make deploy`
```

#### 6. Project Context Protocol (10-15 lines)
Instructions for how the AI should load context when entering a project.

```markdown
## When Working in a Project Directory
1. Check for a CLAUDE.md (or equivalent) in that directory
2. Read it before making any changes
3. If none exists and significant work is being done, create one
4. Update it at the end of significant work sessions
```

### Size Budget

**Target: 200 lines maximum.** If your Constitution exceeds 300 lines, you're putting things in it that belong in a lower tier. The moment you catch yourself adding a database schema or API endpoint list, stop — that goes in Tier 3 or 4.

### Template

See [Section 12: Templates](#12-templates) for a complete starter Constitution.

---

## 4. Tier 2: Living Memory

Living Memory is where the AI stores things it has **learned from experience** — corrections you've given it, patterns that keep causing problems, behavioral gotchas that aren't obvious from the code.

Think of it as the AI's "mistakes I must not repeat" list.

### Where It Lives

| Tool | Location |
|------|----------|
| Claude Code | `~/.claude/projects/<project-path>/memory/MEMORY.md` (auto-memory, built-in) |
| Cursor | `.cursor/rules/memory.mdc` with `alwaysApply: true` |
| GitHub Copilot | `.github/memory.md` (referenced from copilot-instructions.md) |
| Any tool | A `MEMORY.md` file in your project root |

### Structure: Index + Detail Files

The key insight is the **index + companion file** pattern:

**MEMORY.md** (the index — always loaded, kept small):
```markdown
# Living Memory
<!-- Max ~50 lines. Each entry: 1-3 lines. Link to detail files for depth. -->

## Database Gotchas
- **Field X is NOT aggregatable** — has dual semantics. Use table Y instead.
- **Always TRIM(field)** when querying table Z — inconsistent trailing spaces.

## API Gotchas
- **Base URL** — docs site is NOT the API. Actual: `api.example.com/v1`
- [Auth token refresh race condition](memory/feedback_auth_race.md)

## User Preferences
- Be concise with code edits — describe changes briefly, don't show full diffs.
- Prefers dynamic calculations over hardcoded values.
```

**Detail files** (loaded only when the topic comes up):
```markdown
---
name: Auth Token Race Condition
description: OAuth refresh can race with concurrent requests causing 401 cascades
type: feedback
---

## What Happened
Two API calls fired simultaneously. Both detected an expired token. Both tried to refresh.
The second refresh invalidated the first's new token.

## The Fix
Use a mutex/lock around token refresh. Only one request refreshes; others wait for it.

## How to Apply
When touching auth middleware, ensure token refresh is serialized. Never fire concurrent refreshes.
```

### What Goes In Living Memory

| Category | Example | Save when... |
|----------|---------|-------------|
| **Corrections** | "Don't mock the DB in tests — we got burned" | The user corrects your approach |
| **Gotchas** | "Field X has dual semantics — neither MAX nor SUM works" | You discover a non-obvious trap |
| **Patterns** | "Always use DISTINCT ON when joining table A to B" | A pattern prevents recurring errors |
| **Preferences** | "Don't summarize at the end of responses" | User expresses a communication preference |
| **User context** | "User is a data scientist, new to React" | You learn about the user's background |

### What Does NOT Go In Living Memory

- Project-specific data (→ Tier 3: Project Brain)
- Reference data like schemas (→ Tier 4: Knowledge Store)
- Anything longer than 3 lines (→ detail file, linked from index)
- Things derivable from the code
- Ephemeral task details

### Memory Types (for detail files)

Use YAML frontmatter to classify each detail file:

| Type | Description | Decay |
|------|-------------|-------|
| `feedback` | Correction from the user | Permanent (until explicitly removed) |
| `user` | Information about the user | Permanent |
| `project` | Ongoing work/initiative status | Review monthly |
| `reference` | Pointer to external information | Permanent |

### Size Budget

**Index: ~50 lines maximum.** Detail files: no limit, but keep each one focused on a single topic (typically under 30 lines).

### Maintenance

Review Living Memory monthly:
1. Remove entries that are no longer relevant (project completed, bug fixed in a different way)
2. Consolidate entries that have grown similar
3. Move entries that have become project-specific to the relevant Project Brain
4. Update entries where the situation has changed

---

## 5. Tier 3: Project Briefs

Project Briefs are the workhorses of the system. Each project (or major feature area) gets its own instruction file containing everything the AI needs to work effectively in that context.

> **v2 architectural change.** Tier 3 in v1 was a hand-edited markdown file you maintained over time. v2 makes it a **generated artifact** rebuilt from `knowledge.db` on demand. The file you see in your project directory is auto-written; only a small `<!-- PRESERVE_START --> ... <!-- PRESERVE_END -->` block survives regeneration. This is the v2 fix for the slow drift back into 2,000-line files.
>
> The conceptual content below — what should be in a project brief — still applies. The change is **where it lives** (DB rows tagged with the project slug) and **how it gets into the file** (via `--rebuild-md`, not by hand).

### Where They Live

One file per project directory. The file path is registered in `project_context_card.md_path`:

| Tool | File |
|------|------|
| Claude Code | `CLAUDE.md` in the project subdirectory |
| Cursor | `.cursor/rules/<project-name>.mdc` with glob pattern targeting that directory |
| GitHub Copilot | `.instructions.md` in the project subdirectory |
| OpenAI Codex | `AGENTS.md` in the project subdirectory |
| Any tool | `PROJECT_CONTEXT.md` or equivalent in the project directory |

### How v2 Generates Them

```
                         ┌──────────────────────┐
                         │  knowledge.db        │
                         │  ┌────────────────┐  │
   tag rows with the     │  │ lessons_learned│  │     project_context_card row
   project slug          │  │ (project_tags) │  │     is the manifest
   ─────────────────────▶│  └────────────────┘  │
                         │  ┌────────────────┐  │     ┌──────────────────────┐
                         │  │ knowledge_chunks│ │ ──▶ │ project_context_card │
                         │  │ (project_tags) │  │     │  - top_warnings      │
                         │  └────────────────┘  │     │  - active_lesson_ids │
                         └──────────────────────┘     │  - active_chunk_ids  │
                                                       │  - md_path / cap     │
                                                       └──────────┬───────────┘
                                                                  │
                                                       ─rebuild-md┴ ──▶  CLAUDE.md
                                                                          (≤150 lines,
                                                                           PRESERVE block,
                                                                           regen banner)
```

**The flow:**
1. **Tag content** with the project's slug. Lessons via `--log-lesson --ll-projects <slug>`. Chunks via `propose_knowledge.py --project <slug>`.
2. **Rebuild the manifest**: `python3 lib/ll_brief.py --rebuild-brief <slug>`. This walks every tagged row and updates the `project_context_card` row.
3. **Regen the markdown file**: `python3 lib/ll_brief.py --rebuild-md <slug>`. Reads the card, writes a slim file at `card.md_path`.
4. **Read the brief** as your AI's first action: `python3 lib/ll_brief.py --brief <slug>` returns the manifest in stdout — no file read required.

### What Goes In a Project Brief (the generated file)

The regenerator emits a fixed structure. You don't need to maintain it; you just feed the DB:

```markdown
<!-- AUTO-GENERATED — DO NOT HAND-EDIT.
This file is regenerated from knowledge.db / project_context_card.
... -->

<!-- PRESERVE_START -->
## Project Name

5-10 line human-curated overview. Edit ONLY here. Survives every regen.
<!-- PRESERVE_END -->

## ⚠ Top Warnings (read first)
- **LL#42**: <critical-severity lesson title>
- **LL#13**: <critical-severity lesson title>

## Lessons Learned (8)
- LL#42 — <title>
- LL#13 — <title>
- ...

## Knowledge Chunks (15)
- KS#101 — <title>
- ...

---
_Brief regenerated 2026-05-04 12:25 UTC. Project slug: `myapp`._
```

The PRESERVE block answers: **"If a competent developer sat down to work on this project for the first time, what would they need to know that isn't obvious from reading the code?"** — but in 5-10 lines, not 500. The body of the brief is just pointers (`LL#N`, `KS#N`) the AI can drill into.

### Where the Old "Sections" Live in v2

If you used v1 and had a brief with multiple sections (Data Architecture / Key Files / Business Rules / Database Schema / Known Gotchas / Decision Log / Changelog), here's where each maps:

| v1 section | v2 home |
|-----------|---------|
| Overview | PRESERVE block in the generated brief |
| Data Architecture / Database Schema | `knowledge_chunks` (one chunk per table or schema) |
| Key Files | `knowledge_chunks` (one chunk per file group) — or skip; `find` answers this |
| Business Rules | `knowledge_chunks` (one chunk per rule), or `lessons_learned` if it came from a burned-us incident |
| Known Gotchas | `lessons_learned` (this is exactly what LL is for) |
| Decision Log | `knowledge_chunks` (one chunk per decision, or one per `decision_log` topic) |
| Changelog | `lessons_learned` for fixes; `knowledge_chunks` for stable feature notes; `sessions.db` for the timeline |

Don't try to reconstruct a v1 brief in v2 syntax. Migrate the *content* to DB rows; let the regenerator render the new file.

#### Recommended Sections

```markdown
# Project Name — AI Context

## Overview
[2-5 sentences: what this project does, who uses it, why it exists]

## Data Architecture
[Where data comes from, which system is source of truth, join patterns]

## Key Files
| File | Purpose |
|------|---------|
| `main.py` | Entry point, handles X |
| `models.py` | Database models for Y |
| `api/endpoints.py` | REST API for Z |

## Business Rules
[Domain-specific logic the AI can't infer from code]
- Status workflow: Draft → Submitted → Approved → Paid
- Amounts over $10K require VP approval
- Tax calculation uses destination-based sourcing

## Database Schema
[Tables this project owns or heavily uses]

## API Endpoints
[If the project exposes or consumes APIs]

## Known Gotchas
[Things that have caused bugs before]
- The `amount` field includes tax in table A but excludes it in table B
- Cache refreshes every 15 min — don't rely on real-time data for X

## Decision Log
[Why you made key architectural choices — prevents the AI from questioning them]
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-01-15 | Chose SQLite over PostgreSQL | Single user, no concurrent writes needed |
| 2026-02-20 | Cache API responses locally | Rate limited to 100 req/min |

## Changelog
[Reverse-chronological: what changed and why]
### 2026-03-15
- Fixed race condition in token refresh (see memory/feedback_auth_race.md)
- Root cause: concurrent requests both detecting expired token

### 2026-03-10
- Added CSV export feature
- Uses streaming to handle large datasets without memory issues
```

### Sizing Guidelines

| Project Complexity | Typical Size | Examples |
|-------------------|-------------|---------|
| Simple config/tool | 40-100 lines | CLI scripts, simple integrations |
| Standard feature | 150-400 lines | CRUD app, API service, dashboard |
| Complex system | 400-800 lines | Multi-system integration, complex business logic |
| Mission-critical | 800-1600 lines | Financial calculations, data pipelines with many edge cases |

There is no hard maximum — the file is only loaded when working in that project, so context competition is less of a concern than in Tier 1-2. But bigger files should be well-organized with clear headers for skimming.

### The Changelog is the Most Valuable Section

This cannot be overstated. The changelog, written in a **problem → root cause → fix** structure, is the single most effective way to prevent the AI from reintroducing bugs. When the AI sees:

```markdown
### 2026-03-15
- Fixed: Dashboard showing duplicate rows
- Root cause: Missing DISTINCT in join between orders and line_items
- Fix: Added DISTINCT ON (order_id) to the main query
```

...it will never write that join without DISTINCT again.

### Auto-Updating

In v1, you (or your AI) hand-edited the project brain at end-of-session. v2 inverts this — you log knowledge to the DB, and the brief auto-rebuilds.

**Add to your Constitution:**

```markdown
## Project Brief Protocol (v2)
- When finishing significant work in a project, log new knowledge to the DB:
  - Burned-us incident → `python3 lib/ll_brief.py --log-lesson --ll-projects <slug> ...`
  - Stable rule / data fact → `python3 lib/propose_knowledge.py --project <slug> ...`
  - The brief auto-rebuilds. Don't hand-edit the project's CLAUDE.md.
- When entering a project, ALWAYS first run:
  - `python3 lib/ll_brief.py --brief <slug>`
  - This returns the manifest. From there, drill via --ll-get N or --rules.
```

---

## 6. Tier 4: The Knowledge Store

The Knowledge Store is your deep reference library. It holds everything that's too detailed or too broad for the other tiers — full database schemas, terminology disambiguation, metrics definitions, infrastructure configs, tool registries.

> **v2 architectural change.** Tier 4 is now **two SQLite files**, not one or a markdown directory:
>
> ```
> ~/_knowledge/
> ├── knowledge.db    ← curated, small, frequently backed up (chunks, lessons,
> │                     project_context_card, junctions)
> └── sessions.db     ← heavy, append-only (conversation transcripts, summaries)
> ```
>
> v1 had a single `sessions.db` for everything. v2 splits the curated KS from the heavy session log because they have different lifecycles (KS is curated and frequently backed up; sessions are append-only and heavy). Linked via `conversation_id` (TEXT) — `lessons_learned.source_conversation_ref` and `chunk_sessions.conversation_id` point at `session_logs.conversation_id`. ATTACH the other DB at query time when you need them joined.
>
> v2 also drops the "markdown files" complexity level. SQLite + FTS5 is now the only level. The schema is well under 200 lines of SQL, the migrations are idempotent, and `setup_db.py` makes installation a one-liner. The cost of "Level 1: organized markdown" was that everything else (project briefs, write guards, drift detection) had to be retrofitted to handle both modes; v2 picks one and goes deep.

### Complexity Levels (v2)

There's one level. Use it.

```bash
python3 lib/setup_db.py             # creates both DBs
python3 lib/setup_db.py --no-sessions  # KS only, no session capture
```

This creates:

| Database | Tables |
|----------|--------|
| `knowledge.db` | `knowledge_chunks` (+ FTS5), `lessons_learned` (+ FTS5), `project_context_card`, `lesson_chunks`, `chunk_sessions` |
| `sessions.db` | `session_logs` (+ FTS5) |

#### What was Level 1: Organized Markdown Files (deprecated)

In v1, you could opt into a `_knowledge/` directory of markdown files instead of a SQLite database. We dropped this for v2 because:
- It can't support the project_context_card auto-generation
- It can't support write guards (typos slip in too easily)
- It can't support FTS5 search at any meaningful scale
- The cost of supporting two paths everywhere wasn't worth saving the SQLite install

If you really want the simpler approach, you can keep markdown files alongside the database and search them with grep — but the rest of v2's tooling won't help you with them.

#### What was Level 2: SQLite + CLI Script (now the default)

A SQLite database with a Python/bash CLI that the AI can query:

```bash
# Natural language search
python3 knowledge.py --ask "database connection for users table"

# Look up a term
python3 knowledge.py --term "API" --context-hint "internal,rest"

# Find data sources
python3 knowledge.py --source "users"

# List known tools
python3 knowledge.py --tools "dashboard"
```

**Database schema:**

```sql
-- Core knowledge tables
CREATE TABLE metrics (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    formula TEXT,
    description TEXT,
    source_tables TEXT,
    keywords TEXT,
    status TEXT DEFAULT 'active',
    confidence REAL DEFAULT 0.5,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT, -- table, api, file, view
    database TEXT,
    key_columns TEXT, -- JSON array
    description TEXT,
    gotchas TEXT,
    domain TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE business_rules (
    id INTEGER PRIMARY KEY,
    rule TEXT NOT NULL,
    domain TEXT NOT NULL,
    category TEXT, -- gotcha, calculation, config
    severity TEXT DEFAULT 'info', -- critical, warning, info
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE terminology (
    id INTEGER PRIMARY KEY,
    term TEXT NOT NULL,
    meaning TEXT NOT NULL,
    domain TEXT,
    context_clues TEXT, -- comma-separated: words that suggest this meaning
    anti_clues TEXT, -- comma-separated: words that suggest a DIFFERENT meaning
    priority INTEGER DEFAULT 0,
    UNIQUE(term, meaning, domain)
);

CREATE TABLE tools (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url_path TEXT,
    description TEXT,
    target_audience TEXT,
    domain TEXT,
    keywords TEXT
);

-- Full-text search (SQLite FTS5)
CREATE VIRTUAL TABLE metrics_fts USING fts5(name, domain, formula, description, keywords);
CREATE VIRTUAL TABLE data_sources_fts USING fts5(name, database, description, gotchas, domain);
CREATE VIRTUAL TABLE business_rules_fts USING fts5(rule, domain, category);
CREATE VIRTUAL TABLE terminology_fts USING fts5(term, meaning, domain);
```

**Pros**: Searchable, scalable, structured, AI can query precisely.
**Cons**: Requires Python/SQLite setup, more initial effort.

**In your Constitution, add:**
```markdown
## Knowledge Store
Database: `_knowledge/knowledge.db`
CLI: `python3 _knowledge/knowledge.py`

### When to Search
- Data questions: "Where is X stored?", "How do we calculate Y?"
- Infrastructure: Database connections, API configs
- Terminology: Disambiguating abbreviations

### Commands
python3 _knowledge/knowledge.py --ask "your question"
python3 _knowledge/knowledge.py --term "abbreviation" --context-hint "context"
python3 _knowledge/knowledge.py --source "table_name"
```

#### Level 3: Full Pipeline (Advanced)

Add automated mining, LLM consolidation, and specialist routing on top of Level 2. See [Section 9: Scaling Up](#9-scaling-up).

---

## 7. Session Memory

Session memory bridges the gap between conversations. Without it, every new session starts cold — the AI doesn't know what you worked on yesterday or what decisions you made last week.

### Complexity Levels

#### Level 1: Manual Notes (Simplest)

At the end of each significant session, update your Project Brain's changelog. This is the minimum viable session memory.

**In your Constitution:**
```markdown
## End of Session
Before ending a significant work session:
1. Update the project's CLAUDE.md changelog with what was done
2. Note any gotchas discovered in Living Memory
3. Note any decisions made in the project's Decision Log
```

#### Level 2: Built-in Auto-Memory (Recommended for most users)

Most modern AI tools have built-in memory features:

- **Claude Code**: Auto-saves to `~/.claude/projects/<path>/memory/` — this guide's Living Memory (Tier 2) maps directly to this feature
- **Cursor**: Use Memory Bank pattern with 6 structured files
- **ChatGPT**: Built-in memory (limited, but functional)

For Claude Code specifically, the auto-memory system is already Tier 2. Just make sure you're using it well — see [Section 8](#8-tool-specific-setup).

#### Level 3: Session Logging Pipeline (Advanced)

For teams or power users who want full cross-conversation search:

1. **Capture**: Hook into session lifecycle events (exit, compaction, clear) to save conversation transcripts
2. **Summarize**: Use a local or cheap LLM to generate structured summaries
3. **Index**: Store summaries in a searchable database (SQLite + FTS5)
4. **Retrieve**: Query past sessions by project, topic, date, or full-text search

**Summary structure** (what the summarizer should extract):
```markdown
## Session Summary
[One paragraph overview]

## Work Completed
- [Bulleted list of changes made]

## Technical Decisions
- [Architecture choices, approach decisions, with rationale]

## Known Issues
- [Bugs found, incomplete work, blockers]

## Key Context for Future Sessions
- [Things the next session needs to know]
```

**Retrieval query** (run at the start of each session):
```bash
python3 _knowledge/sessions.py --context "ProjectName" --days 30
```

This returns a compact briefing: recent sessions grouped by project, open items, and key decisions — everything the AI needs to pick up where you left off.

**In your Constitution:**
```markdown
## Session Memory
- Database: `_knowledge/sessions.db`
- CLI: `python3 _knowledge/sessions.py`
- When starting work on a project, silently run:
    python3 _knowledge/sessions.py --context "ProjectName" --days 30
- At the end of significant sessions, log what was done:
    python3 _knowledge/sessions.py --save --project "ProjectName" --summary "what was done"
```

---

## 8. Tool-Specific Setup

### Claude Code

Claude Code has the best native support for this system because CLAUDE.md and auto-memory are built in.

**Tier 1 — Constitution:**
- File: `CLAUDE.md` in your project root
- Also supports: `~/.claude/CLAUDE.md` (user-global, applies to all projects)
- Loaded automatically every session

**Tier 2 — Living Memory:**
- Built-in: `~/.claude/projects/<encoded-path>/memory/MEMORY.md`
- Auto-loaded every session
- Claude creates memory files here when you correct it
- You can also manually create/edit files here
- Detail files go in the same directory, linked from MEMORY.md

**Tier 3 — Project Brains:**
- File: `CLAUDE.md` in each project subdirectory
- Claude reads these when instructed to in your Constitution
- Add the "Project Context Protocol" to your Constitution (see Tier 1 template)

**Tier 4 — Knowledge Store:**
- Use a Python CLI script that Claude calls via Bash tool
- Or use MCP servers for native tool integration

**Session Memory:**
- Claude Code has three hooks that matter for session capture. Use **all three** — each one protects against a different data loss scenario:

| Hook | Event | What's at risk without it |
|------|-------|--------------------------|
| `PreCompact` | Before `/compact` or auto-compaction | Full transcript is compressed — raw detail before that point is lost forever |
| `SessionEnd` (matcher: `"clear"`) | When user runs `/clear` | Everything before the clear is wiped — if the session continues, only post-clear work is captured |
| `Stop` | Session exits normally | Final state of the conversation is never captured |

- Configure in `.claude/settings.json` (note the nested `hooks` array — this exact structure is required):
```json
{
  "hooks": {
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/capture-session.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "clear",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/capture-session.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/capture-session.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```
- **Important**: Your capture script should handle deduplication — the same conversation may be captured multiple times (e.g., compaction then exit). Use the conversation ID as a key and replace, don't append.

**Important — VS Code vs CLI:**
- Claude Code hooks (`Stop`, `PreCompact`, etc.) **only fire in the CLI terminal version**
- The **VS Code Claude extension does NOT fire hooks** — sessions will not be captured automatically
- **Fix**: Set up a cron-based watcher script that runs every 10 minutes, scans `~/.claude/projects/` for new/changed conversation files, and logs them to your session database. The `run_RunawayContext.md` executor includes a complete watcher script ready to install.
- If you have multiple users (some CLI, some VS Code), the cron watcher is the only reliable capture method

**Multi-user file permissions (critical):**
- Claude Code's VS Code extension creates conversation files with mode 600 (owner-only read/write)
- A watcher running as a different user **cannot read these files** without explicit permission
- **Fix**: Use POSIX ACLs to grant the watcher user read access:
  ```bash
  # For each VS Code user (run once, plus set default for future files):
  sudo bash -c 'find /home/VSCODE_USER/.claude/projects/ -type f -name "*.jsonl" -exec setfacl -m u:WATCHER_USER:r {} +'
  sudo bash -c 'find /home/VSCODE_USER/.claude/projects/ -type d -exec setfacl -m u:WATCHER_USER:rx {} +'
  sudo find /home/VSCODE_USER/.claude/projects/ -type d -exec setfacl -d -m u:WATCHER_USER:r {} +
  ```
- **Alternative** (no sudo needed): Each user runs their own cron job pointing to the same shared sessions database. No cross-user file access required.
- **Note**: Claude Code may override default ACLs by explicitly setting mode 600 on new files. If permissions break again after an update, re-run the `setfacl` commands or add them to the watcher script to self-heal before each read attempt.

**Pro tips:**
- Claude Code's `/compact` command compresses context but preserves memory files
- Use the built-in auto-memory — when Claude saves a memory, it persists across `/compact` and new sessions
- The `~/.claude/CLAUDE.md` file applies across ALL projects — put truly universal preferences there

### Cursor

**Tier 1 — Constitution:**
- File: `.cursor/rules/constitution.mdc`
- Frontmatter: `alwaysApply: true`
```
---
description: Global rules and knowledge routing
alwaysApply: true
---
[Your constitution content here]
```

**Tier 2 — Living Memory:**
- File: `.cursor/rules/memory.mdc`
- Frontmatter: `alwaysApply: true`

**Tier 3 — Project Brains:**
- Files: `.cursor/rules/<feature-name>.mdc`
- Use glob patterns to auto-attach when editing relevant files:
```
---
description: Auth system context
globs: ["src/auth/**", "src/middleware/auth*"]
---
[Auth project brain content]
```

**Tier 4 — Knowledge Store:**
- Markdown files in a `_knowledge/` directory (Level 1)
- Or use Cursor's `@docs` feature to reference documentation
- Or use MCP integration for dynamic queries

### GitHub Copilot

**Tier 1 — Constitution:**
- File: `.github/copilot-instructions.md`
- Limit: 4,000 characters for code review instructions
- Loaded automatically in Copilot Chat and code review

**Tier 2-3 — Memory + Project Brains:**
- Files: `.instructions.md` in any directory (scoped to that directory)
- Or use path-specific custom instructions in VS Code settings

**Tier 4 — Knowledge Store:**
- Reference documentation via `@workspace` or `#file` in chat
- No native database query support — use Level 1 (markdown files)

### OpenAI Codex CLI

**Tier 1 — Constitution:**
- File: `AGENTS.md` in your project root
- Cascades: Files in subdirectories override/extend parent files

**Tier 2 — Living Memory:**
- Built-in: Codex has a two-phase memory pipeline (extract → consolidate)
- Stores in `MEMORY.md` automatically
- Also generates `skills/` files for reusable patterns

**Tier 3 — Project Brains:**
- File: `AGENTS.md` in each subdirectory
- Automatic cascade resolution — subdirectory files extend root

**Tier 4 — Knowledge Store:**
- Similar approach to Claude Code — Python CLI via shell tool

### Aider

**Tier 1 — Constitution:**
- File: `.aider.conf.yml` for settings, plus a referenced instruction file
- Or use `/read` command to load a constitution file each session

**Tier 2-4:**
- Use `/read` to load relevant context files
- Aider's repo-map feature provides automatic code structure context
- For persistent memory, maintain markdown files and `/read` them as needed

### Any Other Tool (ChatGPT, local LLMs, etc.)

If your tool doesn't have native instruction file support:

1. Create the same file structure (Constitution, MEMORY.md, project files)
2. At the start of each session, paste the Constitution + MEMORY.md into the system prompt or first message
3. When switching to a project, paste that project's brain file
4. For the Knowledge Store, paste relevant sections as needed

This is less elegant but still dramatically more effective than starting from scratch.

---

## 9. Scaling Up

Once the basic four tiers are working, you can add automation to make the system self-maintaining.

### Auto-Mining (Level 3 Knowledge Store)

Write a script that periodically scans your codebase and proposes new knowledge entries:

**What to mine:**
- SQL queries in source code → data source entries
- API endpoint definitions → tool/API entries
- Comments with WARNING/TODO/HACK → business rule entries
- Config files → infrastructure entries
- Project Brain changelogs → lessons learned

**How to mine safely:**
1. Mining produces **proposals**, not direct entries
2. Proposals go into a staging table with low confidence (0.5)
3. A review step (human or LLM) approves/rejects proposals
4. Approved entries get inserted into the real knowledge tables
5. Rejected entries are remembered to prevent re-proposal

```
Codebase → Miner → Proposals (staging) → Review → Knowledge Store
                                            ↑
                                      Human or LLM
```

### LLM Consolidation

If you have access to a local LLM (Ollama, LM Studio, etc.) or a cheap API:

1. **Hourly**: Cluster similar proposals, merge duplicates, boost confidence for multi-source corroboration
2. **Daily**: Auto-promote proposals above a confidence threshold (e.g., 0.8)
3. **Weekly**: Flag stale entries (not verified in 90+ days) for review

### Specialist Agents

For large codebases with multiple domains, create specialist agents that pre-load only their relevant knowledge:

```markdown
# Specialist: Database Expert
- Loads: all data_sources entries, SQL gotchas, schema rules
- Invoked when: user asks about database queries, schemas, or data issues
- Model: fast model (e.g., Sonnet/Haiku) for cost efficiency

# Specialist: Frontend Expert
- Loads: UI component rules, CSS conventions, accessibility guidelines
- Invoked when: user works on frontend components
- Model: fast model
```

Each specialist runs a focused query against the Knowledge Store at startup:
```bash
python3 knowledge.py --specialist-brief "database-expert"
```

This returns only the critical rules, terminology, and gotchas relevant to that domain — keeping the specialist's context window focused and effective.

### Session Memory Pipeline

The full pipeline for automatic cross-conversation continuity:

```
Session lifecycle event fires
    ↓
    ├─ PreCompact: full transcript about to be compressed — capture NOW
    ├─ SessionEnd (clear): conversation about to be wiped — capture NOW
    └─ Stop: session ending normally — capture final state
    ↓
Capture script runs (same script for all three events)
    ↓
Deduplicate by conversation ID (replace, don't append)
    ↓
Metadata logged to sessions.db (files, timestamp, project)     ← Always runs
    ↓
(Optional) AI summarizer picks up unsummarized sessions         ← Cron, batched
    ↓    ├─ Max 5 per run, lock file, 3-attempt cap
    ↓    └─ Marks each session done so it's never reprocessed
    ↓
Next session: --context query retrieves relevant history
```

**Why all three hooks matter**: A single long session might compact twice and then exit — that's three capture events. Without `PreCompact`, you lose the raw transcript from before each compaction. Without `SessionEnd`, a `/clear` mid-session silently drops everything. The capture script is the same for all three; deduplication by conversation ID prevents bloat.

#### AI-Powered Summaries (Optional Enhancement)

The basic pipeline captures metadata — file paths, timestamps, project names. Good enough to jog your memory, but thin. You can optionally add an LLM step (Claude Haiku, GPT-4o-mini, or a local model) to read transcripts and generate real summaries with decisions, issues, and context.

The quality difference is significant. But so is the risk.

**A cautionary tale**: The first version of this system tried to summarize every unprocessed session in one pass. When the API call failed, it retried the full batch. Then retried again. One runaway loop burned through a third of a week's token budget before anyone noticed. These safeguards exist because that happened.

**If you add AI summarization, these are mandatory:**

| Safeguard | Rule | Why |
|-----------|------|-----|
| **Batch limit** | Max 5 sessions per run | A backlog of 50 sessions drains over 10 cycles, not one catastrophic pass |
| **Processed marker** | `summarized` flag in DB; never re-process a completed session | Prevents the retry-everything loop that kills budgets |
| **Attempt cap** | 3 tries max, then mark as permanently failed | A session that fails 3 times will fail forever — stop wasting tokens on it |
| **Lock file** | One summarizer instance at a time | Cron cycles can overlap; two simultaneous runs double-process everything |
| **No retry loops** | On failure, log the error and move to the next session | The cron schedule IS your retry mechanism. Never loop inside a single run. |

**Cost estimate**: ~$0.01-0.05 per session (Haiku/GPT-4o-mini), ~$0.10-0.50 per session (larger models). At 10 sessions/day, that's $3-15/month with a small model. Use the smallest model that gives you good-enough summaries.

**Key implementation details:**
- Deduplicate at capture time (same conversation = replace, don't append)
- Skip tiny sessions (auto-generated, not real work)
- Use a lock file for summarization — never allow concurrent runs
- Index both by project (auto-detected from file paths) and full-text
- The context query should produce a compact briefing, not dump raw summaries

---

## 10. Anti-Patterns

These are the things that make AI memory systems fail. Avoid them.

### 1. The Kitchen Sink Constitution
**Problem**: Putting everything in the global instruction file. 500+ lines, database schemas, API docs, business rules all jumbled together.
**Why it fails**: The AI ignores instructions in the middle of long files. Important rules get lost.
**Fix**: Strict 200-line budget. Route everything else to lower tiers.

### 2. Stale Specifications
**Problem**: Documentation that was accurate when written but hasn't been updated after code changes.
**Why it fails**: The AI follows outdated instructions and generates code that conflicts with reality.
**Fix**: Update Project Brains at the end of every work session. Flag stale Knowledge Store entries. Delete rather than leave stale.

### 3. Duplicate Knowledge
**Problem**: The same information in multiple tiers — e.g., a database gotcha in the Constitution AND the Project Brain AND the Knowledge Store.
**Why it fails**: When you update one copy and forget the others, they drift apart. The AI sees conflicting instructions.
**Fix**: Every fact has exactly one home. Use the routing table to decide where.

### 4. Generic Instructions
**Problem**: Instructions that restate what the AI already knows: "Write clean code", "Follow best practices", "Use meaningful variable names."
**Why it fails**: Wastes precious context budget on zero-information content.
**Fix**: Only store what the AI would get wrong without the instruction.

### 5. Append-Only Memory
**Problem**: Adding new entries but never removing old ones. Memory files grow forever.
**Why it fails**: Signal-to-noise ratio drops. Important entries get buried.
**Fix**: Monthly review. Delete resolved issues, consolidate similar entries, archive completed projects.

### 6. Storing Code Patterns
**Problem**: Documenting coding conventions that are visible in the existing code.
**Why it fails**: The AI can read the code. If your codebase consistently uses camelCase, you don't need to document that.
**Fix**: Only document patterns that are inconsistent, transitioning, or non-obvious.

### 7. Over-Engineering from Day One
**Problem**: Building the full 4-tier system with mining, consolidation, and specialists before you have any content.
**Why it fails**: You don't know what you need until you've worked with the AI for a while.
**Fix**: Start with Tier 1 + 2 only. Add Tier 3 when projects get complex. Add Tier 4 when you're searching for the same reference data repeatedly.

### 8. Unbounded AI Summarization
**Problem**: Using an LLM to summarize session transcripts without batch limits, processed markers, or retry caps. The summarizer tries to process all backlogged sessions at once, hits an API error, retries the full batch, and loops until your token budget is gone.
**Why it fails**: One bad run can burn through a third of your weekly tokens. The feedback loop is invisible — by the time you notice, the damage is done.
**Fix**: Hard cap of 5 sessions per run. A `summarized` flag so completed sessions are never reprocessed. Max 3 attempts before marking a session as permanently failed. A lock file to prevent overlapping runs. And critically: no retry loops inside a single run — let the cron schedule be your retry mechanism. See [Session Memory](#7-session-memory) for the full safeguard list.

---

## 11. Quick Start

Get a working system in 15 minutes. You can always add complexity later.

### Step 1: Create the Constitution (5 minutes)

Create the instruction file for your AI tool (see [Section 8](#8-tool-specific-setup) for the right filename). Copy the Tier 1 template from [Section 12](#12-templates) and customize it.

### Step 2: Create Living Memory (2 minutes)

Create your memory index file. Start it empty — it will fill naturally as you work:

```markdown
# Living Memory
<!-- Behavioral gotchas and corrections. Keep under 50 lines. -->
<!-- Link to detail files for anything longer than 3 lines. -->
```

### Step 3: Work Normally (ongoing)

Just use your AI as usual. When it gets something wrong and you correct it, **tell it to remember the correction**. When you finish significant work on a project, tell it to update (or create) the Project Brain.

### Step 4: Create Your First Project Brain (5 minutes, when needed)

When you start working on a project that has non-obvious context, create a Project Brain file. Copy the Tier 3 template from [Section 12](#12-templates) and fill in what you know.

### Step 5: Add Tier 4 When Ready (later)

When you find yourself repeatedly explaining the same reference data (database schemas, terminology, API docs), create a Knowledge Store. Start with Level 1 (markdown files) and upgrade to Level 2 (SQLite) when it outgrows that.

---

## 12. Templates

### Template A: Constitution (Tier 1)

```markdown
# [Project Name] — AI Instructions

## About
- **Project**: [Name]
- **Purpose**: [One sentence]
- **Language/Stack**: [e.g., Python/FastAPI/PostgreSQL]
- **Working Directory**: [e.g., /home/user/myproject]

## Rules
- Never assume my intent — ask before making changes if the request is ambiguous
- Be concise — don't show full code diffs unless I ask
- Prefer editing existing files over creating new ones
- Don't add features beyond what was requested
- [Add your own preferences]

## Knowledge Architecture

| Tier | Location | What belongs here |
|------|----------|-------------------|
| 1. Constitution | This file | Routing rules, preferences, tool configs |
| 2. Living Memory | MEMORY.md | Cross-session gotchas (2-3 lines each) |
| 3. Project Brains | Per-directory CLAUDE.md | Business rules, schemas, changelogs |
| 4. Knowledge Store | _knowledge/ | Full reference data, terminology |

### Before Saving Anything
1. Search first — does it already exist?
2. Route correctly — project-specific → project brain, not here
3. Never put here: schemas, API docs, project-specific rules
4. Never put in Memory: anything over 3 lines, reference data

## Project Map

| Directory | Project |
|-----------|---------|
| `src/` | Core Application |
| `tests/` | Test Suite |
| `tools/` | Internal Tools |
| [Add your directories] | |

## Project Context Protocol
1. Check for a CLAUDE.md in the project directory before making changes
2. Read it first if found
3. Create one if doing significant work and none exists
4. Update it at the end of significant work — don't ask, just do it

## Session Memory
- Database: `_knowledge/sessions.db`
- CLI: `python3 _knowledge/sessions.py`
- When starting work on a project, silently run:
    python3 _knowledge/sessions.py --context "ProjectName" --days 30
- At the end of significant sessions, log what was done:
    python3 _knowledge/sessions.py --save --project "ProjectName" --summary "what was done"

## Tools & Environment
- [Database, APIs, services, etc. — only what's needed every session]

*Last updated: [date]*
```

### Template B: Living Memory Index (Tier 2)

```markdown
# Living Memory
<!-- Cross-session behavioral gotchas. Keep under 50 lines total.
     2-3 lines per entry. Link to detail files for depth.
     Project data → project CLAUDE.md | Reference data → Knowledge Store -->

## Patterns
- [Pattern entries will accumulate here as you work]

## Gotchas
- [Gotcha entries will accumulate here]

## Preferences
- [Communication and style preferences]
```

### Template C: Memory Detail File

```markdown
---
name: [Short descriptive name]
description: [One line — used to decide relevance in future sessions]
type: [feedback | user | project | reference]
---

## What Happened
[Brief description of the situation]

## Why It Matters
[Why this is important to remember]

## How to Apply
[Concrete instruction for future sessions]
```

### Template D: Project Brain (Tier 3)

```markdown
# [Project Name] — AI Context

## Overview
[2-5 sentences: what this does, who uses it, why it exists]

## Location
- **Path**: [filesystem path]
- **URL**: [if web-accessible]
- **Data Source**: [primary database/API]

## Key Files
| File | Purpose |
|------|---------|
| [filename] | [what it does] |

## Data Architecture
[Where data comes from, source of truth, key relationships]

## Business Rules
[Domain logic the AI can't infer from code alone]

## Known Gotchas
[Things that have caused bugs or confusion]

## Decision Log
| Date | Decision | Rationale |
|------|----------|-----------|
| | | |

## Changelog
### [Date]
- [What changed and why]

*Last updated: [date]*
```

### Template E: Knowledge Store Setup Script

```python
#!/usr/bin/env python3
"""
RunawayContext Knowledge Store — Setup Script
Creates the SQLite database with FTS5 full-text search.
Run once: python3 setup_knowledge.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'knowledge.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'general',
    formula TEXT,
    description TEXT,
    source_tables TEXT,
    keywords TEXT,
    unit TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','deprecated','archived')),
    confidence REAL DEFAULT 0.5,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT CHECK(source_type IN ('table','view','api','file','cache')),
    database_name TEXT,
    key_columns TEXT, -- JSON array
    description TEXT,
    gotchas TEXT,
    domain TEXT NOT NULL DEFAULT 'general',
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS business_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'general',
    category TEXT CHECK(category IN ('gotcha','calculation','config','convention','cross_system')),
    severity TEXT DEFAULT 'info' CHECK(severity IN ('critical','warning','info')),
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS terminology (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    meaning TEXT NOT NULL,
    domain TEXT DEFAULT 'general',
    context_clues TEXT, -- comma-separated positive signals
    anti_clues TEXT,    -- comma-separated negative signals
    priority INTEGER DEFAULT 0,
    UNIQUE(term, meaning, domain)
);

CREATE TABLE IF NOT EXISTS tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url_or_path TEXT,
    description TEXT,
    target_audience TEXT,
    domain TEXT DEFAULT 'general',
    keywords TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Full-text search indexes
CREATE VIRTUAL TABLE IF NOT EXISTS metrics_fts USING fts5(
    name, domain, formula, description, keywords,
    content=metrics, content_rowid=id
);
CREATE VIRTUAL TABLE IF NOT EXISTS data_sources_fts USING fts5(
    name, database_name, description, gotchas, domain,
    content=data_sources, content_rowid=id
);
CREATE VIRTUAL TABLE IF NOT EXISTS business_rules_fts USING fts5(
    rule, domain, category,
    content=business_rules, content_rowid=id
);
CREATE VIRTUAL TABLE IF NOT EXISTS terminology_fts USING fts5(
    term, meaning, domain,
    content=terminology, content_rowid=id
);

-- Auto-sync FTS on changes
CREATE TRIGGER IF NOT EXISTS metrics_ai AFTER INSERT ON metrics BEGIN
    INSERT INTO metrics_fts(rowid, name, domain, formula, description, keywords)
    VALUES (new.id, new.name, new.domain, new.formula, new.description, new.keywords);
END;
CREATE TRIGGER IF NOT EXISTS metrics_ad AFTER DELETE ON metrics BEGIN
    INSERT INTO metrics_fts(metrics_fts, rowid, name, domain, formula, description, keywords)
    VALUES ('delete', old.id, old.name, old.domain, old.formula, old.description, old.keywords);
END;
CREATE TRIGGER IF NOT EXISTS metrics_au AFTER UPDATE ON metrics BEGIN
    INSERT INTO metrics_fts(metrics_fts, rowid, name, domain, formula, description, keywords)
    VALUES ('delete', old.id, old.name, old.domain, old.formula, old.description, old.keywords);
    INSERT INTO metrics_fts(rowid, name, domain, formula, description, keywords)
    VALUES (new.id, new.name, new.domain, new.formula, new.description, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS ds_ai AFTER INSERT ON data_sources BEGIN
    INSERT INTO data_sources_fts(rowid, name, database_name, description, gotchas, domain)
    VALUES (new.id, new.name, new.database_name, new.description, new.gotchas, new.domain);
END;
CREATE TRIGGER IF NOT EXISTS ds_ad AFTER DELETE ON data_sources BEGIN
    INSERT INTO data_sources_fts(data_sources_fts, rowid, name, database_name, description, gotchas, domain)
    VALUES ('delete', old.id, old.name, old.database_name, old.description, old.gotchas, old.domain);
END;
CREATE TRIGGER IF NOT EXISTS ds_au AFTER UPDATE ON data_sources BEGIN
    INSERT INTO data_sources_fts(data_sources_fts, rowid, name, database_name, description, gotchas, domain)
    VALUES ('delete', old.id, old.name, old.database_name, old.description, old.gotchas, old.domain);
    INSERT INTO data_sources_fts(rowid, name, database_name, description, gotchas, domain)
    VALUES (new.id, new.name, new.database_name, new.description, new.gotchas, new.domain);
END;

CREATE TRIGGER IF NOT EXISTS br_ai AFTER INSERT ON business_rules BEGIN
    INSERT INTO business_rules_fts(rowid, rule, domain, category)
    VALUES (new.id, new.rule, new.domain, new.category);
END;
CREATE TRIGGER IF NOT EXISTS br_ad AFTER DELETE ON business_rules BEGIN
    INSERT INTO business_rules_fts(business_rules_fts, rowid, rule, domain, category)
    VALUES ('delete', old.id, old.rule, old.domain, old.category);
END;
CREATE TRIGGER IF NOT EXISTS br_au AFTER UPDATE ON business_rules BEGIN
    INSERT INTO business_rules_fts(business_rules_fts, rowid, rule, domain, category)
    VALUES ('delete', old.id, old.rule, old.domain, old.category);
    INSERT INTO business_rules_fts(rowid, rule, domain, category)
    VALUES (new.id, new.rule, new.domain, new.category);
END;

CREATE TRIGGER IF NOT EXISTS term_ai AFTER INSERT ON terminology BEGIN
    INSERT INTO terminology_fts(rowid, term, meaning, domain)
    VALUES (new.id, new.term, new.meaning, new.domain);
END;
CREATE TRIGGER IF NOT EXISTS term_ad AFTER DELETE ON terminology BEGIN
    INSERT INTO terminology_fts(terminology_fts, rowid, term, meaning, domain)
    VALUES ('delete', old.id, old.term, old.meaning, old.domain);
END;
CREATE TRIGGER IF NOT EXISTS term_au AFTER UPDATE ON terminology BEGIN
    INSERT INTO terminology_fts(terminology_fts, rowid, term, meaning, domain)
    VALUES ('delete', old.id, old.term, old.meaning, old.domain);
    INSERT INTO terminology_fts(rowid, term, meaning, domain)
    VALUES (new.id, new.term, new.meaning, new.domain);
END;

-- Updated-at triggers
CREATE TRIGGER IF NOT EXISTS metrics_updated AFTER UPDATE ON metrics BEGIN
    UPDATE metrics SET updated_at = datetime('now') WHERE id = new.id;
END;
CREATE TRIGGER IF NOT EXISTS ds_updated AFTER UPDATE ON data_sources BEGIN
    UPDATE data_sources SET updated_at = datetime('now') WHERE id = new.id;
END;
CREATE TRIGGER IF NOT EXISTS br_updated AFTER UPDATE ON business_rules BEGIN
    UPDATE business_rules SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

def setup():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.close()
    print(f"Knowledge Store created at: {DB_PATH}")
    print("Tables: metrics, data_sources, business_rules, terminology, tools")
    print("FTS5 indexes: metrics_fts, data_sources_fts, business_rules_fts, terminology_fts")
    print("\\nReady. Add entries via knowledge.py or direct SQL.")

if __name__ == '__main__':
    setup()
```

### Template F: Knowledge Store CLI

```python
#!/usr/bin/env python3
"""
RunawayContext Knowledge Store — Query CLI
Usage:
  python3 knowledge.py --ask "your question"        # Natural language search
  python3 knowledge.py --term "ABC" --context "..."  # Disambiguate a term
  python3 knowledge.py --source "table_name"         # Find data sources
  python3 knowledge.py --rules                       # List business rules
  python3 knowledge.py --tools "keyword"             # Search tools
  python3 knowledge.py --add-rule "rule text" --domain "domain" --severity "warning"
  python3 knowledge.py --add-term "TERM" --meaning "..." --domain "..."
  python3 knowledge.py --stats                       # Database statistics
"""

import sqlite3
import argparse
import json
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'knowledge.db')

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def search_all(query):
    """Search across all knowledge tables using FTS5 with LIKE fallback."""
    db = get_db()
    results = {'metrics': [], 'data_sources': [], 'business_rules': [], 'terminology': [], 'tools': []}

    # Try FTS5 first
    fts_tables = {
        'metrics': ('metrics_fts', ['name', 'domain', 'formula', 'description']),
        'data_sources': ('data_sources_fts', ['name', 'database_name', 'description', 'gotchas']),
        'business_rules': ('business_rules_fts', ['rule', 'domain', 'category']),
        'terminology': ('terminology_fts', ['term', 'meaning', 'domain']),
    }

    for table, (fts_table, _) in fts_tables.items():
        try:
            rows = db.execute(
                f"SELECT t.* FROM {table} t JOIN {fts_table} f ON t.id = f.rowid "
                f"WHERE {fts_table} MATCH ? AND t.status = 'active' ORDER BY rank LIMIT 10",
                (query,)
            ).fetchall()
            results[table] = [dict(r) for r in rows]
        except Exception:
            pass

    # LIKE fallback for tables with no FTS results
    like_query = f"%{query}%"
    for table, (_, columns) in fts_tables.items():
        if not results[table]:
            where = " OR ".join(f"{col} LIKE ?" for col in columns)
            params = [like_query] * len(columns)
            try:
                if table in ('metrics', 'data_sources'):
                    rows = db.execute(
                        f"SELECT * FROM {table} WHERE ({where}) AND status = 'active' LIMIT 10",
                        params
                    ).fetchall()
                else:
                    rows = db.execute(
                        f"SELECT * FROM {table} WHERE ({where}) LIMIT 10", params
                    ).fetchall()
                results[table] = [dict(r) for r in rows]
            except Exception:
                pass

    # Tools (no FTS, use LIKE)
    try:
        rows = db.execute(
            "SELECT * FROM tools WHERE name LIKE ? OR description LIKE ? OR keywords LIKE ? LIMIT 10",
            (like_query, like_query, like_query)
        ).fetchall()
        results['tools'] = [dict(r) for r in rows]
    except Exception:
        pass

    db.close()
    return results

def disambiguate_term(term, context_hint=""):
    """Look up a term and score meanings by context."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM terminology WHERE UPPER(term) = UPPER(?) ORDER BY priority DESC",
        (term,)
    ).fetchall()
    db.close()

    if not rows:
        return f"Term '{term}' not found in terminology."

    if not context_hint:
        return [dict(r) for r in rows]

    # Score by context clues
    hints = [h.strip().lower() for h in context_hint.split(",")]
    scored = []
    for row in rows:
        score = row['priority']
        clues = (row['context_clues'] or "").lower().split(",")
        anti = (row['anti_clues'] or "").lower().split(",")
        for hint in hints:
            if any(hint in c for c in clues):
                score += 10
            if any(hint in a for a in anti):
                score -= 10
        scored.append((score, dict(row)))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored]

def search_sources(query):
    """Search data sources."""
    db = get_db()
    like = f"%{query}%"
    rows = db.execute(
        "SELECT * FROM data_sources WHERE (name LIKE ? OR description LIKE ? OR database_name LIKE ?) "
        "AND status = 'active' LIMIT 20",
        (like, like, like)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

def list_rules(domain=None):
    """List business rules, optionally filtered by domain."""
    db = get_db()
    if domain:
        rows = db.execute(
            "SELECT * FROM business_rules WHERE domain = ? AND status = 'active' ORDER BY severity, category",
            (domain,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM business_rules WHERE status = 'active' ORDER BY domain, severity, category"
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]

def search_tools(query=None, audience=None):
    """Search or list tools."""
    db = get_db()
    if audience:
        rows = db.execute(
            "SELECT * FROM tools WHERE target_audience LIKE ?",
            (f"%{audience}%",)
        ).fetchall()
    elif query:
        like = f"%{query}%"
        rows = db.execute(
            "SELECT * FROM tools WHERE name LIKE ? OR description LIKE ? OR keywords LIKE ?",
            (like, like, like)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM tools ORDER BY domain, name").fetchall()
    db.close()
    return [dict(r) for r in rows]

def add_rule(rule, domain, category='gotcha', severity='info'):
    """Add a business rule."""
    db = get_db()
    db.execute(
        "INSERT INTO business_rules (rule, domain, category, severity) VALUES (?, ?, ?, ?)",
        (rule, domain, category, severity)
    )
    db.commit()
    db.close()
    return "Rule added."

def add_term(term, meaning, domain='general', context_clues='', anti_clues=''):
    """Add a terminology entry."""
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO terminology (term, meaning, domain, context_clues, anti_clues) "
        "VALUES (?, ?, ?, ?, ?)",
        (term, meaning, domain, context_clues, anti_clues)
    )
    db.commit()
    db.close()
    return "Term added."

def get_stats():
    """Database statistics."""
    db = get_db()
    stats = {}
    for table in ['metrics', 'data_sources', 'business_rules', 'terminology', 'tools']:
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        stats[table] = count
    db.close()
    return stats

def format_results(results, as_json=False):
    """Format search results for display."""
    if as_json:
        return json.dumps(results, indent=2, default=str)

    output = []
    for table, rows in results.items():
        if not rows:
            continue
        output.append(f"\n=== {table.upper().replace('_', ' ')} ({len(rows)} results) ===")
        for row in rows:
            if table == 'metrics':
                output.append(f"  [{row.get('domain','')}] {row['name']}: {row.get('formula','') or row.get('description','')}")
            elif table == 'data_sources':
                gotcha = f" ⚠️ {row['gotchas']}" if row.get('gotchas') else ""
                output.append(f"  [{row.get('source_type','')}] {row['name']} ({row.get('database_name','')}){gotcha}")
            elif table == 'business_rules':
                sev = {'critical': '🔴', 'warning': '🟡', 'info': 'ℹ️'}.get(row.get('severity',''), '')
                output.append(f"  {sev} [{row.get('domain','')}] {row['rule']}")
            elif table == 'terminology':
                output.append(f"  {row['term']} = {row['meaning']} (domain: {row.get('domain','')})")
            elif table == 'tools':
                output.append(f"  {row['name']}: {row.get('description','')}")

    return "\n".join(output) if output else "No results found."

def main():
    parser = argparse.ArgumentParser(description='RunawayContext Knowledge Store')
    parser.add_argument('--ask', help='Natural language search across all tables')
    parser.add_argument('--term', help='Look up a term/abbreviation')
    parser.add_argument('--context', dest='context_hint', help='Context hint for term disambiguation')
    parser.add_argument('--source', help='Search data sources')
    parser.add_argument('--rules', action='store_true', help='List business rules')
    parser.add_argument('--tools', nargs='?', const='', help='Search/list tools')
    parser.add_argument('--audience', help='Filter tools by target audience')
    parser.add_argument('--domain', help='Filter by domain')
    parser.add_argument('--add-rule', help='Add a business rule')
    parser.add_argument('--add-term', help='Add a term')
    parser.add_argument('--meaning', help='Meaning for --add-term')
    parser.add_argument('--severity', default='info', help='Severity for --add-rule')
    parser.add_argument('--category', default='gotcha', help='Category for --add-rule')
    parser.add_argument('--context-clues', default='', help='Context clues for --add-term')
    parser.add_argument('--anti-clues', default='', help='Anti-clues for --add-term')
    parser.add_argument('--stats', action='store_true', help='Show database statistics')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    if args.ask:
        results = search_all(args.ask)
        print(format_results(results, args.json))
    elif args.term:
        results = disambiguate_term(args.term, args.context_hint or "")
        if isinstance(results, str):
            print(results)
        elif args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            for r in results:
                print(f"  {r['term']} = {r['meaning']} (domain: {r.get('domain','')}, priority: {r.get('priority',0)})")
    elif args.source:
        results = search_sources(args.source)
        print(format_results({'data_sources': results}, args.json))
    elif args.rules:
        results = list_rules(args.domain)
        print(format_results({'business_rules': results}, args.json))
    elif args.tools is not None:
        results = search_tools(args.tools or None, args.audience)
        print(format_results({'tools': results}, args.json))
    elif args.add_rule:
        print(add_rule(args.add_rule, args.domain or 'general', args.category, args.severity))
    elif args.add_term:
        if not args.meaning:
            print("Error: --meaning is required with --add-term")
            sys.exit(1)
        print(add_term(args.add_term, args.meaning, args.domain or 'general',
                       args.context_clues, args.anti_clues))
    elif args.stats:
        stats = get_stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print("Knowledge Store Statistics:")
            for table, count in stats.items():
                print(f"  {table}: {count} entries")
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
```

---

## 13. v1 → v2 Upgrade

If you already have a v1 RunawayContext install, your `~/_knowledge/sessions.db` is a single file containing `knowledge_chunks`, `lessons_learned`, `sessions`, and the junctions. v2 splits this into two files (`knowledge.db` + `sessions.db`), adds new columns, and introduces new tables (`project_context_card`, junction renames). All existing rows are preserved.

**Recipe:**

```bash
# 1. Back up first (the migrator also backs up — be paranoid)
cp ~/_knowledge/sessions.db ~/_knowledge/sessions.db.manual.bak

# 2. Run the migrator (it does not touch the original)
python3 lib/migrate_v1_to_v2.py --v1-db ~/_knowledge/sessions.db
```

**What the migrator does:**
1. Backs up the v1 file to `sessions.db.v1.bak` next to it.
2. Lifts `knowledge_chunks` + `lessons_learned` into a new `knowledge.db`.
3. Creates a new `sessions.db` with `session_logs` (the v1 `sessions` table renamed).
4. Applies v2 schema migrations to both DBs (adds `severity`, `status`, `slug`, `what_happened`, `why`, `the_fix`, `prevention_rule`, `project_tags`, etc. on lessons; creates `project_context_card`, `lesson_chunks`, `chunk_sessions` junctions).
5. Backfills v1 `project` → `project_tags`, v1 `lesson` → `prevention_rule`, v1 `context` → `what_happened`.
6. Verifies row counts on both sides.

**What you do after the migrator runs:**
1. Edit `lib/_project_slugs.py` — add your project slugs to `CANONICAL_PROJECT_SLUGS` and path mappings to `PATH_TO_SLUG`.
2. Run `python3 lib/ll_brief.py --rebuild-brief <slug>` for each project. This builds the new `project_context_card` rows from your existing tagged content.
3. Set `md_path` on each card so the regenerator knows where to write the slim brief.
4. Run `python3 lib/ll_brief.py --rebuild-md <slug>` to write the auto-generated Tier 3 file. Your existing project CLAUDE.md is overwritten — the original v1 file is in your shell's git history, and its content was already migrated to KS rows.
5. Wire up the drift detector ([Section 15](#15-drift-detection-v2)).

**Distinction: v1 → v2 vs fresh install.** The migrator is ONLY for existing v1 users. For new installs, run `run_RunawayContext.md` instead — it scrapes your existing READMEs / AI notes / config files / project docs and builds the KS from those sources. Don't run the migrator on a fresh install.

---

## 14. Write Guards (v2)

In v1, anyone with a Python REPL could insert a row into `knowledge_chunks` or `lessons_learned` with no project tag and no source attribution. Over time, untagged content accumulated, and queries-by-project missed it. v2 closes this at the write side.

**Three guards apply to every write:**

### 14.1 Required `--project` slug

Every CLI write requires `--project` (or `--ll-projects` for lessons). Argparse rejects the call before touching the DB:

```bash
$ python3 lib/propose_knowledge.py --topic auth --title "Auth" --body "..."
propose_knowledge.py: error: the following arguments are required: --project
```

Same for `--log-lesson`. There's no "I'll tag it later" option.

### 14.2 Canonical slug validation

The slug must match an entry in `CANONICAL_PROJECT_SLUGS` in `lib/_project_slugs.py`:

```bash
$ python3 lib/propose_knowledge.py --project parkers --topic auth --title "Auth" --body "..."
propose_knowledge.py: error: argument --project: Unknown project slug(s): ['parkers'].
Known slugs in this install: ['accounting', 'api', 'frontend', 'general', 'mobile', 'parker'].
To add a new slug: append it to CANONICAL_PROJECT_SLUGS in lib/_project_slugs.py.
```

Typos like `parkers` (should be `parker`) are caught and returned with the valid slug list. If you genuinely need a new slug, you append it to the set first — that's a deliberate, traceable change.

### 14.3 Auto-stamped `source_user`

Every write captures `$SUDO_USER` (if elevated) or `$USER` and stamps it on the row:

```bash
$ python3 lib/propose_knowledge.py --project myapp --topic auth --title "Auth" --body "..."
KS#142 created (project=myapp, tags=['myapp'], topic=auth, source_user=alice)
```

You always know who wrote what. In multi-user setups (Section 16), this is the audit trail.

### 14.4 Auto-mining: junk path rejection

If you build an automated mining script that reads files and inserts rows, route every insert through `slug_from_path()`:

```python
from _project_slugs import slug_from_path

slug = slug_from_path(file_path)
if slug is None:
    return False  # junk path — never reaches the DB
```

`slug_from_path()` returns `None` for paths matching `node_modules`, `vendor/`, `.bak`, `.backup.`, `__pycache__`, `/dist/`, `/build/`, etc. The full list is in `JUNK_PATH_MARKERS` in `lib/_project_slugs.py` — extend as needed.

**Why this matters in practice:** during the v1 → v2 migration on the project I built this for, 226 KS rows came from a backup directory (`POD.backup.2026-04-22/CLAUDE.md`) that had been accidentally scanned. Without `slug_from_path()`, every miner-driven scan was a vector for that kind of pollution. With it, those paths can't insert rows at all.

---

## 15. Drift Detection (v2)

The Tier 3 line cap is enforced by the regenerator — but the regenerator only runs when called. Between calls, someone could hand-edit a project brief and bloat it. The drift detector is the safety net.

**Two mechanisms** depending on which AI tool you use:

### 15.1 Stop hook (Claude Code CLI, anything that fires Stop hooks)

Add to your tool's settings. For Claude Code (`~/.claude/settings.json`):

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "/path/to/RunawayContext/bin/check_md_drift.sh"
      }]
    }]
  }
}
```

Fires at session end. The script walks every `project_context_card` row that has an `md_path`, reads the actual file's line count, and compares to `md_line_cap`. If any file is over, it warns to stderr:

```
⚠ CLAUDE.md drift: /path/to/myapp/CLAUDE.md is 287 lines (cap 150, project=myapp)
  → regen: python3 lib/ll_brief.py --rebuild-md myapp

1 project CLAUDE.md file(s) over their cap.
Hand-edits OUTSIDE the PRESERVE_START / PRESERVE_END markers will be wiped on next regen.
```

Non-blocking — the script always exits 0. The point is visibility, not failure.

### 15.2 Cron / launchd watcher (VS Code Claude extension, anything Stop-hook-less)

For AI tools that don't fire Stop hooks (the VS Code Claude extension is the common case), a scheduled watcher is the alternative.

Linux:
```bash
crontab -e
*/10 * * * * /path/to/RunawayContext/bin/md_drift_watcher.sh
```

macOS launchd: see `bin/md_drift_watcher.sh` for the plist template. `StartInterval` of 600 seconds.

The watcher writes:
- **Log file** at `~/_knowledge/logs/md_drift_watcher.log` — append-only event log with timestamps. Hourly heartbeat when clean.
- **Snapshot file** at `~/_knowledge/logs/md_drift_snapshot.psv` — pipe-separated current-state for any dashboard you want to surface drift in.

### 15.3 Why both?

- **Stop hook** is real-time. The instant a session ends with a bloated file, you know.
- **Cron watcher** catches drift between sessions, including drift caused by external editors, git pulls, or anyone working without the Stop hook installed.

Run both if you can. The Stop hook fires fast; the watcher is the safety net that catches anything the Stop hook misses.

---

## 16. Multi-User Setup (v2)

If you're running RunawayContext on a shared host (team server, family computer, multi-Linux-user box), every Claude Code / AI-tool user needs the same setup: Stop hook in their settings, seeded MEMORY.md pointing at the new system.

`bin/setup_user_protections.sh` does this in one shot:

```bash
# Dry run first — see what it would do
sudo bash bin/setup_user_protections.sh --all

# Apply
sudo bash bin/setup_user_protections.sh --all --apply
```

**What it does** (idempotent — safe to re-run):
1. Discovers every user with a `~/.claude/` dir under `/home/` (Linux) or `/Users/` (macOS).
2. **Backs up existing files in place** before any modification: `settings.json.pre-rc-v2.YYYYMMDD.bak` and `MEMORY.md.pre-rc-v2.YYYYMMDD.bak` written next to each file.
3. Merges the Stop hook into each user's `settings.json` (preserves their existing keys — model, permissions, etc.).
4. If MEMORY.md exists, **prepends** the new-system pointer block; existing notes are preserved underneath. If MEMORY.md doesn't exist, creates it with just the pointer.

**Reversion** is a single `mv` per file:

```bash
mv ~/.claude/settings.json.pre-rc-v2.20260504.bak ~/.claude/settings.json
mv ~/.claude/MEMORY.md.pre-rc-v2.20260504.bak     ~/.claude/MEMORY.md
```

**Cross-platform:** the script detects `Darwin` → `/Users` vs `Linux` → `/home`. Works on either.

**What it does NOT do:**
- Install RunawayContext itself (clone the repo first).
- Fork the KS — every user shares one `knowledge.db` / `sessions.db` at `$RC_KS_DIR`. That's the design: one institutional brain, many readers + writers, attribution via `source_user`.
- Run the v1 → v2 migration. That's a separate concern.

**Per-user CLI access:** in a shared install, you can either let users invoke the full path (`python3 /opt/RunawayContext/lib/ll_brief.py --brief myapp`) or symlink the most-used commands into `/usr/local/bin/`:

```bash
sudo ln -s /opt/RunawayContext/lib/ll_brief.py /usr/local/bin/rc-brief
sudo chmod +x /usr/local/bin/rc-brief
# Now: rc-brief --brief myapp
```

---

## Summary

RunawayContext is not a product — it's a **pattern**. The specific tools don't matter. What matters is:

1. **Tier your knowledge** — small always-loaded, big on-demand
2. **Route correctly** — every fact has one home
3. **Focus on what the AI gets wrong** — don't store common knowledge
4. **Generate, don't curate, the per-project tier** — Tier 3 is built from the DB, not edited by hand
5. **Guard the write side** — typos and untagged content can't reach the DB
6. **Detect drift, don't trust discipline** — every file has a cap, and a watcher tells you when the cap is breached
7. **Start simple** — Constitution + Memory first, add tiers as needed; the executable prompt sets up the rest

The AI doesn't need to be smarter. It needs to **remember** — and the system needs to stay clean without requiring you to be careful. Give it a brain, give the brain a maintenance contract, and it will surprise you with what it can do.

---

*RunawayContext v2.0 — Developed from real-world use across thousands of AI coding sessions across multiple projects, multiple users, and one painful observation: a memory system that depends on discipline drifts. So we put the discipline into the schema, the CLI write guards, and a drift detector — not into a "be careful" line in the docs.*

*Built on research from: arXiv (Codified Context), Manus (Context Engineering), Mem0, OpenAI Codex, and the Claude Code community.*
