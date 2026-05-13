---
name: runaway-context
description: Use this skill when working in a project that has a RunawayContext v3 install. It loads the project's brief from the auto-generated Tier 3 markdown, queries `knowledge.db` for relevant lessons and chunks, and routes writes through the contract-enforced Client (HR-2 / HR-3 / HR-9). Trigger when the conversation enters a new project directory, when the user asks for context on a slug, when correcting AI behavior to capture as a draft, or when the user invokes `/runaway:*` commands.
---

# RunawayContext Skill

This skill activates the RunawayContext v3 retrieval surface inside Claude Code. When the skill is loaded, the AI has access to:

- **Read tools** (via the MCP server): `get_brief`, `search_chunks`, `search_lessons`, `list_drafts`, `list_specialists`, `brief_preview`, `audit_verify`.
- **Write tools** (via the MCP server): `propose_lesson_draft`, `approve_draft`, `reject_draft`, `regen_brief`, `brief_rollback`, `mature_lesson`.

## When to invoke

1. **Entering a new project directory.** The AI should call `get_brief` with the project's slug. The brief is auto-generated, capped at 150 lines, and reflects the current curated state.

2. **The user asks for context.** Examples:
   - "What do you know about the accounting project?" → `get_brief(slug='accounting')`.
   - "Search for paycom-related lessons." → `search_lessons(query='paycom', project='paycom')`.
   - "What's the data map for Procore?" → query `data_sources` and `data_source_mappings` for `system='procore'`.

3. **The user corrects you on something durable.** When the correction is project-specific and likely to recur, propose a draft:
   - `propose_lesson_draft(title=..., what_happened=..., the_fix=..., prevention_rule=..., project_tags=[...])`.
   - **Do not** call `log_lesson` directly. The drafts inbox is the human-approval gate (HR-9).

4. **The user invokes a slash command:**
   - `/runaway:brief <slug>` → `get_brief(slug)`.
   - `/runaway:search <query>` → `search_chunks(query)` + `search_lessons(query)` and present merged results.
   - `/runaway:project <slug>` → switch the active project context (multi-project session stacking).
   - `/runaway:drafts` → `list_drafts()` and present for review.
   - `/runaway:regen <slug>` → `regen_brief(slug)`.
   - `/runaway:stats` → run `runaway stats` and present the output.

## Hard rules to honor

This skill enforces the same hard rules as the underlying install:

- **HR-1.** No network calls. All MCP traffic is stdio. Semantic embeddings are local-onnx unless the install opted in to remote providers.
- **HR-2.** Every `propose_lesson_draft` call must include at least one `project_tags` entry from the canonical slug registry. If the user gives you content without a slug, ask for one before drafting.
- **HR-3.** Never call hard-delete via this skill. The MCP server does not expose it.
- **HR-9.** Never call `mature_lesson` without explicit user authorization. The engine proposes; the user (or you, under their authority) approves.
- **HR-13.** Do not generate `TODO` / `FIXME` markers when writing files in a RunawayContext install's source tree.

## Example invocations

### Entering a project

User: *"I'm working on the accounting tools."*

AI action:
```
get_brief(slug='accounting')
```

AI response: presents the brief content, identifies top warnings, lists active LL pointers.

### Capturing a correction

User: *"No, JCCP stores deltas — you have to SUM across months. You keep getting this wrong."*

AI action:
```
propose_lesson_draft(
    title="JCCP stores deltas, always SUM across months",
    what_happened="AI assumed JCCP rows were cumulative; queried single month → undercount.",
    why="JCCP is a delta table by design; cumulative is computed.",
    the_fix="SELECT SUM(amount) FROM JCCP WHERE job=? AND month <= ?",
    prevention_rule="Never query JCCP for a single month and assume it's the total.",
    project_tags=['accounting']
)
```

AI response: *"Captured as draft #N. You can review it later with `/runaway:drafts`."*

### Searching

User: *"What do we know about Paycom termination data?"*

AI action:
```
search_lessons(query='paycom termination', project='paycom')
search_chunks(query='paycom termination', project='paycom')
```

AI response: presents merged results with maturity badges.

### Approving a draft

User: *"Approve draft 17."*

AI action:
```
approve_draft(draft_id=17, actor='<user_author_id>')
```

AI response: *"Approved as LL#N. Recorded in audit log."*

## What not to do

- **Do not** invent slug names. If a project isn't in the slug registry, ask the user to register it via `runaway slug register --slug ... --description ...`.
- **Do not** swallow errors. If an MCP call returns `isError: true`, surface the error code and the rule it references (e.g., `INVALID_SLUG → HR-2`).
- **Do not** suggest `runaway db hard-delete`. That command is operator-only; if the user truly wants it, they will type it themselves.
- **Do not** edit the auto-generated brief by hand. Edit the PRESERVE block only; everything else is overwritten on regen.

## Verification

If you want to confirm the skill is connected to the right install:

```
audit_verify()       # should return chain_intact=true
list_specialists()   # should match the install's specialists
get_brief(slug='general')  # most installs have a 'general' slug
```

If any of these fail or return unexpected results, the install may be partial. Report the failure to the user before continuing.
