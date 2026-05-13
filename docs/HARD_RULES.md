# Hard Rules Charter (HR-1..HR-15)

These are the rules. They are not suggestions, recommendations, or guidelines. **Every rule has a machine-checkable enforcer. Every enforcer has a named test. If the test does not exist or does not pass, the code does not ship.** No exceptions. No "just this once." No "deferred to next release."

If you are an AI implementing this plan, you are bound by these rules. Your job is to enforce them, not interpret them.

This document mirrors Part 0 of the implementation plan and links each rule to the test file that enforces it.

---

## HR-1 — No network egress by default, ever, at any tier

- **Rule:** The default install path makes zero network calls. Period. Including telemetry, including diagnostics, including update checks.
- **Enforcer:** Unit test `test_no_network_imports` greps every module under `src/runaway_context/` for `socket`, `http`, `urllib`, `requests`, `httpx`, `aiohttp`. Any match in a non-opt-in module fails the build.
- **Opt-in modules** (allowlisted in `test_no_network_imports`): `runaway_context.embeddings.providers.openai`, `runaway_context.embeddings.providers.voyage`, `runaway_context.embeddings.providers.ollama`, `runaway_context.metrics.otlp_exporter`, `runaway_context.federation.refresh_worker`. Each requires explicit user config to activate.
- **Runtime enforcer:** `Client.__init__` walks the loaded module graph and refuses to start if any non-allowlisted network-capable module is loaded but the corresponding config flag is `false`.
- **Test file:** `tests/contract/test_hr_1_no_network.py`
- **Violation handler:** Build fails. Test fails. Runtime refuses to start.

## HR-2 — Every write is project-tagged at the boundary

- **Rule:** No row enters `knowledge_chunks` or `lessons_learned` without a valid canonical project slug, validated at write time.
- **Enforcer:** SQLite triggers (`chunks_require_tags_ins`, `ll_require_tags_ins`) at the SQL layer + write-time guard in `Client._guard_write()` that validates against `slug_registry`.
- **Direct-write block:** Triggers fire `RAISE(ABORT)` on any INSERT to either table where `project_tags` is NULL, empty, or `[]`. The Client further validates each tag against the active slug registry.
- **Test file:** `tests/contract/test_hr_2_writes_require_valid_slug.py`. Attempts every bypass path (direct SQL, malformed CLI, MCP with no project, raw Python via private methods). Each must fail.
- **Violation handler:** Insert rejected at SQL layer. CLI returns exit 2. MCP returns `isError: true`.

## HR-3 — All writes are recoverable

- **Rule:** No CLI, API, or MCP path ever hard-deletes a row from `knowledge_chunks` or `lessons_learned`. Soft delete only.
- **Enforcer:** Schema layer refuses `DELETE` on those tables from non-admin paths. The `Client.soft_delete()` method sets `deleted_at` + `deleted_by` + `deletion_reason` and writes a `record_versions` archive row.
- **Admin escape:** A single CLI command `runaway db hard-delete --table X --id N --i-understand-this-is-permanent --backup-first` exists for emergency use. It is the only path that hard-deletes, requires the backup flag, logs to audit, and is not exposed via MCP.
- **Test file:** `tests/contract/test_hr_3_no_hard_delete_paths.py`. Walks every public method, every CLI subcommand, every MCP tool; confirms none can hard-delete.
- **Violation handler:** Delete rejected at SQL layer.

## HR-4 — Migration is non-destructive

- **Rule:** No v3 schema change drops a column, drops a table, or changes a column's type incompatibly. `ADD COLUMN`, `CREATE TABLE`, `CREATE VIEW`, `CREATE INDEX` only.
- **Enforcer:** Migrator runs `PRAGMA table_info()` before and after each step. Any column lost = abort + restore from backup.
- **Test file:** `tests/contract/test_hr_4_migration_preserves_v2_surface.py`. Loads a frozen v2 fixture DB (`tests/fixtures/v2_clean.db` and `tests/fixtures/v2_with_data.db`), runs the migrator, asserts every v2 column and row count is present.
- **Violation handler:** Migrator aborts; backup restored; exit 2 with diagnostic.

## HR-5 — Tier budgets are enforced in code, not policy

