# Python Client API Reference

The `runaway_context.Client` class is the canonical Python API. The CLI, the MCP server, and any external Python tooling that integrates with a RunawayContext install go through the Client. The Client is the single place HR-2 (project-tagged writes), HR-3 (recoverable writes), HR-5 (budgets), HR-7 (audit), HR-9 (maturation approval), and HR-1 (network egress check) are enforced in Python.

This document is the reference for every public method on `Client`. Every method has a contract that follows HR-14: explicit `Returns:`, `Raises:`, and `Refuses:` sections.

---

## Construction

```python
from runaway_context import Client

client = Client(
    knowledge_db="~/_knowledge/knowledge.db",
    sessions_db="~/_knowledge/sessions.db",
    config_path="~/_knowledge/config.toml",
)
```

**Refuses to start (HR-1) if:** any non-allowlisted network-capable module has been imported into the process without its corresponding config flag set to `true`.

**Refuses to start (HR-4) if:** the schema version of the on-disk database does not match the code's expected version.

**Refuses to start (HR-7) if:** the audit log chain verification fails at startup (operator must run `runaway audit verify` and resolve).

---

## Read Methods

### `read(table, id) -> dict | None`

Read a single row by id from `knowledge_chunks` or `lessons_learned`.

- **Inputs:** `table: Literal['knowledge_chunks', 'lessons_learned']`, `id: int`.
- **Returns:** the row as a dict, or `None` if not found.
- **Raises:** `ValueError` on unknown table.
- **Refuses:** never returns deleted rows; pass `include_deleted=True` to override.

### `search_chunks(query, project=None, limit=10, mode='hybrid', visibility=None) -> list[dict]`

Full-text + (optional) semantic hybrid search over `knowledge_chunks`.

- **Inputs:** `query: str`, optional `project: str`, `limit: int`, `mode: 'fts' | 'semantic' | 'hybrid'`, `visibility: 'private' | 'team' | 'org' | None`.
- **Returns:** list of dicts with chunk fields plus `score`, `score_fts`, `score_semantic`.
- **Raises:** `ValueError` on invalid `mode`; `UnknownSlug` if `project` is set and not in `slug_registry`.
- **Refuses:** never returns deleted rows; respects current install's visibility tier.

### `search_lessons(query, project=None, maturity=None, limit=10, mode='hybrid') -> list[dict]`

Same shape as `search_chunks` but over `lessons_learned`. The default `maturity` filter excludes `internalized`, `superseded`, and `archived` — they remain queryable but require explicit opt-in.

- **Returns:** list of dicts with lesson fields, `derived_severity`, `maturity`, scores.
- **Raises:** `ValueError` on invalid maturity values.

### `get_brief(slug, include_preserve=True) -> dict`

Retrieve a project's auto-generated brief.

- **Inputs:** `slug: str` (validated against `slug_registry`).
- **Returns:** `{"slug", "md_path", "line_count", "line_cap", "content", "regenerated_at"}`.
- **Raises:** `UnknownSlug` if not registered or deprecated without canonical.
- **Refuses:** content is always under the slug's tier cap (HR-5).

### `brief_preview(slug, context_lines=3) -> dict`

Compute the candidate brief content and a unified diff against the current file. No filesystem write.

- **Returns:** `{"candidate_content", "diff", "line_count", "would_exceed_cap"}`.
- **Raises:** `UnknownSlug`.

### `list_drafts(status='pending', project=None, limit=50) -> list[dict]`

List `lesson_drafts` rows.

### `list_specialists(active_only=True) -> list[dict]`

List specialist agents.

### `stats() -> dict`

Return install-wide stats: maturation distribution, top contributors, retrieval health proxies (last eval scores), drift hotspots, audit chain status, semantic index health.

- **Returns:** structured dict (see `runaway stats` for the canonical shape).
- **Refuses:** never reaches network.

### `tier_check() -> dict`

Report what tier the install is at and what the next promotion gate requires.

- **Returns:** `{"current_tier", "next_tier", "requirements": [...], "blockers": [...]}`.

---

## Write Methods

All write methods enforce HR-2 (project-tagged at boundary) via `_guard_write`. All write methods are recorded in the audit log (HR-7). All write methods to soft-deleted-eligible tables go through `_audit_write` and produce a `record_versions` row on overwrite.

