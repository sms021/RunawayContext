# run_SuperContext.md
# One-shot AI Memory System Builder
#
# USAGE: Paste this entire file into your AI assistant, or tell it:
#   "Please read and execute run_SuperContext.md"
#
# REQUIRES: SUPERCONTEXT.md in the same directory (the theory/reference guide)
# WORKS WITH: Claude Code, Cursor, GitHub Copilot, OpenAI Codex, Aider, or any AI with file access

---

You are about to build a persistent, tiered knowledge system called **SuperContext**. The full theory and reference guide is in `SUPERCONTEXT.md` in this same directory — read it first to understand the architecture. Then execute the phases below in order.

**Your job is to do the work, not explain the theory.** Be concise. Show progress, not lectures. Ask questions only when marked [ASK]. Everything else, just do it.

---

## PHASE 0: ORIENT

### 0.1 — Read the Guide
Read `SUPERCONTEXT.md` from this directory. Do not summarize it back to me. Just confirm you've read it and move on.

### 0.2 — Detect My Environment [ASK]
Ask me these questions **all at once** (this is setup, not a conversation):

1. What AI tool are you using? (Claude Code / Cursor / GitHub Copilot / Codex / Aider / ChatGPT / Other)
2. What is your main project directory? (e.g., `/home/user/myproject`, `C:\Users\me\code`, or "this directory")
3. In a sentence or two, what are you building? (e.g., "A 3D printing management tool in Python" or "Multiple school projects in various languages")
4. Any strong preferences for how I work with you? (e.g., "explain things simply", "don't touch files without asking", "I'm a beginner") — or say "none" and we'll figure it out as we go.

**Wait for my answers before proceeding.**

### 0.3 — Map Tool to File Locations
Based on my answer to question 1, set these variables for the rest of the process:

| Tool | Constitution File | Memory Location | Project Brain File |
|------|------------------|-----------------|-------------------|
| Claude Code | `CLAUDE.md` (project root) | `~/.claude/projects/<path>/memory/MEMORY.md` | `CLAUDE.md` per subdirectory |
| Cursor | `.cursor/rules/constitution.mdc` | `.cursor/rules/memory.mdc` | `.cursor/rules/<name>.mdc` with globs |
| GitHub Copilot | `.github/copilot-instructions.md` | `.github/memory.md` | `.instructions.md` per subdirectory |
| OpenAI Codex | `AGENTS.md` (project root) | `MEMORY.md` (project root) | `AGENTS.md` per subdirectory |
| Aider | `.aider.conf.yml` + `INSTRUCTIONS.md` | `MEMORY.md` (project root) | `PROJECT_CONTEXT.md` per subdirectory |
| ChatGPT / Other | `INSTRUCTIONS.md` (project root) | `MEMORY.md` (project root) | `PROJECT_CONTEXT.md` per subdirectory |

---

## PHASE 1: DISCOVER