- **Rule:** T1 ≤200 lines. T2 ≤50 lines (pointer-only). T3 ≤150 lines per project. The brief regenerator refuses to write past cap.
- **Enforcer:** `md_writer.write_brief()` counts lines pre-write; raises `BriefBudgetExceeded` if over. Drift detector watches always-loaded files; alerts on every overrun.
- **Test file:** `tests/contract/test_hr_5_regenerator_refuses_overflow.py`. Constructs a corpus that would produce a 200-line brief at the 150 cap, asserts the writer raises and writes nothing.
- **Violation handler:** Write rejected; user gets clear "this corpus exceeds your tier budget, here is what to prune."

## HR-6 — Author identity is opaque

- **Rule:** `author_id` is `sha256(install_id + local_username)[:12]`. Never contains email, hostname, IP, or any other identifying string. `author_display` is opt-in and explicitly marked one-way.
- **Enforcer:** Schema `CHECK (author_id NOT LIKE '%@%' AND author_id NOT LIKE '%.%')`. Triggers on `authors.display_name` (`authors_display_no_email_ins`, `authors_display_no_email_upd`) reject email-shaped values.
- **Test file:** `tests/contract/test_hr_6_author_id_no_pii.py`. Tries to write rows with email-like `author_id` values; all must reject.
- **Violation handler:** Insert rejected at SQL layer.

## HR-7 — Audit log is append-only and chain-verifiable

- **Rule:** `audit_log` rows are never updated, never deleted, and every row's `this_hash` chains from the previous row's `this_hash`. Any tampering is detectable.
- **Enforcer:** Schema triggers `audit_log_no_update` and `audit_log_no_delete` raise `ABORT`. `runaway audit verify` recomputes the chain and reports any break.
- **Test file:** `tests/contract/test_hr_7_audit_chain_unbreakable.py`. Writes 100 audit rows, asserts chain verifies; then tries to UPDATE / DELETE rows; both must fail.
- **Violation handler:** SQL operation aborts. Verifier flags break with row id of first mismatch.

## HR-8 — Telemetry never blocks, never raises

- **Rule:** A telemetry emission failure must never block, delay, or fail a real operation. Telemetry is fire-and-forget.
- **Enforcer:** `metrics.emit()` wraps every body in try/except, drops on failure, never raises. Background writer thread is bounded queue with `put_nowait`.
- **Test files:** `tests/contract/test_hr_8_emit_never_raises.py` (calls `emit()` with the underlying DB locked, then deleted, then permission-denied — none may raise). `tests/contract/test_hr_8_emit_never_blocks.py` (10,000 iterations under 100ms total).
- **Violation handler:** N/A — tests must pass before merge.

## HR-9 — Maturation transitions require explicit approval

- **Rule:** No automatic maturation state change happens to a real lesson without a human or admin-approved automation explicitly applying it. The engine proposes; humans (or their AIs, under human authority) approve.
- **Enforcer:** `lessons_learned.maturity` is changed only via `Client.mature_lesson(id, to=...)`, which records the approval actor in audit log. The maturation engine writes only to `lessons_learned.suggested_maturity` — never to `maturity`.
- **Test file:** `tests/contract/test_hr_9_maturation_no_auto_apply.py`. Runs the maturation engine on a fixture, asserts `maturity` is unchanged and only `suggested_maturity` columns are populated.
- **Violation handler:** Engine attempts to UPDATE `maturity` directly → SQL trigger rejects (if engine is anything other than the approved Client method).

## HR-10 — No silent failures

- **Rule:** Every operation that fails surfaces the failure to the caller. No swallowed exceptions, no return-null-on-error patterns, no "if it didn't work, just skip" branches.
- **Enforcer:** Lint rule + grep flags every catch-all `except:`, `except Exception:`, or `except BaseException:` without re-raise or explicit log. CI fails on warnings.
- **Test file:** `tests/contract/test_hr_10_no_silent_except.py` greps for the catch-all patterns above.
- **Permitted narrow exceptions** — these are not silent failures, they are control-flow primitives and each is documented at the catch site:
  1. `except ImportError: <fallback>` — typed lazy-import fallbacks for optional sibling modules (used in `client.py`, `mcp_server.py`, `brief.py`, `migrate.py`). The fallback path is documented and observable.
  2. The telemetry `metrics.emit()` body is allowed to swallow everything (HR-8: telemetry never blocks, never raises). Marked with `# emit-allowed` comment.
  3. `except sqlite3.OperationalError: <return-empty>` paths that handle "table doesn't exist yet" during partial migration are permitted when the empty result is the documented semantic.
- **Violation handler:** Build fails.

## HR-11 — No deferred work in the shipped plan