### `log_lesson(*, title, what_happened, why=None, the_fix=None, prevention_rule=None, project_tags, axes=None, source_conversation_ref=None) -> int`

Write a `lessons_learned` row directly (skipping the drafts inbox). Used by curated bulk imports and the CLI `--log-lesson` path. The drafts inbox is the *recommended* path; this is the audited direct path.

- **Inputs:** all fields. `project_tags: list[str]` is required and each tag validated. `axes` is optional `{"blast_radius": int, "frequency": int, "reversibility": int}` with each value 1..5.
- **Returns:** new lesson id.
- **Raises:** `InvalidSlug` if any tag is unknown; `OutOfRangeAxes` if axes are not 1..5; `MissingRequiredField` if `title` is empty.
- **Refuses:** writes with NULL / empty `project_tags`.

### `propose_knowledge(*, title, content, project_tags, kind='chunk', visibility='private', metadata=None) -> int`

Write a `knowledge_chunks` row.

- **Returns:** new chunk id.
- **Raises:** `InvalidSlug`, `MissingRequiredField`, `InvalidVisibility`.
- **Refuses:** writes with NULL / empty `project_tags`.

### `propose_lesson_draft(*, title, what_happened=None, why=None, the_fix=None, prevention_rule=None, project_tags, source_conversation_ref=None) -> int`

Insert into `lesson_drafts` with `status='pending'`. The preferred capture path during a conversation.

- **Returns:** new draft id.
- **Raises:** `InvalidSlug`, `MissingRequiredField`.

### `approve_draft(draft_id, *, actor=None, notes=None, initial_maturity='scar', axes=None) -> int`

Atomically promote a draft to a `lessons_learned` row and update the draft's status to `approved` with `approved_lesson_id` set.

- **Returns:** new lesson id.
- **Raises:** `DraftNotFound`, `DraftAlreadyReviewed`, `OutOfRangeAxes`.

### `reject_draft(draft_id, *, actor=None, reason=None) -> None`

Mark draft `rejected`.

- **Raises:** `DraftNotFound`, `DraftAlreadyReviewed`.

### `supersede(old_lesson_id, new_lesson_id, *, actor=None, reason=None) -> None`

Set `old_lesson_id.superseded_by = new_lesson_id` and transition its maturity to `superseded`. Audited.

- **Raises:** `LessonNotFound`, `SelfSupersede`.

### `soft_delete(table, id, *, actor=None, reason=None) -> None`

Set `deleted_at`, `deleted_by`, `deletion_reason`. Writes a `record_versions` snapshot. This is the only delete path that MCP / CLI expose.

- **Refuses:** if `id` is already deleted (no-op + warn).

### `regen_brief(slug, *, dry_run=False) -> dict`

Regenerate the project brief from the DB. Writes a `brief_snapshots` row first.

- **Returns:** `{"slug", "md_path", "line_count", "snapshot_id", "written"}`.
- **Raises:** `BriefBudgetExceeded` (HR-5), `UnknownSlug`.
- **Refuses:** to write a brief that exceeds the slug's `md_line_cap`.

### `brief_rollback(slug, *, snapshot_id=None, actor=None) -> dict`

Restore a brief from a snapshot. Writes a new snapshot of the current content first (so rollback is itself recoverable).

- **Raises:** `SnapshotNotFound`, `UnknownSlug`.

### `mature_lesson(lesson_id, *, to, actor=None, superseded_by=None, reason=None) -> dict`

The single approved path to transition `lessons_learned.maturity`. HR-9.

- **Raises:** `InvalidMaturityState`, `LockedLesson`, `SupersededByRequired`.
- **Refuses:** to bypass `maturity_locked = 1`.

### `alias_slug(alias, canonical, *, actor=None) -> None`

Create a non-canonical name for a canonical slug. Lookups for `alias` resolve to `canonical`.

- **Raises:** `SlugAlreadyExists`, `CanonicalNotFound`.

### `deprecate_slug(slug, *, canonical=None, actor=None, reason=None) -> None`

Mark a slug deprecated. If `canonical` is given, future writes to `slug` are rerouted to `canonical` (the slug becomes an alias).

- **Raises:** `SlugNotFound`.

### `merge_slugs(from_slug, into_slug, *, actor=None) -> dict`

