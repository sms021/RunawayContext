# RunawayContext: The Manual Walkthrough

This is the "do it by hand" guide. If you want to understand what you're building and why before you build it, start here. If you'd rather have your AI assistant build it for you automatically, use `run_RunawayContext.md` instead.

The full technical reference is in `RUNAWAYCONTEXT.md` — this walkthrough covers the same system in plain language.

---

## What You're Building

Every AI conversation starts from zero. Your assistant doesn't remember what you worked on yesterday, what mistakes it made, or why you chose that database schema. RunawayContext fixes that by giving the AI a structured set of files to read — a memory system that grows as you work.

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

## Tier 3: Project Briefs (auto-generated in v2)

**What**: One instruction file per project or major feature area, containing deep context specific to that project.

**Why**: If you have three projects, 80% of your knowledge is only relevant to one of them. Loading all of it every session wastes context and confuses the AI. Project Briefs load only when you're working in that directory.

**The v1 problem**: a hand-edited project brain drifts. Every session, someone adds "while I'm here, let me note this." Six months later it's 1,200 lines, the auto-load eats your context budget, and the rules quietly stop being enforced. Asking the AI (or yourself) to "stay disciplined" doesn't work over time.

**The v2 fix**: Tier 3 is a **generated artifact**, not a hand-edited file. Knowledge lives in `knowledge.db` (Tier 4). The project brief is rebuilt from the DB on demand and capped at ≤150 lines. The cap is enforced by the regenerator — anything over the cap doesn't get written. There's a single small `<!-- PRESERVE_START --> ... <!-- PRESERVE_END -->` block where you write the project's overview by hand; everything else is overwritten on every rebuild.

**How it works**:
1. **Knowledge lives in the DB.** Lessons go in `lessons_learned`. Reference content goes in `knowledge_chunks`. Both are tagged with one or more project slugs.
2. **A `project_context_card` row is the manifest.** One row per project, holding JSON arrays of active lesson ids, active chunk ids, top warnings, plus the `md_path` and `md_line_cap` for the generated brief.
3. **`--rebuild-brief <slug>` regenerates the card** from anything tagged with that slug.
4. **`--rebuild-md <slug>` writes the file.** Reads the card, renders a slim markdown file with the auto-gen banner, the PRESERVE block, top warnings, lesson pointers (`LL#N — title`), chunk pointers (`KS#N — title`), and stops at the cap.

