---
description: RunawayContext v3 retrieval rule for Cursor. Routes context queries through the MCP server.
globs:
alwaysApply: true
---

# RunawayContext v3 — Cursor Retrieval Rule

This rule is loaded by Cursor for every conversation in this project. It tells Cursor how to talk to the RunawayContext v3 MCP server so retrieval is fast, contracts are honored, and writes are auditable.

## Connection

The MCP server is launched as a subprocess via `runaway mcp serve` and communicates over stdio. There is no port, no HTTP, no network surface (HR-1).

Cursor's MCP configuration should include an entry like:

```json
{
  "mcpServers": {
    "runaway-context": {
      "command": "runaway",
      "args": ["mcp", "serve"]
    }
  }
}
```

Once configured, the 13 MCP tools are available to the agent. See [docs/MCP.md](../../docs/MCP.md) for the full surface.

## What the agent should do

### On project entry

When the agent recognizes it has just entered a new project's workspace, it should call:

```
get_brief(slug=<project slug>)
```

The brief is the canonical context for the project — auto-generated, capped at 150 lines (HR-5), reflecting current curated state.

### On a search request

If the user asks about a topic, the agent should issue both:

```
search_lessons(query=..., project=<slug>)
search_chunks(query=..., project=<slug>)
```

Lessons surface scar-tissue rules; chunks surface reference material. Present them together, with maturity tags on the lessons.

### On a correction

If the user corrects the agent on something durable, the agent should:

1. Identify the relevant slug(s).
2. Compose a draft via `propose_lesson_draft`.
3. Inform the user a draft is pending review.

Do **not** call `log_lesson` directly. The drafts inbox is the human-approval gate (HR-9).

### On a write that needs admin authority

The agent should **not** call:

- `mature_lesson` without explicit user authorization for the specific lesson and target state.
- `regen_brief` if the user hasn't asked for it (briefs auto-regenerate on a schedule; manual regen is for after a curation pass).
- `brief_rollback` without explicit user request.

## Hard rules to honor

The MCP server enforces these contracts at the boundary; the agent should also honor them in its planning:

- **HR-1:** All retrieval is local. Do not propose network-backed alternatives.
- **HR-2:** Every write needs a valid `project_tags` slug. If a user gives content without a slug, ask before drafting.
- **HR-3:** No hard deletes. Soft-delete via `soft_delete` if you genuinely need to retract.
- **HR-9:** Maturation is human-approved. Engine proposes; never auto-apply.
- **HR-13:** Do not insert `TODO` / `FIXME` markers into source files of a RunawayContext install.

## Error handling

If an MCP call returns `isError: true`:

1. Surface the error code (e.g., `INVALID_SLUG`, `BRIEF_BUDGET_EXCEEDED`, `AUDIT_CHAIN_BROKEN`).
2. Reference the rule it enforces.
3. Suggest a remediation:
   - `INVALID_SLUG` → register the slug via CLI.
   - `BRIEF_BUDGET_EXCEEDED` → run `runaway drift suggest --slug <slug>`.
   - `AUDIT_CHAIN_BROKEN` → stop. Do not auto-repair. Surface to the user.

## When the MCP server is unavailable

If the MCP server is not running or the connection fails:

1. Surface the failure to the user (do not silently degrade — HR-10).
2. Suggest: `runaway mcp serve --once` to verify the server starts cleanly.
3. Offer to fall back to reading the most recent generated brief from the filesystem (e.g., `<project>/CLAUDE.md`).

Do **not** invent context. If the brief is unavailable, ask.

## Slash commands

The same `/runaway:*` slash commands work in Cursor as in Claude Code:

- `/runaway:brief <slug>`
- `/runaway:search <query>`
- `/runaway:project <slug>`
- `/runaway:drafts`
- `/runaway:regen <slug>`
- `/runaway:stats`

These are agent-side conveniences; they map to the corresponding MCP tools.

## Verification

To confirm the rule is connected to a working install:

```
audit_verify()       # chain_intact = true
list_specialists()   # matches the install's specialists
```

If either fails, the install is partial. Stop and report to the user.

## References

- The hard rules: [docs/HARD_RULES.md](../../docs/HARD_RULES.md)
- The MCP surface: [docs/MCP.md](../../docs/MCP.md)
- The skill file for Claude Code (mirror of this rule): [skills/runaway-context/SKILL.md](../../skills/runaway-context/SKILL.md)
