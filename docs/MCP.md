# MCP Surface Reference

RunawayContext v3 ships with an MCP (Model Context Protocol) server providing **13 tools** over stdio transport. The server is the canonical integration point for Claude Code, Cursor, and any other MCP-aware AI client.

This document is the authoritative reference for the MCP surface. Every tool listed here is bound by HR-14 (documented and machine-readable contracts) and HR-12 (tested). Tools that take writes are bound by HR-2 (project-tagged at the boundary) and HR-3 (recoverable).

---

## Connection

The server runs over stdio (no TCP / no network — HR-1 is preserved). Clients launch it as a subprocess:

```bash
runaway mcp serve
```

The server reads MCP requests on stdin, writes responses on stdout, and logs to stderr. No port is bound. No background network calls are made.

| Field | Value |
|---|---|
| Protocol version | MCP 2024-11-05 or later |
| Transport | stdio only |
| Authentication | Process-level (filesystem permissions) |
| Network surface | None (HR-1) |
| State | Backed by `knowledge.db` + `sessions.db` |

---

## Tool Index

| # | Tool | Kind | HR rules |
|---|---|---|---|
| 1 | `get_brief` | read | HR-5, HR-14 |
| 2 | `search_chunks` | read | HR-2, HR-14 |
| 3 | `search_lessons` | read | HR-2, HR-14 |
| 4 | `propose_lesson_draft` | write (draft only) | HR-2, HR-3, HR-9 |
| 5 | `approve_draft` | write (admin or self) | HR-2, HR-3, HR-7 |
| 6 | `reject_draft` | write | HR-7 |
| 7 | `list_drafts` | read | — |
| 8 | `regen_brief` | write (filesystem) | HR-5 |
| 9 | `brief_preview` | read | HR-5 |
| 10 | `brief_rollback` | write (filesystem) | HR-3, HR-7 |
| 11 | `mature_lesson` | write (state transition) | HR-9, HR-7 |
| 12 | `list_specialists` | read | — |
| 13 | `audit_verify` | read | HR-7 |

Every tool returns either a success payload or an MCP `isError: true` response with a structured error.

---

## Error Model

Errors follow MCP's structured-error pattern:

```json
{
  "isError": true,
  "code": "BRIEF_BUDGET_EXCEEDED",
  "message": "Brief for slug 'accounting' would be 187 lines; cap is 150 (HR-5).",
  "data": {
    "rule": "HR-5",
    "tier": "T1",
    "slug": "accounting",
    "computed_lines": 187,
    "cap": 150
  }
}
```

Standard error codes:

| Code | Triggered by | Rule |
|---|---|---|
| `INVALID_SLUG` | unknown / deprecated / NULL slug on write | HR-2 |
| `BRIEF_BUDGET_EXCEEDED` | regen would exceed cap | HR-5 |
| `SOFT_DELETE_REQUIRED` | hard-delete attempted via MCP | HR-3 |
| `NOT_AN_APPROVED_PATH` | maturity update outside `mature_lesson` | HR-9 |
| `AUDIT_CHAIN_BROKEN` | verifier detected tamper | HR-7 |
| `MISSING_REQUIRED_FIELD` | required input absent | HR-14 |
| `INVALID_MATURITY_STATE` | maturity not in canonical six | HR-9 |
| `DRAFT_NOT_FOUND` | approve/reject on missing id | — |
| `DRAFT_ALREADY_REVIEWED` | re-approve / re-reject | — |
| `LOCKED_LESSON` | mature on locked lesson | HR-9 |

---

## Tool 1 — `get_brief`

Retrieve the auto-generated brief for a project slug.

**Inputs:**

| Field | Type | Required | Default |
|---|---|---|---|
| `slug` | string | yes | — |
| `include_preserve` | boolean | no | true |
| `max_lines` | integer | no | tier cap |

**Output:**

```json
{
  "slug": "accounting",
  "md_path": "/var/www/html/Accounting/CLAUDE.md",
  "line_count": 127,
  "line_cap": 150,
  "content": "# Accounting — Brief\n\n...",
  "regenerated_at": "2026-05-13T14:22:01Z"
}
```

**Refuses:** unknown slug (`INVALID_SLUG`).
**Rules:** HR-5 (returns line count + cap), HR-14 (documented).

---

## Tool 2 — `search_chunks`

