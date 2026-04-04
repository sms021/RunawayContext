# SuperContext

**A universal framework for giving AI coding assistants persistent memory and project intelligence across sessions.**

Stop re-explaining your codebase every conversation. SuperContext is a structured, tiered knowledge system that makes any AI coding assistant remember, learn, and get smarter over time.

---

## The Problem

Every AI coding session starts from zero. Your assistant doesn't remember yesterday's decisions, doesn't know your project's business rules, and will happily repeat the same mistakes you corrected last week. Context windows are finite, and copy-pasting old conversations doesn't scale.

The common fix — one giant instruction file — creates its own problems. A 2,000-line `CLAUDE.md` or `.cursorrules` eats your context window before you've even asked a question, buries critical rules in walls of text, and becomes impossible to maintain. Your AI ends up ignoring half of it anyway.

## The Solution

SuperContext takes the opposite approach — **small, focused files loaded only when relevant.** Your always-loaded Constitution stays under 200 lines. Project-specific knowledge loads only when you're working in that project. Deep reference data lives in a searchable database and is retrieved on demand. The result: minimal token overhead, fast context loading, and every piece of knowledge exactly where your AI needs it.

It implements a **4-tier knowledge architecture** that mirrors how human experts organize information — from always-available muscle memory to deep reference material retrieved on demand:

| Tier | Name | Loaded | Purpose |
|------|------|--------|---------|
| 1 | **Constitution** | Always | Global directives, routing rules, user preferences (~200 lines) |
| 2 | **Living Memory** | Always | Cross-session behavioral gotchas, lessons learned (~50 lines) |
| 3 | **Project Brains** | On entry | Per-project business rules, schemas, changelogs (unlimited) |
| 4 | **Knowledge Store** | On demand | Searchable database of infrastructure, APIs, metrics (unlimited) |

Plus **Session Memory** — automatic logging of every conversation so your AI can recall what happened last Tuesday.

## What's Included

| File | What It Does |
|------|-------------|
| [**SUPERCONTEXT.md**](SUPERCONTEXT.md) | The full theory and reference guide — 12 sections covering architecture, each tier in detail, session memory, tool-specific setup, scaling, and anti-patterns |
| [**run_SuperContext.md**](run_SuperContext.md) | The executable prompt — hand this to your AI and say "run this." It discovers your projects, migrates existing content, builds all 4 tiers, sets up session logging, and produces a clean system. No manual steps. |
| [**BOOTSTRAP.md**](BOOTSTRAP.md) | A simpler starting point for greenfield setups — builds Tier 1 + 2 through guided Q&A |

## Quick Start

### Option A: Full Setup (Existing Projects)
1. Copy `SUPERCONTEXT.md` and `run_SuperContext.md` into your project root
2. Open your AI coding tool (Claude Code, Cursor, Copilot, etc.)
3. Tell it: *"Please read and execute run_SuperContext.md"*
4. Answer 4 questions about your setup
5. The AI builds everything autonomously (~10 minutes)

### Option B: Greenfield (New Project)
1. Copy `BOOTSTRAP.md` into your project root
2. Tell your AI: *"Please read and execute BOOTSTRAP.md"*
3. Answer questions one at a time — it builds a starter Constitution and Living Memory

## Works With

- **Claude Code** (CLI & VS Code) — full support including auto-capture hooks
- **Cursor** — uses `.cursorrules` + `.cursor/` directory
- **GitHub Copilot** — uses `.github/copilot-instructions.md`
- **OpenAI Codex CLI** — uses `AGENTS.md`
- **Aider** — uses `.aider.conf.yml` + conventions files
- **Windsurf** — uses `.windsurfrules`
- **Any tool that reads markdown** — the core architecture is tool-agnostic

## How It Works

The executable prompt (`run_SuperContext.md`) runs through 7 phases:

1. **Orient** — Detects your AI tool and asks about your setup
2. **Discover** — Scans your codebase for existing config files, docs, and knowledge artifacts
3. **Knowledge Store** — Builds a searchable reference (markdown for small projects, SQLite + FTS5 for larger ones)
4. **Project Brains** — Creates per-project context files with business rules, schemas, and changelogs
5. **Session Memory** — Sets up automatic conversation logging with SQLite, CLI tools, and capture hooks
6. **Living Memory** — Builds the always-loaded behavioral index
7. **Constitution** — Generates the master instruction file (~200 lines) that ties everything together

Content from existing files (READMEs, AI notes, config files) is automatically migrated and classified into the right tier.

## Key Principles

- **Route, don't dump** — Different knowledge belongs at different depths. Business rules go in project brains, not the master config.
- **Budget every tier** — The Constitution has a 200-line budget. Living Memory has 50. Constraints force quality.
- **Earn your place** — Knowledge enters Living Memory only after proving it prevents real mistakes.
- **Decay, don't hoard** — Review and prune regularly. Stale knowledge is worse than no knowledge.
- **Session continuity** — Log every conversation so your AI can recall past decisions and context.

## Background

This system was developed over hundreds of real-world sessions building construction management integrations across Vista, Procore, Monday.com, and other enterprise systems. It draws on research from:

- Academic work on codified context in LLM-assisted development
- Open-source projects (Mem0, OpenMemory, Brain-Agent)
- Industry practice (Manus context engineering, Spotify, OpenAI Codex)
- Hard-won lessons from daily multi-project, multi-user AI workflows

The full research findings and references are in [SUPERCONTEXT.md](SUPERCONTEXT.md).

## License

MIT — use it however you want.

---

*Built by a construction company that accidentally got really good at AI infrastructure.*
