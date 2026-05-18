"""`runaway` CLI dispatcher (E8 entry point).

This is the canonical stdlib-only dispatcher wired by ``runaway = runaway_context.cli:main``
in ``pyproject.toml``. Every subcommand has its own ``--help`` text (HR-14) and a small
handler that calls into :class:`runaway_context.Client` or a sibling module via lazy
import — the Client class is built by a parallel work-stream and may not be present
at the moment any individual handler is invoked.

Exit codes:

    0 — success
    1 — generic error (uncaught exception; traceback surfaced per HR-10)
    2 — refusal (HR-2 invalid slug, HR-3 hard-delete without flags, HR-7 audit broken, etc.)
    3 — config / env missing (e.g. install dir not initialised)

Refuses:
    Destructive operations without their explicit safety flags (HR-3, L8).
    Imports of modules that are not yet present in the package surface
    (with a friendly diagnostic, never a silent ``None``).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from runaway_context.config import Config
from runaway_context.errors import (
    AuditChainBroken,
    BriefBudgetExceeded,
    ConflictReported,
    HardDeleteRefused,
    InvalidProjectSlug,
    MaturationApprovalRequired,
    MigrationAborted,
    NetworkEgressBlocked,
    RunawayContextError,
    TierGateFailed,
)


# ---------------------------------------------------------------------------
# Exit-code constants
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_CONFIG = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _eprint(*parts: Any) -> None:
    """Print to stderr without a trailing extra newline.

    Returns:
        None.
    Refuses:
        Nothing — this is plain stderr output.
    """
    print(*parts, file=sys.stderr)


def _load_config(args: argparse.Namespace) -> Config:
    """Load Config honoring ``--install-dir`` from argparse if present.

    Returns:
        A :class:`runaway_context.config.Config` instance.
    Refuses:
        Nothing — config defaults always resolve to ``~/_knowledge``.
    """
    install_dir = getattr(args, "install_dir", None)
    return Config.load(Path(install_dir).expanduser() if install_dir else None)


def _get_client(args: argparse.Namespace):
    """Lazy-import and instantiate :class:`runaway_context.Client`.

    Returns:
        A live ``Client`` instance ready for read/write operations.
    Refuses:
        With exit-code 3 if the Client surface is not yet importable
        (sibling work-stream has not landed). Exit-code 3 because the
        condition is environmental, not a user refusal.
    """
    cfg = _load_config(args)
    try:
        from runaway_context.client import Client  # type: ignore  # lazy
    except ImportError as e:
        _eprint("runaway: Client class is not yet wired up in this checkout.")
        _eprint(f"        ({e})")
        _eprint("        This dispatcher is intentionally tolerant of in-flight work-streams.")
        sys.exit(EXIT_CONFIG)
    return Client(config=cfg)


def _parse_csv(value: Optional[str]) -> List[str]:
    """Split a comma-separated string; return an empty list when value is falsy.

    Returns:
        List of stripped non-empty strings.
    """
    if not value:
        return []
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _resolve_actor(args: argparse.Namespace) -> str:
    """Resolve the audit actor for a CLI invocation.

    Returns:
        ``args.actor`` if supplied, otherwise the install's opaque ``author_id``.

    Refuses:
        Nothing — falls back through env / getpass within ``identity.current_author_id``.
    """
    if getattr(args, "actor", None):
        return str(args.actor)
    from runaway_context.identity import current_author_id

    cfg = _load_config(args)
    return current_author_id(cfg.install_dir)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    """Run the interactive init wizard.

    Returns:
        0 on successful completion.
    Refuses:
        Nothing — non-interactive mode writes defaults and exits.
    """
    from runaway_context import init as init_mod

    install_dir = Path(args.install_dir).expanduser() if args.install_dir else None
    init_mod.run(install_dir=install_dir, non_interactive=bool(args.non_interactive))
    return EXIT_OK


def cmd_brief(args: argparse.Namespace) -> int:
    """Print the canonical brief for a project.

    Returns:
        0 with brief text on stdout.
    Refuses:
        Unregistered project slugs (HR-2) — exit 2 via the Client.
    """
    client = _get_client(args)
    brief = client.get_brief(args.project)
    card = brief.get("card") or {}
    print(f"# {args.project}")
    md_path = card.get("md_path")
    if md_path:
        print(f"_md_path:_ {md_path}")
    print(f"_last_rebuilt:_ {card.get('last_rebuilt') or 'never'}")
    warnings = brief.get("warnings") or []
    if warnings:
        print("\n## Top warnings")
        for w in warnings:
            print(f"- LL#{w.get('id')} — {w.get('title')}")
    lessons = brief.get("lessons") or []
    if lessons:
        print("\n## Active lessons")
        for ll in lessons:
            print(
                f"- LL#{ll['id']} [{ll.get('maturity', 'active')}] {ll.get('title', '')}"
            )
    chunks = brief.get("chunks") or []
    if chunks:
        print("\n## Active chunks")
        for ch in chunks:
            print(f"- KS#{ch['id']} ({ch.get('topic', '')}) {ch.get('title', '')}")
    return EXIT_OK


def cmd_log_lesson(args: argparse.Namespace) -> int:
    """Append a new lesson-learned to the knowledge store.

    Returns:
        0 and prints the new lesson identifier (``LL#N``).
    Refuses:
        Invalid project slugs (HR-2). Severity axes outside 1..5 (E4).
    """
    client = _get_client(args)
    projects = _parse_csv(args.projects)
    lesson_id = client.log_lesson(
        title=args.title,
        project_tags=projects,
        what_happened=args.what,
        why=args.why,
        the_fix=args.fix,
        prevention_rule=args.rule,
        severity=args.severity,
        blast_radius=args.blast,
        frequency=args.freq,
        reversibility=args.rev,
    )
    print(f"LL#{lesson_id}")
    return EXIT_OK


def cmd_propose_knowledge(args: argparse.Namespace) -> int:
    """Append a proposed knowledge chunk pending approval.

    Returns:
        0 and prints the draft identifier.
    Refuses:
        Invalid project slugs (HR-2).
    """
    client = _get_client(args)
    tags = _parse_csv(args.tags)
    draft_id = client.propose_knowledge(
        project=args.project,
        topic=args.topic,
        title=args.title,
        body=args.body,
        tags=tags,
    )
    print(f"draft#{draft_id}")
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    """Run a search over chunks and/or lessons.

    Returns:
        0 with newline-delimited results (``kind\\tid\\ttitle``).
    Refuses:
        Nothing — empty result sets print nothing and exit 0.
    """
    client = _get_client(args)
    results = []
    kind = (args.kind or "both").lower()
    if kind in ("chunks", "both"):
        for row in client.search_chunks(
            query=args.query, project=args.project, limit=args.limit
        ):
            results.append({"kind": "chunk", **row})
    if kind in ("lessons", "both"):
        for row in client.search_lessons(
            query=args.query, project=args.project, limit=args.limit
        ):
            results.append({"kind": "lesson", **row})
    for row in results:
        k = row.get("kind", "?")
        rid = row.get("id", "?")
        title = row.get("title", "")
        print(f"{k}\t{rid}\t{title}")
    return EXIT_OK


def cmd_list_lessons(args: argparse.Namespace) -> int:
    """List lessons with optional project / status / maturity filters.

    Returns:
        0 with one lesson per line: ``LL#id  maturity  status  title``.
    Refuses:
        Nothing.
    """
    client = _get_client(args)
    lessons = client.list_lessons(
        project=args.project,
        status=args.status,
        maturity=args.maturity,
    )
    for lesson in lessons:
        print(
            "LL#{id}\t{maturity}\t{status}\t{title}".format(
                id=lesson.get("id"),
                maturity=lesson.get("maturity", "-"),
                status=lesson.get("status", "-"),
                title=lesson.get("title", ""),
            )
        )
    return EXIT_OK


def cmd_list_drafts(args: argparse.Namespace) -> int:
    """List pending drafts awaiting approval.

    Returns:
        0 with one draft per line.
    Refuses:
        Nothing.
    """
    client = _get_client(args)
    drafts = client.list_drafts()
    for draft in drafts:
        print(
            "draft#{id}\t{project}\t{title}".format(
                id=draft.get("id"),
                project=draft.get("project", "-"),
                title=draft.get("title", ""),
            )
        )
    return EXIT_OK


def cmd_approve_draft(args: argparse.Namespace) -> int:
    """Approve a pending draft and promote it to its destination table.

    Returns:
        0 and prints the resulting row id.
    Refuses:
        Drafts that no longer exist or have already been resolved.
    """
    client = _get_client(args)
    new_id = client.approve_draft(args.draft_id, actor=args.actor)
    print(f"approved {args.draft_id} -> {new_id}")
    return EXIT_OK


def cmd_reject_draft(args: argparse.Namespace) -> int:
    """Reject a pending draft with optional notes.

    Returns:
        0 on rejection.
    Refuses:
        Drafts that no longer exist or have already been resolved.
    """
    client = _get_client(args)
    client.reject_draft(args.draft_id, actor=args.actor, notes=args.notes)
    print(f"rejected {args.draft_id}")
    return EXIT_OK


def cmd_mature(args: argparse.Namespace) -> int:
    """Apply a maturation transition under explicit human approval (HR-9).

    Returns:
        0 and echoes the transition.
    Refuses:
        Transitions without an ``--actor`` (HR-9).
        Transitions into a state not on the canonical curve.
    """
    client = _get_client(args)
    client.mature_lesson(
        lesson_id=args.lesson_id,
        to=args.to,
        actor=args.actor,
        reason=args.reason,
    )
    print(f"LL#{args.lesson_id} -> {args.to}")
    return EXIT_OK


def cmd_supersede(args: argparse.Namespace) -> int:
    """Mark one lesson as superseded by another.

    Returns:
        0 on success.
    Refuses:
        Either lesson missing; cycles in the supersession graph.
    """
    client = _get_client(args)
    client.supersede(
        old_lesson_id=args.old_id, new_lesson_id=args.new_id, actor=args.actor
    )
    print(f"LL#{args.old_id} -> superseded by LL#{args.new_id}")
    return EXIT_OK


def cmd_regen_brief(args: argparse.Namespace) -> int:
    """Regenerate the project brief honoring PRESERVE blocks.

    Returns:
        0 on success; prints the destination path or a dry-run diff.
    Refuses:
        Briefs that would exceed the tier line cap (HR-5 → :class:`BriefBudgetExceeded`).
    """
    client = _get_client(args)
    result = client.regen_brief(args.project, dry_run=bool(args.dry_run))
    if args.dry_run:
        print(result if isinstance(result, str) else json.dumps(result, indent=2))
    else:
        print(f"wrote {result}")
    return EXIT_OK


def cmd_brief_preview(args: argparse.Namespace) -> int:
    """Show what ``regen-brief`` would write, without writing.

    Returns:
        0 with the preview text on stdout.
    Refuses:
        Nothing — preview never writes.
    """
    try:
        from runaway_context import brief_preview as bp  # type: ignore
        text = bp.preview(args.project)
        print(text)
        return EXIT_OK
    except ImportError:
        # Fall back to Client.regen_brief(..., dry_run=True) so we still produce output.
        client = _get_client(args)
        result = client.regen_brief(args.project, dry_run=True)
        print(result if isinstance(result, str) else json.dumps(result, indent=2))
        return EXIT_OK


def _format_tag_list(project_tags_json: str, free_tags_json: str = "[]") -> str:
    """Render up-to-5 tags as ``[ tag1, tag2 ]`` (or empty string if none).

    Project tags come first (they're the canonical-slug list), free-text tags
    fill remaining slots. Cap at 5 to keep the pointer line under ~150 chars.
    """
    try:
        proj = json.loads(project_tags_json) if project_tags_json else []
        free = json.loads(free_tags_json) if free_tags_json else []
    except (ValueError, TypeError):
        return ""
    if not isinstance(proj, list):
        proj = []
    if not isinstance(free, list):
        free = []
    seen: set = set()
    out: List[str] = []
    for tag in list(proj) + list(free):
        if not isinstance(tag, str):
            continue
        tag = tag.strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= 5:
            break
    if not out:
        return ""
    return " [" + ", ".join(out) + "]"


def cmd_brief_rewrite_pointers(args: argparse.Namespace) -> int:
    """Rewrite Claude Code per-project ``MEMORY.md`` files as pointer-only indexes.

    For every per-project memory dir, this regenerates ``MEMORY.md`` from the
    DB rows currently linked under the canonical slug (lessons + chunks),
    emitting one ``- LL#N — hook`` or ``- KC#N — hook`` line per row.

    Returns:
        ``EXIT_OK`` always. Per-project failures are surfaced in the JSON
        report rather than raising.

    Refuses:
        Writing into directories whose canonical slug cannot be resolved.
        Touching files whose first 256 bytes contain a hand-written marker
        (HR-5 no-clobber); we only rewrite files Claude or RunawayContext
        previously generated.
    """
    from runaway_context import memory_ingest as mi
    from runaway_context.client import Client

    cfg = _load_config(args)
    client = Client(install_dir=cfg.install_dir)
    claude_root = Path(args.claude_root).expanduser() if args.claude_root else None
    report = {
        "dry_run": bool(args.dry_run),
        "files_rewritten": [],
        "files_skipped": [],
    }
    for memdir in mi.discover_memory_dirs(claude_projects_root=claude_root):
        slug = mi._resolve_project_slug(memdir, client)  # noqa: SLF001
        index = memdir / "MEMORY.md"
        if slug is None:
            report["files_skipped"].append(
                {"path": str(index), "reason": "unresolved slug"}
            )
            continue
        # HR-5: only rewrite files whose head looks like a previously-generated
        # pointer index, not user-authored prose.
        if index.exists():
            head = index.read_text(encoding="utf-8", errors="replace")[:256]
            if "AUTO-GENERATED" not in head and "pointer index" not in head.lower():
                report["files_skipped"].append(
                    {"path": str(index), "reason": "hand-edited (HR-5)"}
                )
                continue
        lines = [
            "<!-- AUTO-GENERATED: runaway brief rewrite-pointers -->",
            "# MEMORY.md — pointer index",
            "",
        ]
        import sqlite3
        conn = sqlite3.connect(str(client._knowledge_db))
        try:
            for rid, title, tags_json in conn.execute(
                "SELECT id, title, COALESCE(project_tags, '[]') "
                "FROM lessons_learned "
                "WHERE project = ? AND deleted_at IS NULL ORDER BY id",
                (slug,),
            ):
                hook = (title or "").strip().splitlines()[0][:120]
                tags = _format_tag_list(tags_json)
                lines.append(f"- LL#{rid}{tags} — {hook}")
            for rid, title, tags_json, free_tags in conn.execute(
                "SELECT id, title, COALESCE(project_tags, '[]'), "
                "COALESCE(tags, '[]') "
                "FROM knowledge_chunks "
                "WHERE project = ? AND deleted_at IS NULL ORDER BY id",
                (slug,),
            ):
                hook = (title or "").strip().splitlines()[0][:120]
                tags = _format_tag_list(tags_json, free_tags)
                lines.append(f"- KC#{rid}{tags} — {hook}")
        finally:
            conn.close()
        content = "\n".join(lines) + "\n"
        if not args.dry_run:
            index.write_text(content)
        report["files_rewritten"].append(str(index))
    print(json.dumps(report, indent=2))
    return EXIT_OK


def cmd_brief_rollback(args: argparse.Namespace) -> int:
    """Restore a project brief from a snapshot in ``brief_snapshots``.

    Returns:
        0 with the restored path on stdout.
    Refuses:
        Snapshots that do not exist for the given project.
    """
    try:
        from runaway_context import brief_preview as bp  # type: ignore
    except ImportError as e:
        _eprint("runaway: brief snapshot module not yet present in this checkout.")
        _eprint(f"        ({e})")
        return EXIT_CONFIG
    snapshot_id = args.snapshot_id
    path = bp.rollback(args.project, snapshot_id=snapshot_id)
    print(f"restored {path}")
    return EXIT_OK


def cmd_slug_register(args: argparse.Namespace) -> int:
    """Register a new canonical project slug.

    Returns:
        0 on success.
    Refuses:
        Slugs that fail the format check (HR-2: lowercase snake, ≤64 chars).
        Slugs that are already registered or aliased.
    """
    cfg = _load_config(args)
    # Slug registration must work even before Client is wired up — it's the
    # first thing an adopter does after install. We talk directly to the DB.
    from runaway_context._slugs import is_valid_slug_format
    from runaway_context._db import connect, transaction

    slug = args.slug
    if not is_valid_slug_format(slug):
        _eprint(f"runaway: '{slug}' is not a valid slug "
                f"(lowercase snake_case, 2..64 chars)")
        return EXIT_REFUSED

    if not cfg.knowledge_db.exists():
        _eprint(f"runaway: knowledge.db does not exist at {cfg.knowledge_db}")
        _eprint("        run `runaway db migrate` (or `runaway init`) first.")
        return EXIT_CONFIG

    conn = connect(cfg.knowledge_db)
    try:
        try:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO slug_registry (slug, description) VALUES (?, ?)",
                    (slug, args.description),
                )
        except Exception as e:  # noqa: BLE001 — surfaced to caller per HR-10
            msg = str(e).lower()
            if "unique" in msg or "constraint" in msg:
                _eprint(f"runaway: slug '{slug}' is already registered")
                return EXIT_REFUSED
            raise
    finally:
        conn.close()
    print(f"registered slug: {slug}")
    return EXIT_OK


def cmd_slug_list(args: argparse.Namespace) -> int:
    """List registered project slugs.

    Returns:
        0 with one slug per line: ``slug<TAB>status<TAB>description``.
    Refuses:
        Nothing.
    """
    cfg = _load_config(args)
    from runaway_context._db import connect

    if not cfg.knowledge_db.exists():
        _eprint(f"runaway: knowledge.db does not exist at {cfg.knowledge_db}")
        return EXIT_CONFIG

    conn = connect(cfg.knowledge_db)
    try:
        rows = conn.execute(
            "SELECT slug, COALESCE(status,'active') AS status, "
            "COALESCE(description,'') AS description FROM slug_registry "
            "ORDER BY slug"
        ).fetchall()
        for row in rows:
            print(f"{row['slug']}\t{row['status']}\t{row['description']}")
    finally:
        conn.close()
    return EXIT_OK


def cmd_slug_alias(args: argparse.Namespace) -> int:
    """Alias one slug to a canonical slug.

    Returns:
        0 on success.
    Refuses:
        Either side missing from the registry.
    """
    client = _get_client(args)
    client.alias_slug(alias=args.alias, canonical=args.canonical)
    print(f"aliased {args.alias} -> {args.canonical}")
    return EXIT_OK


def cmd_slug_deprecate(args: argparse.Namespace) -> int:
    """Deprecate a slug; existing rows are preserved (HR-3).

    Returns:
        0 on success.
    Refuses:
        Slugs not present in the registry.
    """
    client = _get_client(args)
    client.deprecate_slug(slug=args.slug, reason=args.reason)
    print(f"deprecated {args.slug}")
    return EXIT_OK


def cmd_slug_merge(args: argparse.Namespace) -> int:
    """Merge one slug into another; old slug becomes an alias of the new one.

    Returns:
        0 on success.
    Refuses:
        Either slug missing; the operation would create a cycle.
    """
    client = _get_client(args)
    client.merge_slugs(from_slug=args.from_slug, to_slug=args.to_slug)
    print(f"merged {args.from_slug} -> {args.to_slug}")
    return EXIT_OK


def cmd_db_migrate(args: argparse.Namespace) -> int:
    """Run :func:`runaway_context.migrate.migrate` against the configured DBs.

    Returns:
        0 with a one-line summary on stdout.
    Refuses:
        Destructive schema changes (HR-4 → :class:`MigrationAborted`).
    """
    from runaway_context.migrate import migrate

    cfg = _load_config(args)
    knowledge_db = Path(args.knowledge_db).expanduser() if args.knowledge_db else cfg.knowledge_db
    sessions_db = Path(args.sessions_db).expanduser() if args.sessions_db else cfg.sessions_db
    metrics_db = Path(args.metrics_db).expanduser() if args.metrics_db else cfg.metrics_db

    # Make sure parent dirs exist for ad-hoc invocations
    for p in (knowledge_db, sessions_db, metrics_db):
        if p is not None:
            Path(p).parent.mkdir(parents=True, exist_ok=True)

    report = migrate(
        knowledge_db=Path(knowledge_db),
        sessions_db=Path(sessions_db) if sessions_db else None,
        metrics_db=Path(metrics_db) if metrics_db else None,
    )
    print(
        f"migrated: {len(report.steps_applied)} steps applied; "
        f"knowledge={knowledge_db}"
    )
    if report.aborted_reason:
        _eprint(f"runaway: migration aborted — {report.aborted_reason}")
        return EXIT_REFUSED
    return EXIT_OK


def cmd_db_import_data_map(args: argparse.Namespace) -> int:
    """Parse a markdown data-map file and register sources into ``data_sources``.

    Tolerant parser: scans every markdown table and acts on any whose headers
    name a ``system`` and ``name`` (or recognised synonyms). The migrator
    never auto-runs this — it must be explicit because the markdown is
    free-form and the importer may misclassify edge cases.

    Returns:
        ``EXIT_OK`` always; the JSON report enumerates skipped rows.

    Refuses:
        Nothing at the CLI layer. The underlying parser records per-row
        skips in ``ImportReport.notes`` rather than raising.
    """
    from runaway_context.cross_system import import_from_markdown

    cfg = _load_config(args)
    md = Path(args.from_file).expanduser()
    report = import_from_markdown(
        Path(cfg.knowledge_db), md,
        default_kind=args.default_kind,
        project=args.project,
        dry_run=bool(args.dry_run),
    )
    out = {
        "dry_run": bool(args.dry_run),
        "sources_added": report.sources_added,
        "sources_skipped": report.sources_skipped,
        "mappings_added": report.mappings_added,
        "mappings_skipped": report.mappings_skipped,
        "notes": report.notes[:40],
    }
    print(json.dumps(out, indent=2))
    return EXIT_OK


def cmd_db_hard_delete(args: argparse.Namespace) -> int:
    """The ONLY hard-delete path (HR-3). Refuses without both safety flags.

    Returns:
        0 on success, with a one-line audit summary.
    Refuses:
        Invocations missing ``--i-understand-this-is-permanent`` or
        ``--backup-first``. Tables not in the soft-delete allowlist.
    """
    if not args.i_understand_this_is_permanent:
        _eprint("runaway: refusing hard-delete without --i-understand-this-is-permanent (HR-3)")
        return EXIT_REFUSED
    if not args.backup_first:
        _eprint("runaway: refusing hard-delete without --backup-first (HR-3)")
        return EXIT_REFUSED
    if args.table not in ("knowledge_chunks", "lessons_learned"):
        _eprint(f"runaway: hard-delete refused: table {args.table!r} not in soft-delete allowlist")
        return EXIT_REFUSED

    # Take a snapshot first (the --backup-first contract). We always copy the
    # DB before mutating it so the operation is recoverable from the snapshot.
    import shutil as _shutil
    from runaway_context import audit as _audit

    cfg = _load_config(args)
    backup_path = Path(str(cfg.knowledge_db) + ".pre-hard-delete.bak")
    _shutil.copy2(cfg.knowledge_db, backup_path)

    conn = sqlite3.connect(str(cfg.knowledge_db))
    try:
        actor = _resolve_actor(args)
        with conn:
            row = conn.execute(
                f"SELECT * FROM {args.table} WHERE id = ?", (args.id,)
            ).fetchone()
            if row is None:
                _eprint(f"runaway: hard-delete refused: {args.table}#{args.id} not found")
                return EXIT_REFUSED
            conn.execute(f"DELETE FROM {args.table} WHERE id = ?", (args.id,))
            _audit.append(
                conn,
                actor=actor,
                action="hard_delete",
                target_table=args.table,
                target_id=args.id,
                details={"backup_path": str(backup_path)},
            )
    finally:
        conn.close()
    print(f"hard-deleted {args.table}#{args.id} (backup at {backup_path})")
    return EXIT_OK


def cmd_audit_verify(args: argparse.Namespace) -> int:
    """Verify the audit_log hash chain (HR-7).

    Returns:
        0 when the chain is intact.
    Refuses:
        2 when any break is detected, with the row id of the first mismatch.
    """
    client = _get_client(args)
    ok, first_bad, reason = client.audit_verify()
    if ok:
        print("audit verify: OK")
        return EXIT_OK
    _eprint(f"audit verify: chain broken at id={first_bad}: {reason}")
    return EXIT_REFUSED


def cmd_stats(args: argparse.Namespace) -> int:
    """Print the terminal-first dashboard equivalent (E21).

    Returns:
        0 with the formatted stats report.
    Refuses:
        Nothing.
    """
    try:
        from runaway_context import stats as stats_mod  # type: ignore
    except ImportError as e:
        _eprint("runaway: stats module not yet present in this checkout.")
        _eprint(f"        ({e})")
        return EXIT_CONFIG
    cfg = _load_config(args)
    snapshot = stats_mod.compute(cfg.knowledge_db, install_dir=cfg.install_dir)
    stats_mod.print_report(snapshot)
    return EXIT_OK


def cmd_tier_check(args: argparse.Namespace) -> int:
    """Print the current tier and the next promotion gate.

    Returns:
        0 with two lines: current tier, next-gate description.
    Refuses:
        Nothing.
    """
    cfg = _load_config(args)
    print(f"current tier: {cfg.tier}")
    next_gate = {
        "T0": "T1 — accumulate 5+ project-specific notes manually",
        "T1": "T2 — 30 days of use, 10 lessons across 2 projects, at least one drift warning",
        "T2": "T3 — a second author_id has logged an approved lesson in the last 30 days",
        "T3": "T4 — 5 resolved import conflicts, 1 designated admin, 30 days under T3",
        "T4": "T5 — SSO configured, federation source identified, 30 days of clean audit",
        "T5": "(top tier — no further gate)",
    }
    print(f"next gate: {next_gate.get(cfg.tier, '(unknown tier)')}")
    return EXIT_OK


def cmd_tier_promote(args: argparse.Namespace) -> int:
    """Run the tier-promotion gate check, and (unless ``--check``) apply it.

    Returns:
        0 when the gate passes (and the new tier is written, unless ``--check``).
    Refuses:
        Failed gate (TierGateFailed) — exit 2.
    """
    cfg = _load_config(args)
    target = args.to
    passed, reason = _evaluate_tier_gate(cfg, target)
    if not passed:
        _eprint(f"runaway: tier promotion gate failed: {reason}")
        return EXIT_REFUSED
    if args.check:
        print(f"gate passes — would promote to {target}")
        return EXIT_OK
    cfg.tier = target
    cfg.save()
    print(f"promoted to {target}")
    return EXIT_OK


def _evaluate_tier_gate(cfg: Config, target: str):
    """Evaluate the documented tier-promotion gate for *target* (P5).

    Returns:
        Tuple ``(passed: bool, reason: str)``. ``reason`` describes the gate
        criterion (passed or failed).

    Refuses:
        Unknown target tiers ``passed=False``.
    """
    valid = ("T1", "T2", "T3", "T4", "T5")
    if target not in valid:
        return False, f"unknown target tier {target!r} (expected one of {valid})"

    try:
        conn = sqlite3.connect(str(cfg.knowledge_db))
    except sqlite3.Error as exc:
        return False, f"cannot open knowledge.db: {exc}"

    try:
        if target == "T1":
            return True, "T1 has no gate beyond a populated DB"
        if target == "T2":
            lessons = conn.execute(
                "SELECT COUNT(*) FROM lessons_learned WHERE deleted_at IS NULL"
            ).fetchone()[0]
            projects = conn.execute(
                "SELECT COUNT(*) FROM project_context_card"
            ).fetchone()[0]
            if lessons < 10:
                return False, f"T2 needs >=10 lessons (have {lessons})"
            if projects < 2:
                return False, f"T2 needs >=2 projects (have {projects})"
            return True, "T2 gate passes"
        if target == "T3":
            second_author = conn.execute(
                "SELECT COUNT(DISTINCT author_id) FROM lessons_learned "
                "WHERE author_id IS NOT NULL"
            ).fetchone()[0]
            if second_author < 2:
                return False, (
                    f"T3 needs >=2 distinct author_ids on lessons (have {second_author})"
                )
            return True, "T3 gate passes"
        if target == "T4":
            admins = conn.execute(
                "SELECT COUNT(*) FROM authors WHERE is_admin = 1"
            ).fetchone()[0]
            if admins < 1:
                return False, "T4 needs >=1 admin (authors.is_admin = 1)"
            return True, "T4 gate passes"
        if target == "T5":
            return False, (
                "T5 promotion requires SSO + federation configured "
                "(see docs/specs/SSO_INTEGRATION.md, FEDERATION.md)"
            )
        return False, "unreachable"
    finally:
        conn.close()


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Undo a RunawayContext install (archive → optional markdown export → remove).

    Returns:
        0 on success (including ``--dry-run``).
        2 when ``--yes`` was omitted on a non-dry-run invocation.

    Refuses:
        Destructive removal without ``--yes`` (HR-3 spirit). Reverting without
        an ``install_manifest.json`` (you're trying to undo something we have
        no record of).
    """
    from runaway_context import uninstall as _uninstall

    cfg = _load_config(args)
    install_dir = cfg.install_dir
    dry = bool(args.dry_run)
    if not dry and not args.yes:
        _eprint(
            "runaway: uninstall refused — pass --yes to confirm "
            "destructive operation (or use --dry-run to preview)."
        )
        return EXIT_REFUSED

    export_md = Path(args.export_markdown).expanduser() if args.export_markdown else None
    archive_dir = Path(args.archive_dir).expanduser() if args.archive_dir else None
    try:
        report = _uninstall.uninstall(
            install_dir,
            dry_run=dry,
            archive=not args.no_archive,
            archive_dir=archive_dir,
            export_markdown=export_md,
            revert=bool(args.revert),
            keep_db=bool(args.keep_db),
            confirm=args.yes,
        )
    except FileNotFoundError as exc:
        _eprint(f"runaway: uninstall could not complete: {exc}")
        return EXIT_REFUSED
    except PermissionError as exc:
        _eprint(f"runaway: uninstall refused: {exc}")
        return EXIT_REFUSED

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return EXIT_OK


def cmd_export_markdown(args: argparse.Namespace) -> int:
    """Export every lesson/chunk/brief to a portable markdown tree.

    Returns:
        0 on success; 2 when the target directory is non-empty and
        ``--overwrite`` was not supplied.
    Refuses:
        Writing into a non-empty directory without ``--overwrite``.
    """
    from runaway_context import uninstall as _uninstall

    cfg = _load_config(args)
    output_dir = Path(args.output).expanduser()
    try:
        counts = _uninstall.export_to_markdown(
            cfg.install_dir, output_dir, overwrite=bool(args.overwrite)
        )
    except FileExistsError as exc:
        _eprint(f"runaway: export-markdown refused: {exc}")
        return EXIT_REFUSED
    print(json.dumps({"path": str(output_dir), **counts}, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run environment diagnostics and optionally apply fixes.

    Sub-options:
        --fix-constitution  rewrite ~/CLAUDE.md Session Memory block (prompted, reversible)
        --fix-memory        rewrite MEMORY.md fetch-detail blocks (prompted, reversible)
        --fix-mcp           merge runaway-context into ~/.claude/mcp.json (prompted)
        --fix-hook          wire capture_session.sh into ~/.claude/settings.json (prompted)
        --fix-all           run every --fix-* in turn
        --revert <ts>       restore files from a prior fix batch
        --list-reverts      list available timestamps
        --yes               skip prompts (still creates backups)

    Returns:
        0 on success (including no-op fixes); 2 when at least one FAIL
        finding exists from the read-only diagnostic pass.
    Refuses:
        Writing anywhere without a prior backup (see doctor_fix module).
    """
    from runaway_context import doctor as _doctor
    from runaway_context import doctor_fix as _fix

    cfg = _load_config(args)

    if getattr(args, "scan", False):
        candidates = _doctor.scan_install_candidates()
        if args.json:
            print(json.dumps(candidates, indent=2, default=str))
        elif not candidates:
            print("no knowledge.db candidates found on standard roots")
        else:
            print(f"runaway doctor --scan — {len(candidates)} candidate(s):\n")
            for c in candidates:
                rc = c.get("row_counts") or {}
                rc_str = ", ".join(f"{k}={v}" for k, v in rc.items()) or "(empty)"
                notes = f"  -- {c['notes']}" if c.get("notes") else ""
                print(f"  [{c['shape']:>10}] {c['path']}{notes}")
                print(f"             {rc_str}")
        return EXIT_OK

    if getattr(args, "list_reverts", False):
        batches = _fix.list_batches()
        if not batches:
            print("no doctor backups recorded")
        else:
            for ts in batches:
                print(ts)
        return EXIT_OK

    if getattr(args, "revert", None):
        n = _fix.revert(args.revert)
        return EXIT_OK if n > 0 else EXIT_USAGE

    assume_yes = bool(getattr(args, "yes", False))
    any_fix_requested = any(
        getattr(args, attr, False)
        for attr in ("fix_constitution", "fix_memory", "fix_mcp", "fix_hook", "fix_all")
    )
    if any_fix_requested:
        if args.fix_all or args.fix_constitution:
            _fix.fix_constitution(assume_yes=assume_yes)
        if args.fix_all or args.fix_memory:
            _fix.fix_memory_md(install_dir=cfg.install_dir, assume_yes=assume_yes)
        if args.fix_all or args.fix_mcp:
            _fix.fix_mcp(assume_yes=assume_yes)
        if args.fix_all or args.fix_hook:
            _fix.fix_capture_hook(install_dir=cfg.install_dir, assume_yes=assume_yes)
        return EXIT_OK

    return _doctor.cli_main(cfg.install_dir, json_output=bool(args.json))


def cmd_drift_check(args: argparse.Namespace) -> int:
    """Run the predictive-drift rules and print findings.

    Returns:
        0 with one finding per line.
    Refuses:
        Nothing.
    """
    try:
        from runaway_context import drift  # type: ignore
    except ImportError as e:
        _eprint("runaway: drift module not yet present in this checkout.")
        _eprint(f"        ({e})")
        return EXIT_CONFIG
    cfg = _load_config(args)
    findings = drift.run_check(cfg)
    for finding in findings:
        print(finding)
    return EXIT_OK


def cmd_specialist_register(args: argparse.Namespace) -> int:
    """Register a specialist agent in the registry.

    Returns:
        0 on success.
    Refuses:
        Specialists whose name is already registered.
    """
    client = _get_client(args)
    client.register_specialist(
        name=args.name, domain=args.domain, description=args.description
    )
    print(f"registered specialist: {args.name}")
    return EXIT_OK


def cmd_specialist_list(args: argparse.Namespace) -> int:
    """List registered specialist agents.

    Returns:
        0 with one specialist per line.
    Refuses:
        Nothing.
    """
    client = _get_client(args)
    for spec in client.list_specialists():
        print(
            "{name}\t{domain}\t{description}".format(
                name=spec.get("name", "?"),
                domain=spec.get("domain", "?"),
                description=spec.get("description", ""),
            )
        )
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    """Export the knowledge store to a JSON bundle (T3 unlock).

    Returns:
        0 with the output path on stdout.
    Refuses:
        Nothing — exports honor soft-delete by default.
    """
    client = _get_client(args)
    path = Path(args.output).expanduser()
    client.export_json(output_path=path, project=args.project)
    print(f"exported {path}")
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    """Import a JSON bundle into the local store (T3 unlock).

    Returns:
        0 on a clean merge; a conflict report path otherwise.
    Refuses:
        Conflicts (raises :class:`ConflictReported`).
    """
    client = _get_client(args)
    path = Path(args.input).expanduser()
    report = client.import_json(input_path=path, actor=args.actor)
    if isinstance(report, dict):
        print(json.dumps(report, indent=2))
    else:
        print("import OK")
    return EXIT_OK


def cmd_sessions_ingest(args: argparse.Namespace) -> int:
    """Ingest a single transcript file into ``session_logs`` if guards allow.

    The Stop-hook script ``bin/capture_session.sh`` shells out to this command;
    so does the cron watcher ``bin/watch_sessions.sh``. The summarizer enforces
    all nine guardrails inside :func:`runaway_context.session_summary.ingest_transcript`
    so a malfunctioning hook cannot burn token budget.

    Returns:
        ``EXIT_OK`` even when a guard fires — the skip reason is printed to
        stdout (machine-readable as JSON when ``--json`` is set). Failures
        that should NOT return 0 (transcript path missing, install dir
        misconfigured) exit non-zero.

    Refuses:
        Calling the LLM provider when ``summarizer_provider`` is ``"off"``
        (default) — the row is inserted with ``notes="metadata-only"``. All
        nine summarizer guards (lock, cooldown, idle, budget, attempt cap,
        circuit breaker, etc.) live inside ``ingest_transcript``.
    """
    from runaway_context import session_summary as ss

    cfg = _load_config(args)
    transcript = Path(args.transcript).expanduser()
    if not transcript.exists():
        _eprint(f"runaway: transcript not found: {transcript}")
        return EXIT_USAGE
    result = ss.ingest_transcript(
        cfg, transcript, force=bool(args.force),
        project_hint=getattr(args, "project", None),
    )
    payload = {
        "conversation_id": result.conversation_id,
        "inserted": result.inserted,
        "skipped_reason": result.skipped_reason,
        "used_llm": result.used_llm,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload))
    else:
        if result.inserted:
            print(
                f"inserted conversation_id={result.conversation_id} "
                f"used_llm={result.used_llm} tokens_in={result.tokens_in} "
                f"tokens_out={result.tokens_out}"
            )
        else:
            print(
                f"skipped conversation_id={result.conversation_id} "
                f"reason={result.skipped_reason}"
            )
    return EXIT_OK


def cmd_sessions_watch(args: argparse.Namespace) -> int:
    """Sweep every transcript on disk through the guarded ingester.

    Used by ``bin/watch_sessions.sh`` (cron) and for manual catch-up.
    ``--once`` is the default; ``--loop`` is reserved for a future watcher
    daemon but currently behaves the same as ``--once`` (the cron entry point
    is intentionally the canonical "loop").

    Returns:
        ``EXIT_OK`` after one sweep; prints a JSON summary.

    Refuses:
        Nothing of its own — each per-transcript ingest enforces all nine
        summarizer guards inside ``ingest_transcript`` and never propagates
        an exception back to this handler.
    """
    from runaway_context import session_summary as ss

    cfg = _load_config(args)
    search_dirs = None
    if getattr(args, "search_dir", None):
        search_dirs = [Path(d).expanduser() for d in args.search_dir]
    summary = ss.ingest_all(cfg, search_dirs=search_dirs, force=bool(args.force))
    print(json.dumps(summary))
    return EXIT_OK


def cmd_sessions_budget(args: argparse.Namespace) -> int:
    """Print today's daily-token-budget ledger.

    Returns:
        ``EXIT_OK``. Output is the JSON snapshot from
        :func:`runaway_context.budget.get_state`.

    Refuses:
        Nothing — the ledger is read-only here.
    """
    from runaway_context import budget

    cfg = _load_config(args)
    state = budget.get_state(
        Path(cfg.install_dir), cap=cfg.summarizer_daily_token_cap,
    )
    print(json.dumps(state.to_dict(), indent=2))
    return EXIT_OK


def cmd_adopt(args: argparse.Namespace) -> int:
    """Run the one-shot adoption flow: discover -> migrate/import -> ingest -> rewrite.

    Walks every step in :mod:`runaway_context.adopt` and prints a JSON report
    enumerating recommended commands (dry-run mode) or actual results
    (when ``--apply`` is set). Each step is independently idempotent — re-runs
    are safe.

    Returns:
        ``EXIT_OK`` on success regardless of per-step recommendations.
        ``EXIT_REFUSED`` (2) when ``--apply`` was requested but no target
        install exists and ``--target-install-dir`` was not provided.

    Refuses:
        Apply mode without a usable target install. Use ``runaway init`` or
        pass ``--target-install-dir`` first.
    """
    from runaway_context import adopt as _adopt

    cfg = _load_config(args)
    target = (
        Path(args.target_install_dir).expanduser()
        if args.target_install_dir else cfg.install_dir
    )
    apply = bool(args.apply)
    if apply and not (target / "knowledge.db").exists():
        _eprint(
            f"runaway: --apply refuses to run against {target} — no knowledge.db. "
            "Run `runaway init --install-dir <dir>` first, or pass "
            "--target-install-dir <existing v3 install>."
        )
        return EXIT_REFUSED

    project_roots = (
        [Path(r).expanduser() for r in args.project_root]
        if args.project_root else None
    )
    claude_root = (
        Path(args.claude_root).expanduser() if args.claude_root else None
    )
    report = _adopt.run(
        target_install_dir=target,
        project_roots=project_roots,
        project_slug=args.project,
        apply=apply,
        claude_projects_root=claude_root,
    )
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return EXIT_OK


def cmd_markdown_ingest(args: argparse.Namespace) -> int:
    """Ingest hand-edited CLAUDE.md / AGENTS.md / .cursor/rules into the KS.

    Each ``## Heading`` block becomes a ``knowledge_chunks`` row; recognized
    lesson bullets (``- LL: ...`` / ``- Rule: ...``) become
    ``lessons_learned`` rows. Original files are rewritten as pointer indexes
    after the first pass; the INGEST_MARKER makes the operation idempotent.

    Returns:
        ``EXIT_OK`` always; per-file errors are encoded in the JSON report.

    Refuses:
        Writing into system roots (``/etc/`` etc.). Re-ingesting a marked
        file without ``--force``.
    """
    from runaway_context import markdown_ingest as mi
    from runaway_context.client import Client

    cfg = _load_config(args)
    client = Client(install_dir=cfg.install_dir)
    roots = [Path(r).expanduser() for r in (args.root or [Path.home()])]
    report = mi.ingest_all(
        client, project=args.project, roots=roots,
        dry_run=bool(args.dry_run), force=bool(args.force),
    )
    out = {
        "dry_run": report.dry_run,
        "project": args.project,
        "counts": report.counts(),
        "records": [
            {
                "path": str(r.path), "action": r.action,
                "sections_inserted": r.sections_inserted,
                "lessons_inserted": r.lessons_inserted,
                "detail": r.detail,
            }
            for r in report.records
        ],
    }
    print(json.dumps(out, indent=2, default=str))
    return EXIT_OK


def cmd_multiuser_provision(args: argparse.Namespace) -> int:
    """Provision RunawayContext for every Claude user on a shared host.

    For each candidate user (UID >= ``--min-uid``, has ``~/.claude``), runs
    ``runaway doctor --fix-all --yes`` under that user's identity. Errors per
    user are encoded in the JSON report — the sweep does not abort on first
    failure.

    Returns:
        ``EXIT_OK`` always; non-zero per-user doctor returncodes appear in
        the report. The caller is expected to inspect the JSON.

    Refuses:
        Root (UID 0) target unless explicitly named via ``--user``.
    """
    from runaway_context import multiuser

    usernames = list(args.user) if args.user else None
    extra: List[str] = []
    if args.runaway_install_dir:
        extra.extend(["--install-dir", args.runaway_install_dir])
    report = multiuser.provision_all(
        usernames=usernames, dry_run=bool(args.dry_run),
        extra_doctor_args=extra, min_uid=args.min_uid,
    )
    print(json.dumps(multiuser.report_to_dict(report), indent=2, default=str))
    return EXIT_OK


def cmd_multiuser_list(args: argparse.Namespace) -> int:
    """List Claude-eligible users on this host without provisioning.

    Returns:
        ``EXIT_OK`` always; prints a JSON array.

    Refuses:
        Nothing — read-only discovery.
    """
    from runaway_context import multiuser

    profiles = multiuser.enumerate_users(min_uid=args.min_uid)
    out = [
        {
            "username": p.username, "uid": p.uid,
            "home": str(p.home), "has_claude_dir": p.has_claude_dir,
        }
        for p in profiles
    ]
    print(json.dumps(out, indent=2))
    return EXIT_OK


def cmd_memory_ingest(args: argparse.Namespace) -> int:
    """Ingest Claude Code auto-memory sibling MDs into the Knowledge Store.

    Walks ``~/.claude/projects/<slug>/memory/``, parses each non-``MEMORY.md``
    file's frontmatter, inserts into ``knowledge_chunks`` or ``lessons_learned``
    based on ``metadata.type``, and rewrites the file as a pointer stub.

    Idempotent: re-running over a tree that's already been ingested only
    touches files whose source content has reverted to non-pointer form.

    Returns:
        ``EXIT_OK`` on success (prints a JSON summary).

    Refuses:
        Per-file errors are encoded in the report; sweep continues past bad files.
    """
    from runaway_context import memory_ingest as mi
    from runaway_context.client import Client

    cfg = _load_config(args)
    client = Client(install_dir=cfg.install_dir)
    root = Path(args.claude_root).expanduser() if args.claude_root else None
    explicit_map = {}
    for entry in (args.map or []):
        if "=" not in entry:
            _eprint(f"runaway: --map entry {entry!r} must be 'dirname=slug'")
            return EXIT_USAGE
        k, _, v = entry.partition("=")
        k, v = k.strip(), v.strip()
        if not k or not v:
            _eprint(f"runaway: --map entry {entry!r} has empty dirname or slug")
            return EXIT_USAGE
        explicit_map[k] = v
    report = mi.ingest_all(
        client,
        claude_projects_root=root,
        project_filter=args.project,
        dry_run=bool(args.dry_run),
        explicit_map=explicit_map or None,
    )
    out = {
        "dry_run": report.dry_run,
        "counts": report.counts(),
        "records": [
            {
                "path": str(r.path),
                "action": r.action,
                "table": r.table,
                "row_id": r.row_id,
                "detail": r.detail,
            }
            for r in report.records
        ],
    }
    print(json.dumps(out, indent=2, default=str))
    return EXIT_OK


def cmd_import_legacy(args: argparse.Namespace) -> int:
    """Import knowledge / lessons / sessions from a legacy directory.

    The classic shape is a directory that contains a ``sessions.db`` with the
    v1 RunawayContext schema OR Steven's hand-rolled "homemade" schema (same
    table names, different columns). Both are detected and imported with
    byte-preserving body copies and timestamp preservation. Slug values that
    aren't snake_case are auto-aliased (e.g. ``runaway-knight`` →
    ``runaway_knight``) so HR-2 enforcement does not reject the import.

    Returns:
        ``EXIT_OK`` on success (prints a JSON summary), ``EXIT_USAGE`` when
        the source directory is missing or has no recognizable layout.

    Refuses:
        Importing from a source whose schema is unrecognized (returns
        ``status="unrecognized"`` from the importer rather than raising).
    """
    from runaway_context import import_legacy as imp

    cfg = _load_config(args)
    src = Path(args.from_dir).expanduser()
    if not src.exists():
        _eprint(f"runaway: source path does not exist: {src}")
        return EXIT_USAGE
    report = imp.run(
        cfg=cfg, source_dir=src, dry_run=bool(args.dry_run),
        preserve_timestamps=bool(args.preserve_timestamps),
    )
    print(json.dumps(report, indent=2, default=str))
    return EXIT_OK


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    """Launch the MCP server (stdio transport).

    Returns:
        Whatever the MCP server returns when shut down.
    Refuses:
        Invocations on installs without the MCP module wired up
        (prints the install path).
    """
    try:
        from runaway_context import mcp_server  # type: ignore
    except ImportError as e:
        _eprint("runaway: MCP server module is not yet available in this checkout.")
        _eprint(f"        ({e})")
        _eprint("        Install the optional dependency: `pip install runaway-context[mcp]`")
        _eprint("        or wait until E11 lands in src/runaway_context/mcp_server.py.")
        return EXIT_CONFIG
    cfg = _load_config(args)
    # MCP server exposes `serve_stdio` (preferred) or `serve` (legacy alias).
    serve_fn = getattr(mcp_server, "serve_stdio", None) or getattr(mcp_server, "serve", None)
    if serve_fn is None:
        _eprint("runaway: mcp_server module is present but exposes no serve entry point.")
        return EXIT_CONFIG
    serve_fn(install_dir=Path(cfg.install_dir))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the global flags every sub-parser inherits.

    Returns:
        None.
    """
    parser.add_argument(
        "--install-dir",
        default=None,
        help="Override the install directory (default: ~/_knowledge or $RC_KS_DIR).",
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with all subcommands.

    Returns:
        The configured :class:`argparse.ArgumentParser`.
    Refuses:
        Nothing — parser construction is pure.
    """
    parser = argparse.ArgumentParser(
        prog="runaway",
        description=(
            "RunawayContext — a contract-enforced living context platform "
            "for AI coding assistants. See `runaway <cmd> --help` for any "
            "subcommand."
        ),
    )
    _add_global_flags(parser)
    parser.set_defaults(_handler=None)

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # init -----------------------------------------------------------------
    p = sub.add_parser("init", help="Run the interactive install wizard (HR-15).")
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Write defaults without prompting (used by tests and CI).",
    )
    p.set_defaults(_handler=cmd_init)

    # brief ----------------------------------------------------------------
    p = sub.add_parser("brief", help="Print the canonical brief for a project.")
    p.add_argument("project", help="Project slug.")
    p.set_defaults(_handler=cmd_brief)

    # log-lesson -----------------------------------------------------------
    p = sub.add_parser("log-lesson", help="Append a new lesson-learned.")
    p.add_argument("--title", required=True)
    p.add_argument("--projects", required=True,
                   help="Comma-separated list of canonical project slugs.")
    p.add_argument("--what", default=None)
    p.add_argument("--why", default=None)
    p.add_argument("--fix", default=None)
    p.add_argument("--rule", default=None)
    p.add_argument("--severity", default=None,
                   help="Optional back-compat severity (critical|warning|info).")
    p.add_argument("--blast", type=int, default=None, help="Blast radius 1..5.")
    p.add_argument("--freq", type=int, default=None, help="Frequency 1..5.")
    p.add_argument("--rev", type=int, default=None, help="Reversibility 1..5 (high = hard).")
    p.set_defaults(_handler=cmd_log_lesson)

    # propose-knowledge ----------------------------------------------------
    p = sub.add_parser(
        "propose-knowledge",
        help="Submit a draft knowledge chunk to the approval queue.",
    )
    p.add_argument("--project", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--tags", default=None, help="Comma-separated tags.")
    p.set_defaults(_handler=cmd_propose_knowledge)

    # search ---------------------------------------------------------------
    p = sub.add_parser("search", help="Hybrid search over chunks and lessons.")
    p.add_argument("query")
    p.add_argument("--project", default=None)
    p.add_argument("--kind", choices=("chunks", "lessons", "both"), default="both")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(_handler=cmd_search)

    # list-lessons ---------------------------------------------------------
    p = sub.add_parser("list-lessons", help="List lessons with optional filters.")
    p.add_argument("--project", default=None)
    p.add_argument("--status",
                   choices=("active", "superseded", "archived"),
                   default=None)
    p.add_argument(
        "--maturity",
        choices=("scar", "active", "stable", "internalized", "superseded", "archived"),
        default=None,
    )
    p.set_defaults(_handler=cmd_list_lessons)

    # list-drafts ----------------------------------------------------------
    p = sub.add_parser("list-drafts", help="List pending drafts in the inbox.")
    p.set_defaults(_handler=cmd_list_drafts)

    # approve-draft --------------------------------------------------------
    p = sub.add_parser("approve-draft", help="Approve a pending draft.")
    p.add_argument("draft_id", type=int)
    p.add_argument("--actor", required=True, help="Name of the approving actor.")
    p.set_defaults(_handler=cmd_approve_draft)

    # reject-draft ---------------------------------------------------------
    p = sub.add_parser("reject-draft", help="Reject a pending draft.")
    p.add_argument("draft_id", type=int)
    p.add_argument("--actor", required=True)
    p.add_argument("--notes", default=None)
    p.set_defaults(_handler=cmd_reject_draft)

    # mature ---------------------------------------------------------------
    p = sub.add_parser(
        "mature",
        help="Apply a maturation transition under explicit human approval (HR-9).",
    )
    p.add_argument("lesson_id", type=int)
    p.add_argument(
        "--to", required=True,
        choices=("scar", "active", "stable", "internalized", "superseded", "archived"),
    )
    p.add_argument("--actor", required=True)
    p.add_argument("--reason", default=None)
    p.set_defaults(_handler=cmd_mature)

    # supersede ------------------------------------------------------------
    p = sub.add_parser(
        "supersede", help="Supersede an old lesson with a new one.",
    )
    p.add_argument("old_id", type=int)
    p.add_argument("new_id", type=int)
    p.add_argument("--actor", default=None)
    p.set_defaults(_handler=cmd_supersede)

    # regen-brief ----------------------------------------------------------
    p = sub.add_parser("regen-brief",
                       help="Regenerate the project brief (PRESERVE blocks preserved).")
    p.add_argument("project")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(_handler=cmd_regen_brief)

    # brief-preview --------------------------------------------------------
    p = sub.add_parser(
        "brief-preview", help="Preview what regen-brief would write, without writing.",
    )
    p.add_argument("project")
    p.set_defaults(_handler=cmd_brief_preview)

    # brief-rewrite-pointers ----------------------------------------------
    p = sub.add_parser(
        "brief-rewrite-pointers",
        help="Rewrite per-project MEMORY.md as pointer-only indexes.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--claude-root", default=None,
                   help="Override ~/.claude/projects (for tests/alt installs).")
    p.set_defaults(_handler=cmd_brief_rewrite_pointers)

    # brief-rollback -------------------------------------------------------
    p = sub.add_parser(
        "brief-rollback",
        help="Restore a project brief from brief_snapshots.",
    )
    p.add_argument("project")
    p.add_argument("--snapshot-id", type=int, default=None,
                   help="Specific snapshot id; default = most recent.")
    p.set_defaults(_handler=cmd_brief_rollback)

    # slug ----------------------------------------------------------------
    p_slug = sub.add_parser("slug", help="Slug-registry operations.")
    slug_sub = p_slug.add_subparsers(dest="slug_command", metavar="<slug_cmd>")

    p = slug_sub.add_parser("register", help="Register a canonical project slug.")
    p.add_argument("slug")
    p.add_argument("--description", default=None)
    p.set_defaults(_handler=cmd_slug_register)

    p = slug_sub.add_parser("list", help="List registered slugs.")
    p.set_defaults(_handler=cmd_slug_list)

    p = slug_sub.add_parser("alias", help="Alias one slug to a canonical one.")
    p.add_argument("alias")
    p.add_argument("canonical")
    p.set_defaults(_handler=cmd_slug_alias)

    p = slug_sub.add_parser("deprecate", help="Deprecate a slug (rows preserved).")
    p.add_argument("slug")
    p.add_argument("--reason", default=None)
    p.set_defaults(_handler=cmd_slug_deprecate)

    p = slug_sub.add_parser("merge", help="Merge one slug into another.")
    p.add_argument("from_slug", metavar="from")
    p.add_argument("to_slug", metavar="to")
    p.set_defaults(_handler=cmd_slug_merge)

    # db ------------------------------------------------------------------
    p_db = sub.add_parser("db", help="Database operations (migrate, hard-delete).")
    db_sub = p_db.add_subparsers(dest="db_command", metavar="<db_cmd>")

    p = db_sub.add_parser("migrate", help="Run the v2→v3 schema migrator (additive).")
    p.add_argument("--knowledge-db", default=None)
    p.add_argument("--sessions-db", default=None)
    p.add_argument("--metrics-db", default=None)
    p.set_defaults(_handler=cmd_db_migrate)

    p = db_sub.add_parser(
        "hard-delete",
        help="The ONLY hard-delete path (HR-3); requires both safety flags.",
    )
    p.add_argument("--table", required=True)
    p.add_argument("--id", required=True, type=int)
    p.add_argument(
        "--i-understand-this-is-permanent",
        action="store_true",
        dest="i_understand_this_is_permanent",
    )
    p.add_argument("--backup-first", action="store_true", dest="backup_first")
    p.set_defaults(_handler=cmd_db_hard_delete)

    p = db_sub.add_parser(
        "import-data-map",
        help="Register data_sources from a markdown table file.",
    )
    p.add_argument("--from", dest="from_file", required=True,
                   help="Path to the markdown file (e.g. claude_database_map.md).")
    p.add_argument("--default-kind", default="table",
                   choices=("table", "view", "endpoint", "file", "queue", "other"))
    p.add_argument("--project", default=None,
                   help="Canonical slug to stamp on every imported source.")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(_handler=cmd_db_import_data_map)

    # audit ---------------------------------------------------------------
    p_audit = sub.add_parser("audit", help="Audit-log operations (HR-7).")
    audit_sub = p_audit.add_subparsers(dest="audit_command", metavar="<audit_cmd>")

    p = audit_sub.add_parser("verify", help="Verify the audit_log hash chain.")
    p.set_defaults(_handler=cmd_audit_verify)

    # stats ---------------------------------------------------------------
    p = sub.add_parser("stats", help="Print the terminal-first stats dashboard.")
    p.set_defaults(_handler=cmd_stats)

    # doctor --------------------------------------------------------------
    p = sub.add_parser(
        "doctor",
        help="Run environment diagnostics and optionally apply install fixes.",
    )
    p.add_argument("--json", action="store_true",
                   help="Emit findings as JSON for AI consumption.")
    p.add_argument("--fix-constitution", action="store_true",
                   help="Rewrite ~/CLAUDE.md Session Memory block to v3 CLI/MCP (prompted, reversible).")
    p.add_argument("--fix-memory", action="store_true",
                   help="Rewrite MEMORY.md fetch-detail blocks to v3 commands (prompted, reversible).")
    p.add_argument("--fix-mcp", action="store_true",
                   help="Merge runaway-context into ~/.claude/mcp.json (prompted, reversible).")
    p.add_argument("--fix-hook", action="store_true",
                   help="Wire capture_session.sh into ~/.claude/settings.json (prompted, reversible).")
    p.add_argument("--fix-all", action="store_true",
                   help="Run every --fix-* in turn. Each prompted individually unless --yes.")
    p.add_argument("--yes", action="store_true",
                   help="Skip prompts. Backups are still created — revert with --revert <ts>.")
    p.add_argument("--revert", metavar="TIMESTAMP",
                   help="Restore files from the named doctor-backups batch.")
    p.add_argument("--list-reverts", action="store_true",
                   help="List available revert timestamps under ~/.runaway/doctor-backups/.")
    p.add_argument("--scan", action="store_true",
                   help="Walk common roots for knowledge.db candidates and "
                        "report each one's shape (v3/v2/v1/foreign/partial). "
                        "Use --json for AI-friendly output.")
    p.set_defaults(_handler=cmd_doctor)

    # uninstall -----------------------------------------------------------
    p = sub.add_parser(
        "uninstall",
        help="Undo a RunawayContext install: archive, optionally export to "
             "markdown, optionally revert touched files, then remove.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would happen; do not modify anything.")
    p.add_argument("--no-archive", action="store_true",
                   help="Skip the timestamped tarball snapshot.")
    p.add_argument("--archive-dir", default=None,
                   help="Where to place the snapshot tarball "
                        "(default: install-dir parent).")
    p.add_argument("--export-markdown", default=None,
                   help="Before removing, dump every lesson/chunk/brief to "
                        "this directory as portable markdown.")
    p.add_argument("--keep-db", action="store_true",
                   help="Remove only config + manifest; keep DBs / templates in place.")
    p.add_argument("--revert", action="store_true",
                   help="Restore the pre-install state of files the install modified "
                        "(reads install_manifest.json).")
    p.add_argument("--yes", action="store_true",
                   help="Confirm. Required for destructive operation "
                        "(HR-3 spirit applied to the install itself).")
    p.set_defaults(_handler=cmd_uninstall)

    # export-markdown -----------------------------------------------------
    p = sub.add_parser(
        "export-markdown",
        help="Dump every lesson/chunk/brief to a portable markdown tree. "
             "Non-destructive — DBs stay in place.",
    )
    p.add_argument("--output", required=True,
                   help="Target directory for the markdown tree.")
    p.add_argument("--overwrite", action="store_true",
                   help="Allow writing into a non-empty target.")
    p.set_defaults(_handler=cmd_export_markdown)

    # tier ----------------------------------------------------------------
    p_tier = sub.add_parser("tier", help="Tier operations (check, promote).")
    tier_sub = p_tier.add_subparsers(dest="tier_command", metavar="<tier_cmd>")

    p = tier_sub.add_parser("check", help="Print current tier and next promotion gate.")
    p.set_defaults(_handler=cmd_tier_check)

    p = tier_sub.add_parser("promote", help="Promote (or check) the install to a higher tier.")
    p.add_argument("--to", required=True, choices=("T2", "T3", "T4", "T5"))
    p.add_argument("--check", action="store_true",
                   help="Run the promotion gate test only; do not promote.")
    p.set_defaults(_handler=cmd_tier_promote)

    # drift ---------------------------------------------------------------
    p_drift = sub.add_parser("drift", help="Drift detector operations.")
    drift_sub = p_drift.add_subparsers(dest="drift_command", metavar="<drift_cmd>")

    p = drift_sub.add_parser("check", help="Run the predictive drift rules.")
    p.set_defaults(_handler=cmd_drift_check)

    # specialist ----------------------------------------------------------
    p_spec = sub.add_parser("specialist", help="Specialist-agent registry operations.")
    spec_sub = p_spec.add_subparsers(dest="specialist_command", metavar="<spec_cmd>")

    p = spec_sub.add_parser("register", help="Register a specialist agent.")
    p.add_argument("--name", required=True)
    p.add_argument("--domain", required=True)
    p.add_argument("--description", default=None)
    p.set_defaults(_handler=cmd_specialist_register)

    p = spec_sub.add_parser("list", help="List registered specialists.")
    p.set_defaults(_handler=cmd_specialist_list)

    # export / import -----------------------------------------------------
    p = sub.add_parser("export", help="Export the knowledge store to a JSON bundle.")
    p.add_argument("--output", required=True)
    p.add_argument("--project", default=None)
    p.set_defaults(_handler=cmd_export)

    p = sub.add_parser("import", help="Import a JSON bundle.")
    p.add_argument("--input", required=True)
    p.add_argument("--actor", required=True)
    p.set_defaults(_handler=cmd_import)

    # mcp -----------------------------------------------------------------
    p_mcp = sub.add_parser("mcp", help="MCP server operations.")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_command", metavar="<mcp_cmd>")

    p = mcp_sub.add_parser("serve", help="Run the MCP server over stdio transport.")
    p.set_defaults(_handler=cmd_mcp_serve)

    # sessions -------------------------------------------------------------
    p_sess = sub.add_parser(
        "sessions",
        help="Capture Claude transcripts into session_logs (guarded).",
    )
    sess_sub = p_sess.add_subparsers(dest="sessions_command", metavar="<sess_cmd>")

    p = sess_sub.add_parser(
        "ingest",
        help="Ingest a single transcript file (used by Stop hook).",
    )
    p.add_argument("--transcript", required=True, help="Path to transcript JSONL.")
    p.add_argument("--project", help="Override project_hint.")
    p.add_argument(
        "--force", action="store_true",
        help="Bypass guards (cooldown, idle, processed marker). Diagnostic use only.",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p.set_defaults(_handler=cmd_sessions_ingest)

    p = sess_sub.add_parser(
        "watch",
        help="Sweep all on-disk transcripts through the guarded ingester.",
    )
    p.add_argument(
        "--once", action="store_true", default=True,
        help="Run one sweep and exit (default).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Bypass guards. Diagnostic only.",
    )
    p.add_argument(
        "--search-dir", action="append", default=None,
        help="Override the default search root (~/.claude/projects).",
    )
    p.set_defaults(_handler=cmd_sessions_watch)

    p = sess_sub.add_parser(
        "budget", help="Print today's daily-token-budget ledger.",
    )
    p.set_defaults(_handler=cmd_sessions_budget)

    # multiuser ------------------------------------------------------------
    p_mu = sub.add_parser(
        "multiuser",
        help="Provision RunawayContext across every Claude user on a shared host.",
    )
    mu_sub = p_mu.add_subparsers(dest="multiuser_command", metavar="<mu_cmd>")

    p = mu_sub.add_parser("list", help="List Claude-eligible users.")
    p.add_argument("--min-uid", type=int, default=1000)
    p.set_defaults(_handler=cmd_multiuser_list)

    p = mu_sub.add_parser(
        "provision",
        help="Run `runaway doctor --fix-all --yes` for every eligible user.",
    )
    p.add_argument("--user", action="append", default=None,
                   help="Restrict to specific username(s); repeat the flag for multiple.")
    p.add_argument("--min-uid", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--runaway-install-dir", default=None,
                   help="Pass --install-dir <dir> to each doctor invocation.")
    p.set_defaults(_handler=cmd_multiuser_provision)

    # memory ---------------------------------------------------------------
    p_mem = sub.add_parser(
        "memory",
        help="Ingest Claude Code auto-memory MDs into the Knowledge Store.",
    )
    mem_sub = p_mem.add_subparsers(dest="memory_command", metavar="<mem_cmd>")

    p = mem_sub.add_parser(
        "ingest",
        help="Walk ~/.claude/projects/<slug>/memory/ and ingest sibling MDs.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be ingested; touch nothing.")
    p.add_argument("--project", default=None,
                   help="Only process memory dirs that map to this canonical slug.")
    p.add_argument("--claude-root", default=None,
                   help="Override ~/.claude/projects (for tests/alt installs).")
    p.add_argument("--map", action="append", default=None, metavar="DIR=SLUG",
                   help="Force a specific memory-dir name to map to a slug, "
                        "overriding the auto-detect heuristic. Repeat the flag "
                        "for multiple mappings: --map -var-www-html=parkway "
                        "--map -var-www-html-Estimating=estimate_helper")
    p.set_defaults(_handler=cmd_memory_ingest)

    # adopt ----------------------------------------------------------------
    p = sub.add_parser(
        "adopt",
        help="One-shot: discover any existing knowledge system, migrate/import "
             "everything into the KS, rewrite source files as pointers.",
    )
    p.add_argument("--target-install-dir", default=None,
                   help="Target v3 install dir; defaults to current install.")
    p.add_argument("--apply", action="store_true",
                   help="Actually run the steps. Default is dry-run / report only.")
    p.add_argument("--project", default="general",
                   help="Canonical slug attributed to hand-edited MD content "
                        "(memory-ingest auto-detects per dir; this is for the "
                        "constitution/brief sweep).")
    p.add_argument("--project-root", action="append", default=None,
                   help="Root dir to walk for CLAUDE.md/AGENTS.md/.cursor/rules. "
                        "Repeat for multiple roots. Defaults to ~.")
    p.add_argument("--claude-root", default=None,
                   help="Override ~/.claude/projects (tests / alt installs).")
    p.set_defaults(_handler=cmd_adopt)

    # markdown -------------------------------------------------------------
    p_md = sub.add_parser(
        "markdown",
        help="Ingest hand-edited CLAUDE.md / AGENTS.md / .cursor/rules.",
    )
    md_sub = p_md.add_subparsers(dest="markdown_command", metavar="<md_cmd>")

    p = md_sub.add_parser(
        "ingest",
        help="Walk root dirs, extract sections + lesson bullets, write to KS.",
    )
    p.add_argument("--project", required=True,
                   help="Canonical slug owning the ingested content.")
    p.add_argument("--root", action="append", default=None,
                   help="Project root to walk (repeatable). Defaults to ~.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Re-ingest files that already carry the INGEST_MARKER.")
    p.set_defaults(_handler=cmd_markdown_ingest)

    # import-legacy --------------------------------------------------------
    p = sub.add_parser(
        "import-legacy",
        help="One-shot importer for v1 RunawayContext OR homemade sessions.py DBs.",
    )
    p.add_argument(
        "--from", dest="from_dir", required=True,
        help="Source directory (contains sessions.db with v1 or homemade schema).",
    )
    p.add_argument("--dry-run", action="store_true", help="Report what would import.")
    p.add_argument(
        "--preserve-timestamps", action="store_true", default=True,
        help="Carry created_at / updated_at from source (default on).",
    )
    p.set_defaults(_handler=cmd_import_legacy)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for ``runaway = runaway_context.cli:main``.

    Returns:
        The process exit code (see module docstring for the schema).
    Refuses:
        Per-handler refusals exit 2 with a friendly message. Uncaught
        exceptions exit 1 with the traceback surfaced (HR-10: no silent
        failures).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    handler: Optional[Callable[[argparse.Namespace], int]] = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_OK

    try:
        return int(handler(args) or 0)
    except (
        AuditChainBroken,
        BriefBudgetExceeded,
        ConflictReported,
        HardDeleteRefused,
        InvalidProjectSlug,
        MaturationApprovalRequired,
        MigrationAborted,
        NetworkEgressBlocked,
        TierGateFailed,
        RunawayContextError,
    ) as e:
        _eprint(f"runaway: refused — {type(e).__name__}: {e}")
        return EXIT_REFUSED
    except SystemExit:
        raise
    except KeyboardInterrupt:
        _eprint("runaway: interrupted")
        return EXIT_ERROR
    except Exception:  # noqa: BLE001 — HR-10: surfaced, not swallowed
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