Full-text + (optional) semantic hybrid search across `knowledge_chunks`.

**Inputs:**

| Field | Type | Required | Default |
|---|---|---|---|
| `query` | string | yes | — |
| `project` | string | no | (all) |
| `limit` | integer | no | 10 |
| `mode` | enum: `fts` / `semantic` / `hybrid` | no | `hybrid` (if semantic enabled) else `fts` |
| `include_deleted` | boolean | no | false |
| `visibility` | enum: `private` / `team` / `org` | no | follows install tier |

**Output:**

```json
{
  "results": [
    {
      "chunk_id": 142,
      "title": "Vista JC_DETAIL months store deltas",
      "content": "...",
      "project_tags": ["accounting"],
      "score": 0.87,
      "score_fts": 0.61,
      "score_semantic": 0.92
    }
  ],
  "total": 1,
  "mode_used": "hybrid"
}
```

**Refuses:** invalid `mode`, unknown `visibility` (`MISSING_REQUIRED_FIELD`).
**Rules:** HR-2 (tag-aware), HR-1 (semantic local-only by default).

---

## Tool 3 — `search_lessons`

Full-text + (optional) semantic hybrid search across `lessons_learned`. Mature lessons (`internalized`, `superseded`, `archived`) are returned unless excluded.

**Inputs:**

| Field | Type | Required | Default |
|---|---|---|---|
| `query` | string | yes | — |
| `project` | string | no | (all) |
| `limit` | integer | no | 10 |
| `maturity` | array of enum | no | `["scar", "active", "stable"]` |
| `mode` | enum | no | `hybrid` if available |

**Output:**

```json
{
  "results": [
    {
      "lesson_id": 432,
      "title": "Paycom cat2desc filter pulled false positives",
      "what_happened": "...",
      "prevention_rule": "Use deptdesc + position_family, not cat2desc",
      "project_tags": ["paycom", "field_staff"],
      "maturity": "stable",
      "blast_radius": 3,
      "frequency": 4,
      "reversibility": 2,
      "derived_severity": "warning"
    }
  ]
}
```

**Refuses:** invalid `maturity` array, unknown `project` (`INVALID_SLUG`).

---

## Tool 4 — `propose_lesson_draft`

Capture a scar-tissue incident as a draft. Drafts sit in `lesson_drafts` until a human approves.

**Inputs:**

| Field | Type | Required |
|---|---|---|
| `title` | string | yes |
| `what_happened` | string | no |
| `why` | string | no |
| `the_fix` | string | no |
| `prevention_rule` | string | no |
| `project_tags` | array<string> | yes (≥1, all validated against slug_registry) |
| `source_conversation_ref` | string | no |

**Output:**

```json
{
  "draft_id": 17,
  "status": "pending",
  "audit_id": 4801
}
```

**Refuses:** missing / unregistered `project_tags` (`INVALID_SLUG`); missing `title` (`MISSING_REQUIRED_FIELD`).
**Rules:** HR-2 (every draft tagged), HR-3 (drafts never deleted, only state-transitioned), HR-9 (drafts feed the maturation curve through human approval).

---

## Tool 5 — `approve_draft`

Promote a draft to a real `lessons_learned` row. The approval is recorded in the audit log.

**Inputs:**

| Field | Type | Required |
|---|---|---|
| `draft_id` | integer | yes |
| `actor` | string | no (defaults to current `author_id`) |
| `notes` | string | no |
| `initial_maturity` | enum | no (defaults to `scar`) |
| `axes` | object with `blast_radius`/`frequency`/`reversibility` | no |

**Output:**

```json
{
  "draft_id": 17,
  "lesson_id": 521,
  "audit_id": 4811
}
```

**Refuses:** unknown draft (`DRAFT_NOT_FOUND`); already-reviewed draft (`DRAFT_ALREADY_REVIEWED`); axes out of 1..5 (`INVALID_MATURITY_STATE` semantics extended).
**Rules:** HR-7 (audit), HR-9 (state-transition single-path).

---

## Tool 6 — `reject_draft`

Mark a draft rejected. The row is preserved; status moves to `rejected`.

**Inputs:** `draft_id` (int, required), `reason` (string, optional), `actor` (string, optional).

**Output:** `{ "draft_id": 17, "status": "rejected", "audit_id": 4812 }`