- **Rule:** Every item in the plan ships in the named phase or is explicitly removed from the plan. "Deferred to v3.x" is not a valid status. Items are DONE or NOT IN THIS PLAN.
- **Enforcer:** Each section of the plan has a `Status` field that can be one of: `pending` / `in_progress` / `done` / `removed_from_plan`. No other status is valid. The release gate refuses to ship v3.0 with any `pending` or `in_progress` item in P1 or P2.
- **Test file:** `tests/contract/test_hr_11_plan_status_complete.py`. Parses the plan file, asserts every Status field is `done` or `removed_from_plan` for the shipping release.
- **Violation handler:** Release blocked.

## HR-12 — No tests, no merge

- **Rule:** Every feature, every contract, every CLI verb, every MCP tool ships with at least one test that proves it works. PRs without tests for new behavior are rejected by CI.
- **Enforcer:** CI checks that every new public function (anything not prefixed `_`) has at least one test reference. Coverage threshold ≥85% on `src/runaway_context/` excluding `_*` modules.
- **Test file:** `tests/contract/test_hr_12_public_api_coverage.py`. Introspects `runaway_context.Client` and the MCP tool registry; asserts every method/tool has a matching test in the suite.
- **Violation handler:** PR blocked.

## HR-13 — No "TODO" or "FIXME" in shipping code

- **Rule:** Source files in the shipping release contain no `TODO`, `FIXME`, `HACK`, `XXX`, or "deferred" comments. If something needs doing, do it now or remove the path entirely.
- **Enforcer:** Pre-commit hook + CI grep step.
- **Test file:** `tests/contract/test_hr_13_no_todo_in_release.py`. Greps for these markers; fails build on match. Applies to source files; documentation is excluded only when a marker appears inside a fenced code block illustrating the rule.
- **Violation handler:** Commit blocked.

## HR-14 — Contracts are documented and machine-readable

- **Rule:** Every public API method, every MCP tool, every CLI command has a docstring or help string that explicitly states: what it does, what it promises, what it refuses, what errors it raises.
- **Enforcer:** Lint rule + introspection test. Missing docstring on a public symbol fails build.
- **Test file:** `tests/contract/test_hr_14_public_api_documented.py`. Walks `Client.*`, `mcp_server.*`, CLI commands; asserts docstring is non-empty and contains the strings `Returns:`, `Raises:`, `Refuses:` (or notes that none apply).
- **Violation handler:** Build fails.

## HR-15 — The reference implementation must work end-to-end on a clean machine

- **Rule:** A clean checkout of the repo, followed by the documented install command, must produce a working install with all contracts enforced. No "but you also need to..." steps.
- **Enforcer:** `test_clean_install_works` runs in a sandbox (Docker container with nothing but Python + sqlite), checks out, installs, runs the entire contract test suite, expects pass.
- **Test file:** `tests/contract/test_hr_15_clean_install_works.py`
- **Violation handler:** Build fails.

---

## The Adopter's AI Contract

By installing RunawayContext, the installing AI agent commits to:

1. **Read the hard rules** (this file and Part 0 of the implementation plan).
2. **Never weaken or work around a hard rule.** If a rule appears to need changing, propose the change in a versioned PR titled `RULE-CHANGE: HR-N — <summary>`. Do not silently work around it.
3. **Run the contract test suite after install** and report the result honestly.
4. **If a contract test fails, report the failure** — do not mark the install complete. Report the test name, the rule it enforces, and what was tried.
5. **Honor the no-skipping clause:** every install step completes or the install does not complete.
6. **Never use `@pytest.mark.skip` in `tests/contract/`** to make CI green.
7. **Never introduce a config flag** that weakens a hard rule without an explicit RULE-CHANGE PR that updates both the rule and the test.

The AI's adherence is checkable: contract tests pass or they do not. There is no middle ground.

---

## Quick Reference (print and pin)

```
HR-1   No network egress by default
HR-2   Every write is project-tagged at the boundary
HR-3   All writes are recoverable (soft delete only)
HR-4   Migration is non-destructive
HR-5   Tier budgets enforced in code
HR-6   Author identity is opaque
HR-7   Audit log is append-only and chain-verifiable
HR-8   Telemetry never blocks, never raises
HR-9   Maturation transitions require explicit approval
HR-10  No silent failures
HR-11  No deferred work in the shipped plan
HR-12  No tests, no merge
HR-13  No TODO/FIXME in shipping code
HR-14  Contracts are documented and machine-readable
HR-15  Reference implementation works end-to-end on a clean machine
```

Read this before every commit.
