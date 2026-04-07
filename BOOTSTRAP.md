# SuperContext: The Manual Walkthrough

This is the "do it by hand" guide. If you want to understand what you're building and why before you build it, start here. If you'd rather have your AI assistant build it for you automatically, use `run_SuperContext.md` instead.

The full technical reference is in `SUPERCONTEXT.md` — this walkthrough covers the same system in plain language.

---

## What You're Building

Every AI conversation starts from zero. Your assistant doesn't remember what you worked on yesterday, what mistakes it made, or why you chose that database schema. SuperContext fixes that by giving the AI a structured set of files to read — a memory system that grows as you work.

The trick is **not dumping everything into one file.** AI accuracy drops when context gets too long, and important instructions in the middle of large files get ignored. So we split knowledge into four tiers: small things load every time, big things load only when needed.

---

## Tier 1: The Constitution

**What**: A single instruction file that loads every session. Think of it as your AI's standing orders.

**Why**: Without this, you repeat yourself constantly. "Use tabs not spaces." "Don't touch the production database." "My project uses PostgreSQL, not MySQL." The Constitution holds these universal rules so every conversation starts with baseline context.

**How**: Create one file in the location your AI tool expects:

| Tool | File |
|------|------|
| Claude Code | `CLAUDE.md` in project root |
| Cursor | `.cursor/rules/constitution.mdc` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| OpenAI Codex | `AGENTS.md` in project root |
| Aider | `INSTRUCTIONS.md` in project root |
| ChatGPT / Other | `INSTRUCTIONS.md` in project root |

**What goes in it**:
- Your preferences and hard rules ("always use TypeScript strict mode", "never mock the database in tests")
- A routing table telling the AI where to find deeper knowledge (Tiers 2-4)
- A project map if you have multiple projects
- Tool and environment configuration (API keys, database hosts, etc.)
- Session memory commands (covered in the Session Memory section below)

