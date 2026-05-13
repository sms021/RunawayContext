# RETRIEVAL.md — T0 Paste-Once Template

This is a paste-once retrieval template for T0 (Hello World) users. **You have no database yet.** This file lives in your project's root and is referenced from your AI tool's system prompt. It teaches the AI how to load context from your project as if a real RunawayContext install were present.

Once you accumulate 5+ project-specific notes, you should promote to T1 — see [BOOTSTRAP.md](BOOTSTRAP.md). After that, the retrieval template is replaced by the auto-generated brief.

---

## How to use this file

1. **Copy this file** into your project root: `cp RETRIEVAL.md /path/to/your/project/RETRIEVAL.md`.
2. **Reference it** in your AI tool's primary instructions file (CLAUDE.md / AGENTS.md / `.cursorrules`):

   > "When working in this project, read RETRIEVAL.md first. It explains how to find context."

3. **Maintain it.** As you add notes, files, or templates, append pointers to the "Project Notes" section.

---

## The Contract (with your AI)

When you, the AI, work in this project:

1. **Read this file first.** It is the routing index. If it says "see `notes/business-rules.md` for accounting rules," go read that file before answering accounting questions.

2. **Do not assume context across sessions.** If a fact is not in this file or in a file this file points to, ask. Do not guess based on prior conversations — those conversations may have been with a different machine, a different developer, or a different model.

3. **Honor file budgets.** If this file grows past 100 lines, suggest creating a separate notes file and pointing to it from here. Do not let RETRIEVAL.md become a memory dump.

4. **Capture corrections.** When the user corrects you on something specific to this project, suggest adding a pointer line to the "Lessons Learned" section below. Do not write long-form lessons; one line each.

5. **No silent failures.** If a referenced file does not exist, report it. Do not invent its contents.

---

## Project Overview

> **Fill in:** describe your project in 5..10 lines. What it does, what stack it uses, what tier the constraints are at.
> *(This file is your project's template; fill in this section before showing it to your AI. The framework's source files forbid placeholder markers per HR-13; this user-owned template is exempt because you are completing it before use.)*

---

## Routing Table

| Topic | Where to look |
|---|---|
| Business rules | `notes/business-rules.md` (create if needed) |
| Data sources / schemas | `notes/data-sources.md` (create if needed) |
| Deployment / ops | `notes/ops.md` (create if needed) |
| Recent decisions | `notes/decisions/<date>-<topic>.md` |
| API conventions | `notes/api.md` |

> Edit this table to match your project. Drop rows that do not apply.

---

## Project Notes

(Add pointer lines here as you accumulate them. Format: one line per note.)

- *No notes yet.*

---

## Lessons Learned

(Things you corrected the AI on. One line each. Drill into a longer file if needed.)

- *No lessons logged yet.*

---

## When to Graduate to T1

You will know it is time when:

- This file has more than ~25 pointer lines.
- You find yourself opening the same notes file in every session.
- You realize that across machines or developers, the notes do not travel with the project.
- You catch yourself re-explaining the same business rule for the third time.

At that point, follow [BOOTSTRAP.md § Tier 1](BOOTSTRAP.md#tier-1--solo). The database eliminates the per-session re-explanation; the auto-generated brief replaces this file.

---

## Promotion Note

When you migrate to T1, this file is **kept** as a fallback retrieval layer for AIs that do not understand MCP. The auto-generated brief (Tier 3) supersedes most of its contents; the lessons-learned pointers move into `knowledge.db`.

The T0 → T1 path is non-destructive: nothing in this file is overwritten. Your existing notes are imported into the database via `runaway import-markdown` (see BOOTSTRAP), tagged with their slug, and rendered back into the generated brief.

---

## Reference

For the full theory: [RUNAWAYCONTEXT.md](RUNAWAYCONTEXT.md).
For the hard rules: [docs/HARD_RULES.md](docs/HARD_RULES.md).
For the install prompt: [INSTALL_PROMPT.md](INSTALL_PROMPT.md).
