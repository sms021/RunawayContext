"""Memory MD ingester — pulls Claude Code auto-memory sibling files into the KS.

Background
----------
Claude Code's auto-memory subsystem writes one MD file per memory under
``~/.claude/projects/<slug>/memory/`` with YAML frontmatter::

    ---
    name: short-kebab-case-slug
    description: one-line summary
    metadata:
      type: user|feedback|project|reference
    ---
    body...

Before v3.2.0 these siblings survived v2->v3 migration as on-disk orphans —
``MEMORY.md`` was scanned, but its siblings (``feedback_*.md`` / ``project_*.md``
/ ``reference_*.md`` / ``user_*.md``) never entered ``knowledge_chunks`` or
``lessons_learned``. The doctor's ``check_memory_md_stale_paths`` saw nothing.
Search returned nothing. A single user's machine carried 31 such orphans
post-migration (concrete repro: 2026-05-17).

This module closes that gap. ``ingest_all()`` walks every per-project memory
directory, parses each sibling MD, inserts it into the right table (feedback
-> ``lessons_learned``, others -> ``knowledge_chunks``), and rewrites the MD on
disk as a *pointer stub*: same frontmatter slug + description, but with
``metadata.type = 'pointer'`` and ``db_table`` / ``db_row_id`` keys. The body is
collapsed to a single line referencing the DB row.

Idempotency
-----------
Re-running is safe. A pointer-shaped MD is skipped. A non-pointer MD whose
(project, title) already matches a row in the target table is *linked* — the
existing row's ``source`` is left untouched (HR-3 / HR-9 — never silently
overwrite) but the MD is still rewritten as a pointer to that row.

Refuses
-------
- Frontmatter that fails to parse -> ERROR record in the report, file untouched.
- Empty bodies after stripping frontmatter -> SKIPPED (no useful payload).
- Per-project subdir with no canonical slug match in ``slug_registry`` ->
  ERROR record (HR-2 — every write needs a valid slug).

Public surface
--------------
- :func:`discover_memory_dirs` - find every per-project memory dir
- :func:`discover_memory_mds` - find sibling MDs (excludes ``MEMORY.md``)
- :func:`parse_memory_md` - parse one file into a dict
- :func:`ingest_one` - ingest one file, return the result
- :func:`ingest_all` - run a full sweep, return an :class:`IngestReport`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Maps frontmatter metadata.type -> destination table.
# 'feedback' and 'scar' represent "things that burned us / discipline rules" —
# both land in lessons_learned. Everything else is reference material, which
# lands in knowledge_chunks. Unknown types default to knowledge_chunks (the
# safer choice: knowledge is queryable but not weighted into brief warnings).
TYPE_TO_TABLE: Dict[str, str] = {
    "feedback": "lessons_learned",
    "scar": "lessons_learned",
    "rule": "lessons_learned",
    "user": "knowledge_chunks",
    "project": "knowledge_chunks",
    "reference": "knowledge_chunks",
    "term": "knowledge_chunks",
    "metric": "knowledge_chunks",
    "gotcha": "knowledge_chunks",
}


@dataclass
class IngestRecord:
    """Result of processing one MD file."""

    path: Path
    action: str  # 'inserted', 'linked', 'skipped', 'error', 'already_pointer'
    table: Optional[str] = None
    row_id: Optional[int] = None
    detail: Optional[str] = None


@dataclass
class IngestReport:
    """Aggregate result of an :func:`ingest_all` sweep."""

    records: List[IngestRecord] = field(default_factory=list)
    dry_run: bool = False

    def counts(self) -> Dict[str, int]:
        """Return a dict ``{action: count}`` summary."""
        out: Dict[str, int] = {}
        for r in self.records:
            out[r.action] = out.get(r.action, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_memory_dirs(claude_projects_root: Optional[Path] = None) -> List[Path]:
    """Return every per-project ``memory/`` directory under Claude Code's tree.

    Args:
        claude_projects_root: Override the default ``~/.claude/projects``. Used
            for tests and for hosts whose Claude home is non-standard.

    Returns:
        List of ``memory/`` directories that actually exist (may be empty).

    Refuses:
        Nothing - read-only discovery, never raises on missing paths.
    """
    root = Path(claude_projects_root) if claude_projects_root else (
        Path.home() / ".claude" / "projects"
    )
    if not root.exists():
        return []
    out: List[Path] = []
    for memdir in root.rglob("memory"):
        if memdir.is_dir():
            out.append(memdir)
    return sorted(out)


def discover_memory_mds(memdir: Path) -> List[Path]:
    """Return MD files in *memdir* excluding ``MEMORY.md`` (the index).

    Refuses:
        Nothing - returns ``[]`` for non-existent / unreadable dirs.
    """
    if not memdir.exists():
        return []
    out = []
    for p in sorted(memdir.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)


def _parse_frontmatter_yaml(fm: str) -> Dict[str, Any]:
    """Hand-rolled tiny YAML reader for the subset auto-memory writes.

    Auto-memory writes a fixed shape: top-level scalar keys (``name``,
    ``description``) plus a ``metadata:`` block with scalar children. We do not
    pull in PyYAML; this avoids a runtime dependency for a one-off importer.

    Refuses:
        Lists, anchors, multi-document streams, or anything past the limited
        shape above - raises :class:`ValueError`.
    """
    out: Dict[str, Any] = {}
    cur_block: Optional[Tuple[str, Dict[str, Any]]] = None
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Detect dedented top-level key
        if not line.startswith(" "):
            if ":" not in line:
                raise ValueError(f"malformed frontmatter line: {line!r}")
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                cur_block = (key, {})
                out[key] = cur_block[1]
            else:
                out[key] = _coerce_scalar(val)
                cur_block = None
        else:
            # Indented child of the active block
            if cur_block is None:
                raise ValueError(f"unexpected indented line (no parent): {line!r}")
            stripped = line.strip()
            if ":" not in stripped:
                raise ValueError(f"malformed nested line: {line!r}")
            key, _, val = stripped.partition(":")
            cur_block[1][key.strip()] = _coerce_scalar(val.strip())
    return out


def _coerce_scalar(val: str) -> Any:
    """Strip surrounding quotes and coerce true/false/int."""
    if val.startswith(("'", '"')) and val.endswith(val[0]) and len(val) >= 2:
        return val[1:-1]
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    if val.isdigit():
        return int(val)
    return val


def parse_memory_md(path: Path) -> Dict[str, Any]:
    """Parse a memory MD into ``{frontmatter, body, raw}``.

    Raises:
        ValueError: when the file has no frontmatter or the frontmatter is
            malformed.
    """
    text = path.read_text()
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"no frontmatter in {path}")
    fm = _parse_frontmatter_yaml(m.group("fm"))
    body = m.group("body").strip()
    return {"frontmatter": fm, "body": body, "raw": text}


def _memory_type(fm: Dict[str, Any]) -> str:
    md = fm.get("metadata") or {}
    if isinstance(md, dict):
        return str(md.get("type") or "").strip().lower()
    return ""


# ---------------------------------------------------------------------------
# Pointer stub writer
# ---------------------------------------------------------------------------


def rewrite_as_pointer(
    path: Path,
    *,
    table: str,
    row_id: int,
    name: str,
    description: str,
) -> None:
    """Rewrite *path* as a pointer stub with frontmatter only.

    Refuses:
        Nothing - the caller is responsible for already having a committed
        row in the DB before this is called (failure to write the file leaves
        an orphan-DB row, which is recoverable on next sweep).
    """
    text = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "metadata:\n"
        "  type: pointer\n"
        f"  db_table: {table}\n"
        f"  db_row_id: {row_id}\n"
        "---\n"
        f"See knowledge.db `{table}` row {row_id}.\n"
    )
    path.write_text(text)


# ---------------------------------------------------------------------------
# Ingest one
# ---------------------------------------------------------------------------


def _resolve_project_slug(memdir: Path, client) -> Optional[str]:
    """Pick the canonical slug for *memdir*.

    The per-project memory dir lives at ``~/.claude/projects/<dash-name>/memory``
    where ``<dash-name>`` is Claude Code's munged form of an absolute path
    (e.g. ``-var-www-html``). We map that back to a canonical slug by:

    1. Checking ``slug_registry`` for an exact match on the dir name.
    2. Falling back to the rightmost path segment (``html`` -> try registry).
    3. Returning ``None`` if no match - the caller emits an ERROR record.
    """
    name = memdir.parent.name
    try:
        from runaway_context._slugs import canonicalize_slug
    except ImportError:
        canonicalize_slug = lambda s: s  # noqa: E731

    candidates = [
        name,
        name.lstrip("-").replace("-", "_"),
        name.split("-")[-1] if "-" in name else None,
    ]
    candidates = [c for c in candidates if c]
    # Probe slug_registry directly via the client connection.
    import sqlite3
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        for cand in candidates:
            row = conn.execute(
                "SELECT slug FROM slug_registry WHERE slug = ? AND status = 'active'",
                (canonicalize_slug(cand),),
            ).fetchone()
            if row:
                return row[0]
    finally:
        conn.close()
    return None


def _find_existing_row(client, table: str, project: str, title: str) -> Optional[int]:
    """Return an existing row id matching (project, title), or None."""
    import sqlite3
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        if table == "lessons_learned":
            row = conn.execute(
                "SELECT id FROM lessons_learned WHERE project = ? AND title = ? "
                "AND (deleted_at IS NULL) LIMIT 1",
                (project, title),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM knowledge_chunks WHERE project = ? AND title = ? "
                "AND (deleted_at IS NULL) LIMIT 1",
                (project, title),
            ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def ingest_one(path: Path, client, *, dry_run: bool = False) -> IngestRecord:
    """Ingest one memory MD into the DB and rewrite it as a pointer.

    Returns:
        :class:`IngestRecord` describing what happened. The function does NOT
        raise on per-file errors - errors are encoded in the record so a
        sweep can continue past one bad file.
    """
    try:
        parsed = parse_memory_md(path)
    except (OSError, ValueError) as exc:
        return IngestRecord(path=path, action="error", detail=f"parse: {exc}")

    fm = parsed["frontmatter"]
    body = parsed["body"]
    mtype = _memory_type(fm)
    if mtype == "pointer":
        return IngestRecord(
            path=path, action="already_pointer",
            table=(fm.get("metadata") or {}).get("db_table"),
            row_id=(fm.get("metadata") or {}).get("db_row_id"),
        )
    if not body:
        return IngestRecord(path=path, action="skipped", detail="empty body")

    name = str(fm.get("name") or path.stem).strip()
    description = str(fm.get("description") or name).strip()
    table = TYPE_TO_TABLE.get(mtype, "knowledge_chunks")

    project = _resolve_project_slug(path.parent, client)
    if not project:
        return IngestRecord(
            path=path, action="error",
            detail=f"no canonical slug for memdir {path.parent.parent.name!r}",
        )

    source = f"memory:{path}"

    existing = _find_existing_row(client, table, project, description)
    if existing is not None:
        if not dry_run:
            rewrite_as_pointer(
                path, table=table, row_id=existing,
                name=name, description=description,
            )
        return IngestRecord(
            path=path, action="linked", table=table, row_id=existing,
            detail="matched existing (project, title)",
        )

    if dry_run:
        return IngestRecord(path=path, action="inserted", table=table, row_id=None)

    if table == "lessons_learned":
        row_id = client.log_lesson(
            title=description,
            project_tags=[project],
            prevention_rule=body,
            severity="warning",
            source=source,
        )
    else:
        row_id = client.propose_knowledge(
            project=project,
            topic=name,
            title=description,
            body=body,
            source=source,
        )

    rewrite_as_pointer(
        path, table=table, row_id=row_id, name=name, description=description,
    )
    return IngestRecord(path=path, action="inserted", table=table, row_id=row_id)


# ---------------------------------------------------------------------------
# Full sweep
# ---------------------------------------------------------------------------


def ingest_all(
    client,
    *,
    claude_projects_root: Optional[Path] = None,
    project_filter: Optional[str] = None,
    dry_run: bool = False,
) -> IngestReport:
    """Walk every per-project memory dir and ingest each sibling MD.

    Args:
        client: an instantiated :class:`runaway_context.client.Client`.
        claude_projects_root: override ``~/.claude/projects`` (for tests).
        project_filter: only process memory dirs whose canonical slug matches.
        dry_run: report what would happen without writing to DB or disk.

    Returns:
        :class:`IngestReport` aggregating one record per file processed.
    """
    report = IngestReport(dry_run=dry_run)
    for memdir in discover_memory_dirs(claude_projects_root):
        if project_filter is not None:
            slug = _resolve_project_slug(memdir, client)
            if slug != project_filter:
                continue
        for md_path in discover_memory_mds(memdir):
            rec = ingest_one(md_path, client, dry_run=dry_run)
            report.records.append(rec)
    return report