Merge `from_slug` into `into_slug`. Updates all `project_tags` references in `knowledge_chunks`, `lessons_learned`, `lesson_drafts`, `project_context_card`, and `data_sources`. The `from_slug` row in `slug_registry` becomes `status='merged'` with `canonical_slug = into_slug`. Audited per row.

- **Returns:** `{"updated_chunks", "updated_lessons", "updated_drafts", "updated_cards", "updated_sources", "audit_id"}`.
- **Raises:** `SlugNotFound`, `CannotMergeIntoSelf`.

### `set_visibility(table, id, level, *, actor=None) -> None`

Change visibility of a chunk or lesson.

- **Refuses:** any value not in `visibility_levels`.

### `mark_lesson_used(lesson_id, *, conversation_ref=None) -> None`

Increment usage counter for a lesson. Drives the maturation engine's suggestions.

---

## Admin Methods (operator-only)

These are not exposed via MCP. They require operator-level access (e.g., `runaway_admin` role at T4+, or local operator at T1/T2).

### `audit_verify() -> dict`

Recompute the audit log hash chain end-to-end.

- **Returns:** `{"chain_intact": bool, "row_count": int, "broken_at_row": int | None}`.

### `regen_specialist(name, *, dry_run=False) -> dict`

Regenerate a specialist agent's brief from `specialist_knowledge`.

### `export_json(*, slugs=None, path=None) -> dict`

Serialize the install (or a slug subset) to JSON for T3 git workflows.

### `import_json(path, *, conflict_strategy='report') -> dict`

Import a JSON export. Returns a conflict report; never overwrites without explicit resolution.

### `migrate(*, from_version, to_version, verify=True) -> dict`

The v2 → v3 (or future) migrator. Additive-only per HR-4.

### `hard_delete(table, id, *, i_understand_this_is_permanent=False, backup_first=False, actor=None) -> None`

The single admin escape path. **Both flags must be true** or the call raises `DestructiveFlagsRequired`. Writes to audit. Not exposed via MCP.

---

## Error Hierarchy

All exceptions live in `runaway_context.errors`:

```
RunawayContextError
├── InvalidSlug          (HR-2)
├── UnknownSlug          (HR-2)
├── MissingRequiredField (HR-14)
├── InvalidVisibility
├── OutOfRangeAxes
├── BriefBudgetExceeded  (HR-5)
├── UnknownSnapshot
├── DraftNotFound
├── DraftAlreadyReviewed
├── LessonNotFound
├── SelfSupersede
├── InvalidMaturityState (HR-9)
├── LockedLesson         (HR-9)
├── SupersededByRequired (HR-9)
├── SlugAlreadyExists
├── SlugNotFound
├── CannotMergeIntoSelf
├── DestructiveFlagsRequired (HR-3, L8)
├── NetworkEgressBlocked (HR-1)
├── SchemaVersionMismatch (HR-4)
├── AuditChainBroken     (HR-7)
└── TelemetryWriteDropped (always swallowed; emitted as a counter — HR-8)
```

---

## Contract: every public method

Per HR-14, every public method on `Client` has a docstring with sections:

```
"""<summary>

Args:
    <name>: <description>.

Returns:
    <type>: <description>.

Raises:
    <ExceptionName>: <when>.

Refuses:
    <described preconditions or post-conditions that this method enforces>.
"""
```

A test (`tests/contract/test_hr_14_public_api_documented.py`) walks every non-underscore attribute on `Client` and asserts the docstring contains these markers. Methods that genuinely raise nothing and refuse nothing must still include the sections with text "None." — silence is not allowed.

---

## Threading and Concurrency

- The Client is **thread-safe for reads** across multiple threads sharing one Client instance.
- Writes are serialized internally with a `threading.RLock`.
- The telemetry writer thread is bounded (`put_nowait`) and may drop on saturation (HR-8 guarantees never-blocks).
- The audit log writer is synchronous within the write transaction — it cannot be deferred (HR-7 invariant).

For multi-process access, SQLite's WAL mode + busy-timeout handle concurrency. The Client sets `journal_mode=WAL` and `busy_timeout=5000` on connection.

---

## Tier and Capability Reporting

The Client exposes:

```python
client.tier_check()        # returns dict with current_tier, next_tier, requirements, blockers
client.list_specialists()  # available specialists
client.config              # frozen dict of current config (read-only attribute)
```

`client.config` is the source of truth for which opt-in modules are activated. Test fixtures use this to assert HR-1 invariants at runtime.