Scan my project. Do all of these silently (don't ask, just do). Report findings in a single summary when done.

### 1.1 — Find Existing Instruction Files
Search for any files that are already providing AI context:
- `CLAUDE.md`, `AGENTS.md`, `INSTRUCTIONS.md`, `AI_Notes.txt`, `AI_Prompt.txt`
- `.cursorrules`, `.cursor/rules/*.mdc`
- `.github/copilot-instructions.md`, `.instructions.md`
- `README.md` files that contain AI-directed instructions
- `.aider*` config files
- Any file with "AI" or "context" or "instructions" in the name

Read each one found. Note its location and size.

### 1.2 — Identify Projects
Scan the directory tree (max 3 levels deep) and identify distinct projects or feature areas. Look for signals:
- Directories with their own `package.json`, `requirements.txt`, `Cargo.toml`, `go.mod`, `Makefile`, `composer.json`, or similar
- Directories with their own `src/`, `lib/`, `app/` subdirectories
- Directories that appear to be self-contained tools or features
- If this is a monorepo, identify each package/service
- If this is a single project, identify major feature directories (e.g., `auth/`, `api/`, `frontend/`, `database/`)

For each project/area found, note: name, path, primary language, and a one-line description based on the code.

### 1.3 — Find Existing Knowledge
Scan for knowledge that should be captured:
- Database schemas (`.sql` files, migration files, ORM models)
- API documentation (OpenAPI/Swagger files, API route definitions)
- Configuration files with non-obvious settings
- Environment variable definitions (`.env.example`, config files)
- Terminology or abbreviations used in comments or docs
- Known issues documented in comments (TODO, FIXME, HACK, WARNING, XXX)
- Test files that reveal business rules

Don't read every file — skim headers, look for patterns, sample a few files per project.

### 1.4 — Report Discovery [SHOW ME]
Show me a summary table:

```
DISCOVERY REPORT
================
Existing instruction files found: [count]
  - [filename] ([lines] lines) — [what it contains]

Projects/areas identified: [count]
  - [name] ([path]) — [one-line description]

Knowledge artifacts found:
  - Database schemas: [count] files
  - API definitions: [count] files
  - Config files: [count] files
  - TODOs/known issues: [count] items
  - Other docs: [count] files

Recommended setup:
  - Tier 1 (Constitution): [new / migrate from existing file]
  - Tier 2 (Living Memory): [new / seed from existing corrections]
  - Tier 3 (Project Brains): [count] project files to create
  - Tier 4 (Knowledge Store): [Level 1 markdown / Level 2 SQLite / skip for now]
```

**Wait for me to say "go" or make adjustments before proceeding.**

---

## PHASE 2: BUILD THE KNOWLEDGE STORE (Tier 4)

Build this FIRST because Tiers 1-3 will reference it. Skip this phase if the discovery report recommended "skip for now."

### 2.1 — Choose Level
- If I have **fewer than 20 knowledge items** total → Level 1 (markdown files in a `_knowledge/` directory)
- If I have **20+ items OR databases/APIs** → Level 2 (SQLite + CLI)

### 2.2 — Level 1: Markdown Knowledge Store
Create a `_knowledge/` directory with these files as needed:
- `databases.md` — schemas, connection info, table descriptions, gotchas
- `terminology.md` — abbreviations and their meanings with context
- `api-reference.md` — external API documentation, endpoints, auth
- `architecture.md` — system-level decisions and rationale
- `tools.md` — internal tools, scripts, utilities

Populate each from the knowledge artifacts found in Phase 1. Don't pad — only include what was actually found.

### 2.3 — Level 2: SQLite Knowledge Store
1. Create a `_knowledge/` directory
2. Create `_knowledge/setup_knowledge.py` using Template E from SUPERCONTEXT.md
3. Run it to create the database
4. Create `_knowledge/knowledge.py` using Template F from SUPERCONTEXT.md
5. Populate the database from Phase 1 findings:
   - Database schemas → `data_sources` table
   - Business rules from TODOs/comments → `business_rules` table
   - Abbreviations from the codebase → `terminology` table
   - Internal tools/scripts → `tools` table
6. Run `--stats` and show me the result

### 2.4 — Import Existing Instruction File Content
If existing instruction files were found in Phase 1.1, classify every piece of content in them:

| Content Type | Destination |
|-------------|-------------|
| User preferences / behavioral rules | → Tier 1 (Constitution) |
| Corrections / gotchas / "never do X" | → Tier 2 (Living Memory) |
| Project-specific business rules | → Tier 3 (that project's brain) |
| Database schemas, API docs, reference data | → Tier 4 (Knowledge Store) |
| Generic advice the AI already knows | → DELETE (don't migrate) |
| Outdated information | → DELETE |

**Do the classification silently.** You'll use the results in the next phases.

---

## PHASE 3: BUILD PROJECT BRAINS (Tier 3)

Create one Project Brain file for each project/area identified in Phase 1.2.

### 3.1 — For Each Project
Create the Project Brain file (using the correct filename for my AI tool) with these sections. Only include sections that have real content — don't create empty sections with placeholder text.

**Required sections:**
- **Overview** — 2-3 sentences from what you learned scanning the code
- **Key Files** — table of important files and their purposes (scan the directory, pick the 5-15 most important)

**Include if applicable:**
- **Data Architecture** — if the project uses databases or APIs
- **Business Rules** — any domain logic found in comments, tests, or existing docs
- **Known Gotchas** — from TODO/FIXME/HACK comments and existing instruction files
- **Decision Log** — if any architectural decisions are documented in comments or existing files

**Always include (even if empty):**
- **Changelog** — empty section with a comment: `<!-- Update this at the end of each work session -->`

### 3.2 — Migrate Content from Existing Files
Move project-specific content identified in Phase 2.4 into the correct Project Brain. Remove it from the source file.

### 3.3 — Report [SHOW ME]
List every Project Brain file created, with line count and sections included. Example:
```
PROJECT BRAINS CREATED
======================
  src/auth/CLAUDE.md (47 lines) — Overview, Key Files, Business Rules, Changelog
  src/api/CLAUDE.md (82 lines) — Overview, Key Files, Data Architecture, API Endpoints, Gotchas, Changelog
  tools/printer/CLAUDE.md (35 lines) — Overview, Key Files, Changelog
```

---

## PHASE 4: BUILD LIVING MEMORY (Tier 2)

### 4.1 — Create the Memory Index
Create the memory index file (correct location for my tool) with:

```markdown
# Living Memory
<!-- Cross-session behavioral gotchas. Keep under 50 lines total.
     2-3 lines per entry max. Link to detail files for depth.
     Project data → project brain | Reference data → Knowledge Store -->

## Preferences
- [Seed from my answer to question 0.2.4, if I gave preferences]

## Gotchas
<!-- Will fill naturally as we work together -->

## Patterns
<!-- Will fill naturally as we work together -->
```

### 4.2 — Seed from Existing Content
If existing instruction files contained corrections, gotchas, or "never do X" items (classified in Phase 2.4):
- Items under 3 lines → add directly to the memory index
- Items over 3 lines → create a detail file and link to it from the index

### 4.3 — Create the Memory Directory
If my tool supports detail files (Claude Code does natively, others need a `_memory/` directory), create the directory structure:

```
[memory location]/
├── MEMORY.md (or equivalent index file)
└── [detail files will go here as we work]
```

---

## PHASE 4.5: BUILD SESSION MEMORY

Session Memory gives you cross-conversation continuity. Without it, every new session starts cold — no record of what was worked on, what decisions were made, or what broke.

### 4.5.1 — Create the Session Database

Create `_knowledge/sessions.db` using this schema:

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT UNIQUE,
    user TEXT DEFAULT 'default',
    session_date TEXT NOT NULL DEFAULT (date('now')),
    project TEXT,
    summary TEXT,
    work_completed TEXT,
    technical_decisions TEXT,
    known_issues TEXT,
    key_context TEXT,
    files_modified TEXT,
    full_transcript TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE sessions_fts USING fts5(
    conversation_id, project, summary, work_completed,
    technical_decisions, known_issues, key_context, files_modified,
    content=sessions, content_rowid=id
);

CREATE TRIGGER sessions_ai AFTER INSERT ON sessions BEGIN
    INSERT INTO sessions_fts(rowid, conversation_id, project, summary, work_completed, technical_decisions, known_issues, key_context, files_modified)
    VALUES (new.id, new.conversation_id, new.project, new.summary, new.work_completed, new.technical_decisions, new.known_issues, new.key_context, new.files_modified);
END;

CREATE TRIGGER sessions_ad AFTER DELETE ON sessions BEGIN
    INSERT INTO sessions_fts(sessions_fts, rowid, conversation_id, project, summary, work_completed, technical_decisions, known_issues, key_context, files_modified)
    VALUES ('delete', old.id, old.conversation_id, old.project, old.summary, old.work_completed, old.technical_decisions, old.known_issues, old.key_context, old.files_modified);
END;

CREATE TRIGGER sessions_au AFTER UPDATE ON sessions BEGIN
    INSERT INTO sessions_fts(sessions_fts, rowid, conversation_id, project, summary, work_completed, technical_decisions, known_issues, key_context, files_modified)
    VALUES ('delete', old.id, old.conversation_id, old.project, old.summary, old.work_completed, old.technical_decisions, old.known_issues, old.key_context, old.files_modified);
    INSERT INTO sessions_fts(rowid, conversation_id, project, summary, work_completed, technical_decisions, known_issues, key_context, files_modified)
    VALUES (new.id, new.conversation_id, new.project, new.summary, new.work_completed, new.technical_decisions, new.known_issues, new.key_context, new.files_modified);
END;
```

### 4.5.2 — Create the Session CLI

Create `_knowledge/sessions.py` with these capabilities:

```
python3 _knowledge/sessions.py --save                              # Log current session (prompts for details)
python3 _knowledge/sessions.py --save --project "X" --summary "Y"  # Log with args (no prompts)
python3 _knowledge/sessions.py --context "ProjectName" --days 30   # Project briefing for session start
python3 _knowledge/sessions.py --search "query"                    # Full-text search all sessions
python3 _knowledge/sessions.py --recent 5                          # Last N sessions
python3 _knowledge/sessions.py --project "ProjectName"             # All sessions for a project
python3 _knowledge/sessions.py --stats                             # Session counts by project
```

**The `--save` command** must accept these fields (all optional, prompt if not provided):
- `--project` — which project was worked on (auto-detect from files_modified if omitted)
- `--summary` — one-line summary of the session
- `--work` — what was completed (bullet list)
- `--decisions` — technical decisions made
- `--issues` — known issues or incomplete work
- `--context` — key context for future sessions
- `--files` — files modified (comma-separated)
- `--transcript` — full conversation text (optional, can be large)

**The `--context` command** (most important) outputs a compact briefing:
```
PROJECT BRIEFING: [ProjectName] (last 30 days)
==============================================
Sessions: [count]

Recent work:
  [date] — [summary]
  [date] — [summary]
  [date] — [summary]

Key decisions:
  - [decision from most recent session]
  - [decision from next most recent]
  - [decision from next]

Open issues:
  - [issue]

Recently modified files:
  - [file list from last 3 sessions]
```

**Implementation notes:**
- Use `argparse` for clean CLI
- Use `sqlite3.Row` for dict-like access
- FTS5 MATCH for `--search`, LIKE fallback if MATCH fails
- `--context` should limit to 15 most recent sessions and 5 most recent decisions
- All commands support `--json` for structured output
- Keep the script self-contained (no external dependencies beyond Python stdlib)

### 4.5.3 — Set Up Auto-Logging (Tool-Dependent)

**Claude Code**: Create a capture script (e.g., `_knowledge/capture_session.sh`) that:
1. Reads JSON from stdin (Claude Code passes `{"session_id":"...","transcript_path":"...","cwd":"..."}`)
2. Extracts file paths from the transcript to detect which project was worked on
3. Saves a metadata entry to sessions.db (date, project, files modified)
4. This is the "breadcrumb" layer — automatic but no AI summary

Then add a Stop hook to `.claude/settings.json` using the **correct nested format**:
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/_knowledge/capture_session.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

**Important**: Claude Code hooks use a nested `hooks` array — a flat format will fail silently.

For rich session entries with real summaries, the user (or AI) should still run `sessions.py --save` manually at the end of significant sessions. Without a local LLM, auto-summarization isn't possible — the hook captures metadata only.

**Cursor / Copilot / Other**: No hook support. Add this to the Constitution instead:
```markdown
## End of Session
At the end of every significant work session, run:
  python3 _knowledge/sessions.py --save --project "[project]" --summary "[what was done]"
I will remind you if you forget.
```

**Claude Code via VS Code Extension**: VS Code's Claude extension **does NOT fire CLI hooks**. The Stop hook will not trigger. Build a cron-based watcher instead.

Create `_knowledge/watch_sessions.sh`:
```bash
#!/bin/bash
# watch_sessions.sh — Cron-based session watcher for VS Code Claude extension
# VS Code's Claude extension does NOT fire CLI hooks. This script catches those sessions.
# Install: crontab -e → */10 * * * * /path/to/_knowledge/watch_sessions.sh >> /path/to/_knowledge/watcher.log 2>&1

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSIONS_PY="$SCRIPT_DIR/sessions.py"
STATE_FILE="$SCRIPT_DIR/.watcher_state"
LOCK_FILE="/tmp/supercontext_watcher.lock"

# ===== CONFIGURE THESE =====
# Add all users who use Claude Code via VS Code (home_dir:username pairs)
# CLI users are already covered by the Stop hook — only add VS Code users here
WATCH_USERS=(
    # "$HOME:$(whoami)"     # Uncomment if YOU use VS Code instead of CLI
    # "/home/otheruser:otheruser"   # Add other VS Code users
)
# How long (seconds) a session must be idle before we log it
IDLE_THRESHOLD=1800  # 30 minutes
# ===========================

# If no users configured, detect self
if [ ${#WATCH_USERS[@]} -eq 0 ]; then
    WATCH_USERS=("$HOME:$(whoami)")
fi

echo ""
echo "=== Session Watcher - $(date) ==="

# Prevent concurrent runs
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[Watcher] Already running, skipping"
    exit 0
fi

# Load previous state (file sizes by conversation ID)
declare -A PREV_SIZES
if [ -f "$STATE_FILE" ]; then
    while IFS='=' read -r key value; do
        PREV_SIZES["$key"]="$value"
    done < "$STATE_FILE"
fi

declare -A NEW_SIZES
LOGGED=0
SKIPPED=0

for entry in "${WATCH_USERS[@]}"; do
    HOME_DIR="${entry%%:*}"
    USERNAME="${entry##*:}"
    PROJECTS_DIR="$HOME_DIR/.claude/projects"

    if [ ! -d "$PROJECTS_DIR" ]; then
        continue
    fi

    # Find all conversation JSONL files
    while IFS= read -r -d '' conv_file; do
        [ -f "$conv_file" ] || continue

        conv_id=$(basename "$conv_file" .jsonl)

        # Get file size — try GNU stat, fall back to macOS stat
        file_size=$(stat -c%s "$conv_file" 2>/dev/null || stat -f%z "$conv_file" 2>/dev/null || echo "0")

        # Skip tiny files (empty/starter sessions)
        if [ "$file_size" -lt 15000 ]; then
            continue
        fi

        NEW_SIZES["$conv_id"]="$file_size"
        prev_size="${PREV_SIZES[$conv_id]:-0}"

        # Skip if unchanged
        if [ "$file_size" -eq "$prev_size" ]; then
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        # File is new or grew — check if idle (session likely ended)
        last_mod=$(stat -c%Y "$conv_file" 2>/dev/null || stat -f%m "$conv_file" 2>/dev/null || echo "0")
        now=$(date +%s)
        idle_secs=$((now - last_mod))

        if [ "$idle_secs" -ge "$IDLE_THRESHOLD" ]; then
            short_id="${conv_id:0:8}"
            echo "[Watcher] $USERNAME/$short_id: idle ${idle_secs}s, logging session"

            # Extract files modified from the JSONL (look for tool_use write/edit operations)
            files_changed=$(grep -o '"file_path":"[^"]*"' "$conv_file" 2>/dev/null | \
                sed 's/"file_path":"//;s/"//' | sort -u | head -20 | tr '\n' ',' | sed 's/,$//')

            # Detect project from file paths (use the Constitution's project map if available)
            project="Unknown"
            if [ -n "$files_changed" ]; then
                # Use the first directory component after the project root as a rough project name
                project=$(echo "$files_changed" | tr ',' '\n' | head -1 | \
                    sed "s|$HOME_DIR/||" | cut -d'/' -f1)
            fi

            # Log to session database
            python3 "$SESSIONS_PY" --save \
                --project "$project" \
                --summary "VS Code session (auto-captured)" \
                --files "$files_changed" \
                --user "$USERNAME" \
                2>/dev/null && LOGGED=$((LOGGED + 1))
        else
            echo "[Watcher] $USERNAME/${conv_id:0:8}: still active (idle ${idle_secs}s), waiting"
        fi
    done < <(find "$PROJECTS_DIR" -maxdepth 2 -name "*.jsonl" -print0 2>/dev/null)
done

# Save state for next run
> "$STATE_FILE"
for key in "${!NEW_SIZES[@]}"; do
    echo "${key}=${NEW_SIZES[$key]}" >> "$STATE_FILE"
done
# Preserve sizes for conversations we didn't re-scan
for key in "${!PREV_SIZES[@]}"; do
    if [ -z "${NEW_SIZES[$key]:-}" ]; then
        echo "${key}=${PREV_SIZES[$key]}" >> "$STATE_FILE"
    fi
done

echo "[Watcher] Done: logged=$LOGGED, skipped=$SKIPPED"
```

Make it executable and install the cron job:
```bash
chmod +x _knowledge/watch_sessions.sh
```

Then ask the user: **"Do you or anyone else use Claude Code through VS Code (not the terminal CLI)?"**

- If **yes**: Install the cron watcher:
  ```bash
  (crontab -l 2>/dev/null; echo "*/10 * * * * $(pwd)/_knowledge/watch_sessions.sh >> $(pwd)/_knowledge/watcher.log 2>&1") | crontab -
  ```
  Edit `watch_sessions.sh` to add each VS Code user to the `WATCH_USERS` array.

- If **no**: Skip the cron job. The Stop hook handles CLI sessions. Tell the user: "If you switch to VS Code later or add VS Code users, run `crontab -e` and add the watcher."

**Multi-user file permissions** (critical for multi-user setups):
When the watcher runs as one user but needs to read another user's Claude conversation files, file permissions will block it. Claude Code creates files with mode 600 (owner-only). Fix this with ACLs:

For each VS Code user whose sessions need to be captured by the watcher:
```bash
# Replace WATCHER_USER with the user running the cron job
# Replace VSCODE_USER with each VS Code user's home directory
sudo bash -c 'find /home/VSCODE_USER/.claude/projects/ -type f -name "*.jsonl" -exec setfacl -m u:WATCHER_USER:r {} +'
sudo bash -c 'find /home/VSCODE_USER/.claude/projects/ -type d -exec setfacl -m u:WATCHER_USER:rx {} +'
# Set default ACL so future files inherit the permission
sudo find /home/VSCODE_USER/.claude/projects/ -type d -exec setfacl -d -m u:WATCHER_USER:r {} +
```

If sudo is not available, an alternative is to have each user run their own cron job (pointing to the same `watch_sessions.sh` and sessions database). No cross-user permissions needed.

**If hooks fail or aren't supported**, fall back to the Constitution instruction. The AI should remind the user (or do it itself) at the end of significant sessions.

### 4.5.4 — Verify
Run `python3 _knowledge/sessions.py --stats` to confirm the database was created. Then save a test entry:
```bash
python3 _knowledge/sessions.py --save --project "SuperContext" --summary "Initial SuperContext setup" --work "Built 4-tier knowledge system" --decisions "SQLite for session storage, Level 1 markdown for knowledge store"
```

Run `--recent 1` to confirm it saved correctly.

---

## PHASE 5: BUILD THE CONSTITUTION (Tier 1)

This is the **last** thing built because it references everything else.

### 5.1 — Draft the Constitution
Build the Constitution file using Template A from SUPERCONTEXT.md. Customize it with:

**From my answers:**
- Project name and description (question 0.2.2-3)
- My preferences (question 0.2.4)
- My tool and environment info (question 0.2.1)

**From the build:**
- Knowledge routing table pointing to the actual locations of Tier 2, 3, and 4 as built
- Project map table listing every project brain created in Phase 3
- Knowledge Store commands (if Tier 4 was built)
- Session Memory commands (from Phase 4.5)
- Project context protocol telling the AI to read project brains AND run `sessions.py --context` when entering a directory

**Hard rules:**
- **Maximum 200 lines.** Count them. If over 200, you're including content that belongs in a lower tier.
- **No database schemas.** That's Tier 3 or 4.
- **No project-specific business rules.** That's Tier 3.
- **No reference data.** That's Tier 4.
- **No generic AI instructions** ("write clean code", "use best practices"). The AI already knows these.

### 5.2 — Handle the Old Instruction File
If an existing instruction file was found in Phase 1.1:
- All its content has been migrated to the correct tiers (Phase 2.4, 3.2, 4.2)
- **Replace it** with the new Constitution (don't create a second file)
- If the old file was a different filename than what my tool uses, rename it and delete the old one

### 5.3 — Review [ASK]
**Show me the complete Constitution file.** Tell me the line count. Ask me to review and approve or request changes. Do not save until I approve.

---

## PHASE 6: VERIFY & ACTIVATE

### 6.1 — Final Report [SHOW ME]
Show me a complete summary of everything built:

```
SUPERCONTEXT BUILD COMPLETE
============================

Constitution (Tier 1):
  File: [path]
  Lines: [count] (target: ≤200)

Living Memory (Tier 2):
  Index: [path] ([count] lines, [count] entries)
  Detail files: [count]

Project Brains (Tier 3):
  [count] project files created:
  - [path] ([lines] lines)
  - [path] ([lines] lines)
  ...

Knowledge Store (Tier 4):
  Type: [Level 1 markdown / Level 2 SQLite / not created]
  Location: [path]
  [If SQLite: entries by table - metrics: X, data_sources: X, business_rules: X, terminology: X, tools: X]
  [If markdown: file count and total lines]

Session Memory:
  Database: [path to sessions.db]
  CLI: [path to sessions.py]
  Auto-logging: [hook configured / manual reminder in Constitution / not set up]
  Test entry: [saved successfully / failed — reason]

Migrated from existing files:
  - [old filename]: [X] items moved to Tier 1, [X] to Tier 2, [X] to Tier 3, [X] to Tier 4, [X] deleted

Files to clean up:
  - [any old instruction files that were replaced]
```

### 6.2 — Teach Me the Habits [SHOW ME]
Show me this cheat sheet:

```
KEEPING YOUR AI MEMORY ALIVE
==============================

1. CORRECT ME     → When I get something wrong, say "remember: [the correction]"
                    I'll save it to Living Memory so I never repeat the mistake.

2. LOG SESSIONS   → When we finish significant work, say "log this session"
                    I'll save a summary to the session database so future sessions
                    know what happened. (If hooks are set up, this happens automatically.)

3. START SESSIONS → When you come back to a project after a break, say
                    "check what we've done on [project name] recently"
                    I'll pull up context from both the project brain AND session history.

4. UPDATE BRAINS  → When we finish significant work, say "update the project brain"
                    I'll add a changelog entry and update any stale sections.

5. STAY SLIM      → If the Constitution passes 200 lines, tell me to slim it down.
                    I'll move the excess to the correct lower tier.

6. MONTHLY CLEAN  → Once a month, say "review the memory system"
                    I'll prune stale entries, check for duplicates, and flag drift.
```

### 6.3 — Clean Up Legacy Files [ASK]
If any existing instruction files were replaced or their content migrated, show a list of files that are now redundant:

```
LEGACY FILES (safe to delete — content has been migrated):
  - [file] → content now in [destination]
  - [file] → content now in [destination]
```

Ask: "Want me to delete these legacy files? Their content has been migrated to the new system. I can also just leave them if you prefer."

Only delete if the user says yes. If they say no or skip, move on.

### 6.4 — Activate [SHOW ME]
Tell me:

```
Your SuperContext memory system is live.

→ Restart this session (or start a new one) for the Constitution to take effect.
→ From now on, I'll automatically use your project brains when working in those directories.
→ When you correct me, I'll save the lesson to Living Memory.
→ Your knowledge is organized, your context is clean, and every session from here builds on the last.
```

### 6.5 — Verify (Optional but Recommended)
Tell the user:

```
To verify everything works:
1. Start a new session (exit and reopen your AI tool)
2. Ask: "What do you know about me and my projects?"
   → The AI should reference your Constitution and project map
3. Navigate to a project and ask it to check the project brain
   → It should read the CLAUDE.md and know the project context
4. Run: python3 _knowledge/sessions.py --recent 1
   → Should show the SuperContext setup session
```

---

## RULES FOR THE AI EXECUTING THIS FILE

1. **Do the work.** Don't explain what you're about to do — just do it, then show results.
2. **Be accurate over fast.** Actually read files, actually scan directories. Don't guess or assume.
3. **Ask only at [ASK] points.** Everything else, execute autonomously.
4. **If something fails, adapt.** Missing permissions? Use a different path. Can't create SQLite? Fall back to markdown. Tool doesn't support X? Find the closest equivalent.
5. **Respect the line budgets.** The Constitution MUST stay under 200 lines. The Memory index MUST stay under 50 lines. These are hard limits, not suggestions.
6. **Don't pad files with placeholders.** Empty sections with "TODO" or "Add content here" are worse than no section at all. Only create sections with real content.
7. **The Knowledge Store setup scripts (Templates E and F) come from SUPERCONTEXT.md.** Read that file to get them. Do not fabricate your own versions — use those templates.
8. **When classifying existing content for migration, err on the side of deleting.** If you're unsure whether something is useful, it probably isn't. The user can always re-add it. Bloat is the enemy.