**Refuses:** unknown draft (`DRAFT_NOT_FOUND`).

---

## Tool 7 — `list_drafts`

List pending drafts (default) or filter by status.

**Inputs:**

| Field | Type | Default |
|---|---|---|
| `status` | enum | `pending` |
| `project` | string | (all) |
| `limit` | integer | 50 |

**Output:** array of draft summaries with id, title, project_tags, proposed_by, proposed_at.

---

## Tool 8 — `regen_brief`

Regenerate a project brief from the database. Writes to the configured `md_path`. Refuses overflow (HR-5). Saves a snapshot before write.

**Inputs:**

| Field | Type | Required |
|---|---|---|
| `slug` | string | yes |
| `dry_run` | boolean | no (default false) |

**Output:** `{ "slug": "...", "md_path": "...", "line_count": 127, "snapshot_id": 88, "written": true }`

**Refuses:** unknown slug (`INVALID_SLUG`); would exceed cap (`BRIEF_BUDGET_EXCEEDED`).
**Rules:** HR-5 (hard line cap).

---

## Tool 9 — `brief_preview`

Preview what `regen_brief` would write, without touching the filesystem. Returns the candidate markdown and a unified diff against the current file (if present).

**Inputs:** `slug` (required), `context_lines` (int, default 3).

**Output:** `{ "candidate_content": "...", "diff": "...", "line_count": 127, "would_exceed_cap": false }`

---

## Tool 10 — `brief_rollback`

Roll back to the most recent (or specified) snapshot of a brief.

**Inputs:** `slug` (required), `snapshot_id` (optional — defaults to most recent prior to current).

**Output:** `{ "slug": "...", "rolled_back_to_snapshot": 87, "audit_id": 4830 }`

**Rules:** HR-7 (audited rollback), HR-3 (no data lost — snapshots are versioned).

---

## Tool 11 — `mature_lesson`

The single approved path to transition `lessons_learned.maturity`. HR-9 says the engine proposes; humans approve. This is "approve."

**Inputs:**

| Field | Type | Required |
|---|---|---|
| `lesson_id` | integer | yes |
| `to` | enum: `scar`/`active`/`stable`/`internalized`/`superseded`/`archived` | yes |
| `actor` | string | no |
| `superseded_by` | integer | required only if `to=superseded` |
| `reason` | string | no |

**Output:** `{ "lesson_id": 432, "from": "active", "to": "stable", "audit_id": 4841 }`

**Refuses:** invalid state (`INVALID_MATURITY_STATE`); locked lesson (`LOCKED_LESSON`); `superseded_by` missing for supersede.
**Rules:** HR-9, HR-7.

---

## Tool 12 — `list_specialists`

List specialist agents.

**Inputs:** `active_only` (bool, default true).

**Output:** array of `{id, name, domain, description, md_path, knowledge_count}`.

---

## Tool 13 — `audit_verify`

Recompute the audit log hash chain. Returns chain status. Read-only.

**Inputs:** none.

**Output:** `{ "chain_intact": true, "row_count": 4831, "first_row_at": "...", "last_row_at": "..." }` or `{ "chain_intact": false, "broken_at_row": 4123 }`.

**Rules:** HR-7.

---

## What MCP Does NOT Expose

By design, the following are CLI / Client-only:

- `hard-delete` (HR-3 admin escape — never via MCP).
- `migrate` (v2 → v3 schema migration — operator-only).
- `tier promote` (operator decision, audited).
- `slug merge`, `slug deprecate` (data-shape changes, audited).
- `audit verify --repair` (no such flag exists, but if it did it would not be MCP-exposed).
- `config set` (writes to install config).

The MCP surface is **read-mostly with carefully gated writes**. Anything that changes the install topology or escapes the recoverable-write invariant is operator-only.

---

## Versioning and Compatibility

The MCP surface follows semantic versioning at the tool level. Adding a tool is a minor bump. Removing a tool is a major bump (which v3 commits to not doing per HR-4-by-analogy for the API surface). Adding optional fields to a tool's input or output schema is a patch bump.

Every tool name and its inputs/outputs are pinned by `tests/contract/test_hr_14_public_api_documented.py` (which verifies the docstrings) and `tests/contract/test_hr_12_public_api_coverage.py` (which verifies each tool has a feature test).