**What ends up in each project brief**:
- **Banner** declaring auto-generation (so future-you doesn't hand-edit and lose work)
- **PRESERVE block** — 5-10 lines of human-curated overview ("what this project does, who owns it, why it exists"). Survives every regen.
- **⚠ Top warnings** — the 3 critical-severity lessons for this project, with `LL#N` pointers
- **Lessons Learned** — top 25 active lessons with `LL#N` pointers
- **Knowledge Chunks** — top 20 active chunks with `KS#N` pointers
- **Footer** — regen timestamp, drill-in commands

**When to create one**: One per project that has its own knowledge. The AI's `run_RunawayContext.md` discovers your projects automatically; you can also add slugs by hand to `lib/_project_slugs.py`.

**Build it**:
```bash
# After lessons + chunks are tagged with the project slug:
python3 lib/ll_brief.py --rebuild-brief myapp
python3 lib/ll_brief.py --rebuild-md myapp     # writes to card.md_path
```

**Read it** (your AI's first action when entering a project):
```bash
python3 lib/ll_brief.py --brief myapp
```

This returns the full manifest in one query — no need to grep. From there, drill into any pointer (`--ll-get N` or query the `knowledge_chunks` table).

---

## Tier 4: The Knowledge Store

**What**: A searchable database holding reference data the AI queries on demand. v2 splits it into two SQLite files:

```
~/_knowledge/
├── knowledge.db    ← the BRAIN (chunks, lessons, project briefs, junctions)
└── sessions.db     ← the LOG (conversation transcripts + summaries)
```

**Why two files?**
- `knowledge.db` is curated and small. Backed up frequently.
- `sessions.db` grows fast (transcripts are heavy). Backed up less often, retained longer.
- Linked via `conversation_id` (TEXT) — `lessons_learned.source_conversation_ref` and `chunk_sessions.conversation_id` point at `session_logs.conversation_id`. ATTACH at query time when you need them joined.
- You can disable session capture entirely by skipping `sessions.db`. The brain works standalone.

**knowledge.db schema** (simplified):

| Table | Purpose |
|-------|---------|
| `knowledge_chunks` | Curated reference content (facts, schemas, API details). FTS5 indexed. |
| `lessons_learned` | Burned-us-once incidents, with severity / status / supersession lifecycle. FTS5 indexed. |
| `project_context_card` | One row per project. The auto-generated manifest that drives Tier 3. |
| `lesson_chunks` | Junction — which chunks each lesson informs. |
| `chunk_sessions` | Junction — which sessions each chunk came up in (uses conversation_id, no FK across DBs). |

**sessions.db schema**:

| Table | Purpose |
|-------|---------|
| `session_logs` | One row per archived conversation. Includes summary, work_completed, technical_decisions, and (optionally inline) full_transcript. FTS5 over the summary fields. |

**Build it**:
```bash
python3 lib/setup_db.py                  # default: ~/_knowledge/{knowledge,sessions}.db
python3 lib/setup_db.py --no-sessions    # skip session capture, brain only
```

**Query it** — the AI uses these patterns:
```bash
# What's known about a topic?
sqlite3 ~/_knowledge/knowledge.db \
  "SELECT title, body FROM knowledge_chunks_fts WHERE knowledge_chunks_fts MATCH 'auth' LIMIT 5"

# What lessons are critical for this project?
python3 lib/ll_brief.py --ll-list --ll-project myapp

# What conversation did this lesson come from?
python3 lib/ll_brief.py --ll-get 42      # auto-ATTACHes sessions.db, shows linked session
```

**When to build this**: Every install needs Tier 4. v1 left this optional ("when need emerges"); v2 makes it the substrate everything else writes through. Tier 3 briefs are generated FROM Tier 4 — there is no Tier 3 without Tier 4 in v2.

---

## Write Guards (new in v2)

Knowledge enters `knowledge.db` via two write paths:

1. **Direct CLI**: `lib/propose_knowledge.py` (chunks) and `lib/ll_brief.py --log-lesson` (lessons)
2. **Auto-mining**: optional discovery scripts that scan files and populate the KS

Both are guarded:

| Guard | What it catches |
|-------|----------------|
| **Required `--project` slug** | A lesson or chunk without a project slug can't be inserted. Argparse rejects the call before touching the DB. |
| **Canonical slug validation** | The slug must match an entry in `CANONICAL_PROJECT_SLUGS` in `lib/_project_slugs.py`. Typos like `parkers` (should be `parker`) are rejected with a "did you mean...?" listing the valid slugs. |
| **Auto-stamped `source_user`** | Every write captures `$USER` (or `$SUDO_USER`). You always know who wrote what. |
| **Junk path rejection** | Auto-miners that pass file paths through `slug_from_path()` get `None` for paths matching `node_modules`, `vendor/`, `.bak`, `.backup.`, `__pycache__`, etc. The miner skips those rows entirely — they never reach the DB. |

**The point**: bloat is cheaper to prevent at the write side than to clean up at the read side. v1 relied on policy ("be careful what you put in"). v2 makes the validation a function of the schema and the CLI — typos can't slip in, untagged content can't accumulate.

**Setting up your slug taxonomy** is a one-time task:
1. Open `lib/_project_slugs.py`
2. Edit `CANONICAL_PROJECT_SLUGS` — add every project slug your install will use
3. Edit `PATH_TO_SLUG` — map directory paths to slugs (so the auto-miner can derive slugs from file paths)
4. Save — every CLI write now validates against this list

Adding a new project later is one append to the set + one entry in the path map.

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

## Drift Detection (new in v2)

Even with the cap-enforced regenerator, files can grow if someone hand-edits between rebuilds. The drift detector catches this.

**Two paths**, depending on which AI tool you use:

**Path A — Stop hook** (Claude Code CLI, anything that fires Stop hooks):

Add to your tool's settings (`~/.claude/settings.json` for Claude Code):
```json
"hooks": {
  "Stop": [{
    "hooks": [{ "type": "command", "command": "/path/to/RunawayContext/bin/check_md_drift.sh" }]
  }]
}
```
Fires at session end. Walks every `project_context_card` row, checks the actual file's line count against the card's `md_line_cap`, and warns to stderr if any file is over.

**Path B — Cron / launchd watcher** (VS Code Claude extension, anywhere Stop hooks don't fire):

Linux cron:
```bash
crontab -e
*/10 * * * * /path/to/RunawayContext/bin/md_drift_watcher.sh
```

macOS launchd: see `bin/md_drift_watcher.sh` for the plist template. Runs every 10 minutes, writes to `~/_knowledge/logs/md_drift_watcher.log` and a snapshot file a dashboard can scrape.

Either path is non-blocking — it warns, it doesn't fail. You see the drift the moment it happens, you regen, and you're back to clean. The repeated visibility is what keeps you honest.

---

## Putting It Together

Build order in v2:

1. **Schema** — `python3 lib/setup_db.py` creates `knowledge.db` + `sessions.db`. Idempotent.
2. **Slug taxonomy** — edit `lib/_project_slugs.py` to list your projects.
3. **Tier 4 (Knowledge Store)** — your AI populates this from existing READMEs / config files / project docs during the executable-prompt run, OR you populate by hand using `propose_knowledge.py` and `--log-lesson`.
4. **Tier 3 (Project Briefs)** — generated, not hand-built. `--rebuild-brief <slug>` then `--rebuild-md <slug>`.
5. **Tier 2 (Living Memory)** — start mostly empty, write `LL#N` / `KS#N` pointers when needed.
6. **Session Memory** — set up the PostCompact hook (Claude Code) or cron capture script for VS Code users. `sessions.db` accumulates.
7. **Tier 1 (Constitution)** — last, because it references everything else.
8. **Drift detection** — wire the Stop hook + cron watcher.

The Constitution is still the routing hub. It tells the AI: "Here's who I am, here are my rules, here's the canonical slug list, and here are the commands to query everything else."

**If this feels like a lot**: in v2 the executable prompt (`run_RunawayContext.md`) sets up steps 1-8 for you. Hand it to your AI. Read this BOOTSTRAP if you want to understand what it's doing — but you don't have to do it by hand.

---

## Keeping It Alive

A memory system that isn't maintained becomes a liability — stale instructions are worse than no instructions, because the AI follows them confidently.

v1 made keeping the system alive a habit problem. v2 moves most of it into the tooling. You still have a few things to actually do:

1. **Correct and capture.** When the AI gets something wrong, log it as a lesson:
   ```bash
   python3 lib/ll_brief.py --log-lesson --ll-projects myapp \
       --ll-title "Cache headers were wrong" --ll-severity warning \
       --ll-prevention "Always set Cache-Control: no-store on auth endpoints"
   ```
   The brief auto-rebuilds. The lesson is searchable forever.

2. **Promote stable rules into knowledge_chunks.** When a lesson has matured into a discipline you always follow, write a chunk for it:
   ```bash
   python3 lib/propose_knowledge.py --project myapp --topic auth_cache_policy \
       --title "Auth Cache Policy" --body "..." --tags security,http
   ```
   Lessons are scar tissue (incidents). Chunks are reference (current state).

3. **Supersede outdated lessons.** When a lesson no longer applies, mark it:
   ```sql
   UPDATE lessons_learned SET status='superseded', superseded_by=99 WHERE id=42;
   ```
   The brief auto-drops superseded lessons on next rebuild.

4. **Don't hand-edit project briefs.** v1 told you to update changelogs at end-of-session. v2 doesn't have changelogs — it has lessons + chunks + sessions, all DB rows, all auto-aggregated. The drift detector tells you if anyone forgot.

5. **Trust the watcher.** When the cron watcher logs drift, regen the file. Don't hand-fix the line count.

---

## What's Next

- **Full reference**: `RUNAWAYCONTEXT.md` has the complete technical details, schema diagrams, anti-patterns, and scaling advice
- **Automated setup**: `run_RunawayContext.md` is a paste-and-go executor — your AI reads it and builds the whole system for you (v2 schema, slug taxonomy, briefs, hooks — all of it)
- **v1 → v2 upgrade**: see `lib/migrate_v1_to_v2.py`. Idempotent, preserves your existing knowledge, separate from fresh-install path.
- **Questions or feedback**: [github.com/sms021/RunawayContext](https://github.com/sms021/RunawayContext)

---

*RunawayContext v2.0 — Built from real-world experience across thousands of AI coding sessions across multiple projects, multiple users, and one painful encounter with how fast a "small" memory system grows back into a 2000-line file when the discipline is policy-only.*
