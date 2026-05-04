# RunawayContext

**A universal framework for giving AI coding assistants persistent memory and project intelligence across sessions.**

> **v2.0 — May 2026.** Major architectural upgrade. Project Brains are now auto-generated artifacts (not hand-edited markdown), all writes are project-tagged and validated, and a drift detector keeps the always-loaded files honest. **v1 users see [v1 → v2 upgrade](#v1--v2-upgrade)** below — no destructive changes, your existing knowledge is preserved.

> **Formerly SuperContext.** Same system, new name — renamed to avoid conflicts with existing projects.

Stop re-explaining your codebase every conversation. RunawayContext is a structured, tiered knowledge system that makes any AI coding assistant remember, learn, and get smarter over time — without growing into the giant context-eating instruction file you're trying to escape.

---

## The Problem

Every AI coding session starts from zero. Your assistant doesn't remember yesterday's decisions, doesn't know your project's business rules, and will happily repeat the same mistakes you corrected last week. Context windows are finite, and copy-pasting old conversations doesn't scale.

The common fix — one giant instruction file — creates its own problems. A 2,000-line `CLAUDE.md` or `.cursorrules` eats your context window before you've even asked a question, buries critical rules in walls of text, and becomes impossible to maintain. Your AI ends up ignoring half of it anyway.

**The harder problem** is keeping the system honest over time. Even if you start small, instruction files drift. Every session adds a "while I'm here, let me note this." Six months later you're back to a 2,000-line file. The rules quietly stop being enforced.

## The Solution

RunawayContext takes the opposite approach — **small, focused files loaded only when relevant, and physics-enforced discipline that keeps them small.**

- Always-loaded **Constitution** stays under 200 lines.
- Project-specific **briefs** are auto-generated from a database — hand-edits outside a tiny PRESERVE block get wiped on next regen.
- Deep reference data lives in a **searchable database** (SQLite + FTS5) and is retrieved on demand.
- Every write is **project-tagged and validated** at write time — typos and untagged content get rejected, not silently saved.
- A **drift detector** runs on session end (and as a cron / launchd watcher for AI tools where Stop hooks don't fire) and warns you the moment any file grows past its cap.

The result: minimal token overhead, fast context loading, and a system that stays clean without requiring discipline.

It implements a **4-tier knowledge architecture** that mirrors how human experts organize information — from always-available muscle memory to deep reference material retrieved on demand:

| Tier | Name | Loaded | Purpose | Budget |
|------|------|--------|---------|--------|
| 1 | **Constitution** | Always | Global directives, routing rules, user preferences | ≤ 200 lines |
| 2 | **Living Memory** | Always | Cross-session behavioral gotchas, with `LL#N` / `KS#N` pointers | ≤ 50 lines |
| 3 | **Project Briefs** | On project entry | Auto-generated from the DB. Hand-edits outside the PRESERVE block are wiped. | ≤ 150 lines (enforced) |
| 4 | **Knowledge Store** (`knowledge.db`) | On demand | Chunks, lessons, project manifests, junctions | unlimited |
| 4b | **Sessions** (`sessions.db`) | On demand | Full conversation transcripts. Cross-DB JOIN to KS via `conversation_id`. | unlimited |

Plus **Session Memory** — automatic logging of every conversation so your AI can recall what happened last Tuesday.

## What's New in v2

| Feature | What it does |
|---------|--------------|
| **`project_context_card`** | A new "manifest" table — one row per project. Holds top warnings, active LL ids, active chunk ids. Auto-rebuilt from any tagged source row. The first thing your AI queries when entering a project. |
| **Auto-generated Tier 3** | Project briefs are generated from the DB, with a `<!-- PRESERVE_START -->` / `<!-- PRESERVE_END -->` block for the human-curated overview. Everything else regenerates on every rebuild. Hand-edits drift gets physics-enforced, not policy-enforced. |
| **Required project tagging** | Every `--log-lesson` and `--propose-knowledge` call requires a `--project` slug from a canonical list. Typos and untagged writes are rejected at the CLI. |
| **Auto-tagged miner** | If you use a discovery / mining script, it auto-derives slugs from file paths and rejects junk paths (`node_modules`, `vendor/`, `.bak`, etc.) before they ever reach the DB. |
| **Drift detector** | Stop-hook script + cron / launchd watcher. Surfaces any project brief that has grown past its line cap. Logs to a snapshot file a dashboard can scrape. |
| **Split DB design** | `knowledge.db` (curated, small, frequently backed up) and `sessions.db` (heavy transcripts, retained longer). Linked via `conversation_id` and ATTACH at query time. |
| **Multi-user setup helper** | One script provisions Stop hooks + seeded MEMORY.md across every Claude user on a shared host (Mac or Linux). Backs up existing files in place before any modification. |
| **Lifecycle on lessons** | LL gains `severity` (critical/warning/info), `status` (active/superseded/archived), and `superseded_by` for graceful evolution. |

## What's Included

```
RunawayContext/
├── README.md                  ← you are here
├── BOOTSTRAP.md               manual walkthrough of every tier
├── RUNAWAYCONTEXT.md          full theory + reference guide
├── run_RunawayContext.md      executable prompt — hand to your AI
├── CHANGELOG.md               v1 → v2 migration notes
├── schema/                    SQL migrations for knowledge.db + sessions.db
├── lib/                       Python CLI + helpers
│   ├── setup_db.py            schema bootstrap (fresh installs)
│   ├── migrate_v1_to_v2.py    v1 → v2 upgrader (only for v1 users)
│   ├── _project_slugs.py      canonical slug taxonomy (you edit this)
│   ├── ll_brief.py            lessons + project_context_card CLI
│   ├── md_writer.py           project brief regenerator
│   └── propose_knowledge.py   knowledge_chunks write guard
└── bin/                       shell scripts
    ├── check_md_drift.sh      Stop-hook drift detector
    ├── md_drift_watcher.sh    cron / launchd watcher
    ├── backup_db.sh           dated SQLite snapshots
    └── setup_user_protections.sh  multi-user rollout helper
```

## Quick Start

### Option A: Full Setup — Existing Projects (Automated)
1. Clone this repo: `git clone https://github.com/sms021/RunawayContext.git`
2. Open your AI coding tool (Claude Code, Cursor, Copilot, etc.) in your project's directory
3. Tell it: *"Please read and execute `path/to/RunawayContext/run_RunawayContext.md`"*
4. Answer the orientation questions
5. The AI scrapes your existing READMEs / AI notes / config files / project docs, builds the canonical slug list, populates `knowledge.db`, generates the Constitution and per-project briefs, and wires up Stop hooks. ~10 minutes.

### Option B: Manual Setup (Build It Yourself)
1. Read [BOOTSTRAP.md](BOOTSTRAP.md) — walks through each tier, explains why it exists, and gives you the exact commands to build it
2. Build each tier by hand
3. Good for: learning the system, custom environments, or teams that want to understand before automating

### v1 → v2 Upgrade
If you already have a v1 RunawayContext install (single `sessions.db`):

```bash
# 1. Back up first (the migration script also backs up, but be paranoid)
cp ~/_knowledge/sessions.db ~/_knowledge/sessions.db.manual.bak

# 2. Run the migrator
python3 lib/migrate_v1_to_v2.py --v1-db ~/_knowledge/sessions.db
```

The migrator splits your single v1 database into v2's `knowledge.db` (chunks + lessons + new tables) and `sessions.db` (transcripts), backfills the new columns, and verifies row counts. Original v1 file is preserved unchanged. See [CHANGELOG.md](CHANGELOG.md) for the full upgrade story.

## Works With

- **Claude Code** (CLI & VS Code) — full support including PostCompact + Stop hooks
- **Cursor** — uses `.cursor/rules/` directory
- **GitHub Copilot** — uses `.github/copilot-instructions.md`
- **OpenAI Codex CLI** — uses `AGENTS.md`
- **Aider** — uses `.aider.conf.yml` + conventions files
- **Windsurf** — uses `.windsurfrules`
- **Any tool that reads markdown** — the core architecture is tool-agnostic

For VS Code-style extensions where Stop hooks don't fire, the cron / launchd `md_drift_watcher.sh` handles drift detection independently.

## Key Principles

- **Route, don't dump** — Different knowledge belongs at different depths. Business rules go in project briefs, not the Constitution.
- **Budget every tier** — Constitution ≤ 200 lines, Living Memory ≤ 50, Project Briefs ≤ 150. Constraints force quality. v2 enforces these constraints in code, not just policy.
- **Earn your place** — Knowledge enters Living Memory only after proving it prevents real mistakes. Lessons learned have severity and status — they evolve over time.
- **Decay, don't hoard** — `status='superseded'` retires old lessons gracefully. The drift watcher tells you when a brief is bloating.
- **Tag at the write side** — Every chunk and lesson is project-tagged. Typos can't sneak in. The canonical slug list is your install's contract.
- **Session continuity** — Log every conversation. Lessons cite their source session via `conversation_id`. ATTACH `sessions.db` at query time to drill in.

## Background

This system was developed over hundreds of real-world sessions building construction management integrations across Vista, Procore, Monday.com, and other enterprise systems. v2's enforcement-first design came from watching v1's policy-only constraints quietly drift over six months of use. The fix was to put the discipline into the schema, the CLI write guards, and a drift detector — not into a "be careful" line in the docs.

Draws on research from:
- Academic work on codified context in LLM-assisted development
- Open-source projects (Mem0, OpenMemory, Brain-Agent)
- Industry practice (Manus context engineering, Spotify, OpenAI Codex)
- Hard-won lessons from daily multi-project, multi-user AI workflows

The full research findings and references are in [RUNAWAYCONTEXT.md](RUNAWAYCONTEXT.md).

## License

MIT — use it however you want.

---

*Built by [Runaway Ideas](https://github.com/sms021) — a construction company that accidentally got really good at AI infrastructure.*