**What does NOT go in it**:
- Database schemas (that's Tier 3 or 4)
- Project-specific business rules (that's Tier 3)
- Things the AI already knows ("write clean code", "follow best practices")
- Reference data (that's Tier 4)

**Hard limit: 200 lines.** If your Constitution is longer, you're putting things in the wrong tier. Every line competes for the AI's attention — make them count.

---

## Tier 2: Living Memory

**What**: A short index of behavioral corrections and patterns — things the AI keeps getting wrong that you've had to fix. Plus optional detail files for items that need more than a couple lines.

**Why**: AI assistants make the same mistakes across conversations. You correct them once, they learn for that session, then forget. Living Memory makes corrections permanent. It's the fastest tier to show value because every entry directly prevents a repeated mistake.

**How**: Create a memory index file:

| Tool | File |
|------|------|
| Claude Code | `~/.claude/projects/<encoded-path>/memory/MEMORY.md` (built in — Claude already uses this) |
| Cursor | `.cursor/rules/memory.mdc` |
| GitHub Copilot | `.github/memory.md` |
| Others | `MEMORY.md` in project root |

**What goes in it**:
- Corrections: "JCCP stores deltas, not cumulative values — always SUM across months"
- Gotchas: "The VS Code extension doesn't fire CLI hooks — use a cron watcher instead"
- Patterns: "Always use TRIM(field) when querying this table — trailing spaces are inconsistent"

**Format**: 2-3 lines per entry, max. If something needs more depth, create a separate detail file and link to it from the index. The index itself should stay under 50 lines.

**When to add entries**: Whenever you correct the AI and the correction isn't obvious from the code itself. Both failures (things it got wrong) and validated approaches (things it got right that were non-obvious) are worth capturing.

---

## Tier 3: Project Brains

**What**: One instruction file per project or major feature area, containing deep context specific to that project.

**Why**: If you have three projects, 80% of your knowledge is only relevant to one of them. Loading all of it every session wastes context and confuses the AI. Project Brains load only when you're working in that directory.

**How**: Create a file in each project directory using the same naming convention as your Constitution (e.g., `CLAUDE.md` for Claude Code, `.instructions.md` for Copilot). Then add a rule to your Constitution telling the AI to read the project brain when it enters a project directory.

**What goes in it**:
- **Overview**: 2-3 sentences about what this project does
- **Key Files**: The 5-15 most important files and what they do
- **Data Architecture**: Database tables, APIs, external services this project uses
- **Business Rules**: Domain logic that isn't obvious from the code ("GP is calculated as contract amount minus actual cost, not including retainage")
- **Known Gotchas**: Things that break if you're not careful
- **Decision Log**: Why things are the way they are ("We chose SQLite over PostgreSQL because this runs on single-user machines")
- **Changelog**: Updated at the end of each work session

**When to create one**: When a project is complex enough that you find yourself re-explaining context to the AI. One simple project? Skip this for now. A monorepo with six services? Create one per service.

---

## Tier 4: The Knowledge Store

**What**: A searchable database (or collection of markdown files) holding reference data the AI queries on demand.

**Why**: Some knowledge is too large or too detailed for instruction files — full database schemas, API documentation, terminology dictionaries, tool inventories. The Knowledge Store holds this without bloating your always-loaded tiers. The AI searches it when it needs specific reference data.

**How** (two levels, pick one):

### Level 1: Markdown Files (simple)
Create a `_knowledge/` directory with files organized by topic:
- `databases.md` — schemas, connection info, table descriptions
- `terminology.md` — abbreviations and their meanings
- `api-reference.md` — external API docs, endpoints, auth details
- `tools.md` — internal scripts and utilities

Good for: fewer than 20 knowledge items, single-person projects.

### Level 2: SQLite Database (scalable)
Create a SQLite database with tables for metrics, data sources, business rules, terminology, and tools. Add a Python CLI script so the AI can query it via bash commands.

Good for: 20+ knowledge items, databases, APIs, teams, or anyone who wants full-text search.

**When to build this**: When you catch yourself (or the AI) searching for the same reference information repeatedly. Don't build it on day one — let the need emerge.

---

## Session Memory

The tiers above give the AI knowledge. Session Memory gives it continuity — a record of what happened in past conversations so it can pick up where you left off.

### The Basic Layer: Metadata Capture

At minimum, you want to log what happened in each session: which files were touched, which project was active, and when. This is the breadcrumb trail.

**How it works**:
1. A SQLite database (`sessions.db`) stores one row per conversation
2. A capture script runs at the end of each session (via hook, cron, or manually) and logs file paths, timestamps, and project name
3. A CLI tool (`sessions.py`) lets you query past sessions and generate project briefings
4. Your Constitution tells the AI to run `sessions.py --context "ProjectName"` when starting work on a project

**The context briefing** is the payoff — it gives the AI a compact summary of recent work, decisions, and open issues for that project. Instead of starting cold, it starts with "here's what happened last week."

For Claude Code specifically: a `Stop` hook can trigger the capture script automatically at the end of every conversation. For VS Code users (where hooks don't fire), a cron-based watcher script that scans for new conversation files every 10 minutes is the alternative.

### The Optional Layer: AI-Powered Summaries

The basic metadata capture logs file lists and timestamps. Functional, but thin. You can optionally have an LLM (Claude Haiku, GPT-4o-mini, or a local model) read the conversation transcript and generate a real summary — what was done, what decisions were made, what's still open.

The quality difference is dramatic. But the risk is real.

**What went wrong for us**: The first version of our AI summarizer had no safeguards. It tried to process every unprocessed session in one pass. When the API call failed, it retried the full batch. Then retried again. One runaway loop burned through a third of a week's token budget before anyone noticed. It was invisible until the bill came.

**If you add AI summarization, these safeguards are non-negotiable:**

| Safeguard | What | Why |
|-----------|------|-----|
| **Batch limit** | Process max 5 sessions per run | A backlog of 50 drains over 10 cron cycles, not one catastrophic pass |
| **Processed marker** | Flag each session as done in the database | Completed sessions never re-enter the queue — this prevents the retry-everything loop |
| **Attempt cap** | 3 tries max, then mark as permanently failed | If it fails 3 times, it'll fail forever. Stop burning tokens on it. |
| **Lock file** | Only one summarizer instance runs at a time | Cron cycles can overlap — two simultaneous runs double-process everything |
| **No retry loops** | On failure, log the error and move on | The cron schedule is your retry mechanism. Never loop inside a single run. Ever. |

**Cost reality**: ~$0.01-0.05 per session with a small model (Haiku, GPT-4o-mini). At 10 sessions/day, that's $3-15/month. Use the smallest model that gives you good-enough summaries — Haiku is usually plenty.

**Our recommendation**: Start with the basic metadata layer. It's free, it's reliable, and it gives you useful context. Add AI summaries later if the basic layer feels too thin — and when you do, implement every safeguard in the table above. They're not optional.

---

## Putting It Together

Build in this order:

1. **Tier 4 (Knowledge Store)** first, if you need one — because the other tiers will reference it
2. **Tier 3 (Project Brains)** — deep per-project context
3. **Tier 2 (Living Memory)** — start it mostly empty, it'll fill as you work
4. **Session Memory** — the database, CLI, and capture mechanism
5. **Tier 1 (Constitution)** last — because it references everything else

The Constitution is the routing hub. It tells the AI: "Here's who I am, here are my rules, and here's where to find everything else." Every other tier is a destination the Constitution points to.

**If this feels like a lot**: start with just Tier 1 and Tier 2. A 50-line Constitution and an empty Living Memory index. That alone will make a noticeable difference. Add the other tiers when the need becomes obvious.

---

## Keeping It Alive

A memory system that isn't maintained becomes a liability — stale instructions are worse than no instructions, because the AI follows them confidently.

Five habits:

1. **Correct and capture.** When the AI gets something wrong, tell it — and make sure the correction lands in Living Memory. "Remember: this field stores deltas, not cumulative values."

2. **Log sessions.** At the end of significant work, log what was done. If you have auto-capture set up, this happens for free. If not, `sessions.py --save --project "X" --summary "Y"` takes 10 seconds.

3. **Start sessions with context.** When you come back to a project after a break, tell the AI to pull up recent history. The context briefing prevents the "where were we?" dance.

4. **Update project brains.** At the end of significant work, update the changelog and any sections that have drifted. This is the most commonly skipped step and the one that causes the most pain when neglected.

5. **Prune monthly.** Once a month, review Living Memory and project brains. Delete resolved issues, consolidate similar entries, and remove anything the code now makes obvious. A lean memory system is a fast memory system.

---

## What's Next

- **Full reference**: `SUPERCONTEXT.md` has the complete technical details, templates, anti-patterns, and scaling advice
- **Automated setup**: `run_SuperContext.md` is a paste-and-go executor — your AI reads it and builds the whole system for you
- **Questions or feedback**: [github.com/sms021/SuperContext](https://github.com/sms021/SuperContext)

---

*SuperContext v1.0 — Built from real-world experience across 1,500+ AI coding sessions.*
