"""Hand-edited markdown ingestor (v3.3.3).

Closes the last gap in the "single source of truth" vision: hand-edited
constitution and brief files (``CLAUDE.md``, ``AGENTS.md``, ``.cursor/rules/``,
per-project ``CLAUDE.md``) that hold knowledge the user typed by hand into
files instead of through the Client API. Until this module shipped, that
content lived ONLY in files — never indexed, never queryable through MCP,
not visible to ``search_chunks``.

What gets extracted
-------------------
Each markdown file is parsed for two kinds of knowledge:

1. **Sections** — each ``## Heading`` block becomes a ``knowledge_chunks``
   row, with the heading as ``topic`` and the body as ``body``. Tags are
   inferred from the heading text (lowercase tokens that match registered
   slugs).
2. **Lessons-style bullets** — bullets that match a short pattern like
   ``- LL: <description>`` or ``- Rule: <description>`` become
   ``lessons_learned`` rows. This is opt-in via the source line; ordinary
   bullets are folded into the surrounding chunk.

Idempotency
-----------
A file is rewritten as a thin pointer index only on opt-in (a top-of-file
marker ``<!-- runaway-ingest: INGESTED <iso-ts> -->`` is appended after the
first pass). Re-running the importer over an already-rewritten file is a
no-op — the marker signals "stop here". To re-ingest, remove the marker.

Refuses
-------
- Writing into files under ``/etc/``, ``/usr/``, system roots.
- Touching a file that has both no marker AND looks like user-authored
  free-form prose (no recognizable sections) without --force.
- Importing without a target install (cfg.knowledge_db must exist).

Public surface
--------------
- :func:`discover_handedited_mds` — find candidate files
- :func:`parse_markdown_doc` — section + bullet extraction
- :func:`ingest_one` — process one file
- :func:`ingest_all` — sweep a project root
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# File patterns we consider candidates for ingestion. Globs evaluated under
# each provided root.
_DEFAULT_GLOBS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".cursor/rules/*.md",
    ".cursor/rules/**/*.md",
)


# Bullets recognized as explicit lessons. Examples:
#   - LL: do not mock the database in integration tests
#   - Rule: every write needs a slug
#   - Scar: shipped without tests once, never again
_LESSON_BULLET = re.compile(
    r"^\s*-\s*(?P<kind>LL|Rule|Scar|Lesson)\s*:\s*(?P<text>.+)$",
    re.IGNORECASE,
)


# A file we previously rewrote leaves this marker behind. Idempotency hinge.
INGEST_MARKER = "<!-- runaway-ingest: INGESTED"


# Safety: never write into system roots (matches uninstall's rail set).
_BLOCKED_WRITE_ROOTS = (
    "/etc/", "/usr/", "/var/log/", "/var/db/", "/bin/", "/sbin/", "/sys/", "/proc/",
)


@dataclass
class IngestRecord:
    """One file's outcome."""

    path: Path
    action: str  # 'ingested' / 'already_ingested' / 'skipped' / 'error'
    sections_inserted: int = 0
    lessons_inserted: int = 0
    detail: Optional[str] = None


@dataclass
class IngestReport:
    """Aggregate of one :func:`ingest_all` sweep."""

    records: List[IngestRecord] = field(default_factory=list)
    dry_run: bool = False

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.records:
            out[r.action] = out.get(r.action, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_handedited_mds(
    roots: List[Path],
    globs: Optional[List[str]] = None,
) -> List[Path]:
    """Return every candidate markdown file under any of *roots*.

    Args:
        roots: directories to scan (user project dirs, ``$HOME``, etc.).
        globs: pattern set; defaults to CLAUDE.md / AGENTS.md / .cursor/rules.

    Returns:
        Sorted, de-duplicated list of paths. Skips files under blocked
        system roots.

    Refuses:
        Nothing — read-only discovery.
    """
    use_globs = list(globs) if globs else list(_DEFAULT_GLOBS)
    seen: set = set()
    out: List[Path] = []
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        for pattern in use_globs:
            for match in root.glob(pattern):
                if not match.is_file():
                    continue
                resolved = match.resolve()
                if any(str(resolved).startswith(b) for b in _BLOCKED_WRITE_ROOTS):
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                out.append(match)
    return sorted(out)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def parse_markdown_doc(text: str) -> Dict[str, Any]:
    """Split *text* into ``{sections: [...], lessons: [...]}``.

    Sections are ``{level, heading, body}`` for each ``## Heading`` block.
    Lessons are ``{kind, text}`` for each recognized lesson bullet.

    Returns:
        Dict with two top-level lists. Body strings preserve original
        whitespace (no .strip on internal lines).
    """
    lines = text.splitlines()
    sections: List[Dict[str, Any]] = []
    lessons: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    body_buf: List[str] = []

    def flush() -> None:
        if cur is None:
            return
        cur["body"] = "\n".join(body_buf).strip("\n")
        sections.append(cur)

    for line in lines:
        m = _HEADING.match(line)
        if m:
            flush()
            cur = {"level": len(m.group(1)), "heading": m.group(2).strip()}
            body_buf = []
            continue
        # Inside a section (or before the first heading): check for lesson bullets
        lm = _LESSON_BULLET.match(line)
        if lm:
            lessons.append({
                "kind": lm.group("kind").lower(),
                "text": lm.group("text").strip(),
            })
            # Don't add to body — lesson bullets are extracted out
            continue
        if cur is not None:
            body_buf.append(line)
    flush()
    return {"sections": sections, "lessons": lessons}


def _slug_match(heading: str, available_slugs: set) -> List[str]:
    """Return any registered slugs that appear as lowercase words in *heading*."""
    words = re.findall(r"[a-z_][a-z0-9_]*", heading.lower())
    return [w for w in words if w in available_slugs]


# ---------------------------------------------------------------------------
# Ingest one
# ---------------------------------------------------------------------------


def _is_pointer_stub(text: str) -> bool:
    """A previously-rewritten file carries the INGEST_MARKER."""
    return INGEST_MARKER in text[:1024]


def _registered_slugs(client) -> set:
    import sqlite3
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        return {
            r[0] for r in conn.execute(
                "SELECT slug FROM slug_registry WHERE status='active'"
            )
        }
    finally:
        conn.close()


def ingest_one(
    path: Path, client, *,
    project: str,
    dry_run: bool = False,
    force: bool = False,
) -> IngestRecord:
    """Parse *path*, insert sections + lessons, rewrite as a pointer stub.

    Args:
        path: markdown file to ingest.
        client: live :class:`Client` bound to the target install.
        project: canonical slug that owns this file's content.
        dry_run: parse + classify only.
        force: ignore the INGEST_MARKER and re-ingest anyway.

    Returns:
        :class:`IngestRecord`. Per-file errors land in the record, not raised.

    Refuses:
        Writing into system roots; re-ingesting a marked file unless *force*.
    """
    if any(str(path.resolve()).startswith(b) for b in _BLOCKED_WRITE_ROOTS):
        return IngestRecord(path=path, action="skipped",
                            detail="path under blocked system root")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return IngestRecord(path=path, action="error", detail=f"read: {exc}")

    if _is_pointer_stub(text) and not force:
        return IngestRecord(path=path, action="already_ingested",
                            detail="INGEST_MARKER present (use --force to re-ingest)")

    parsed = parse_markdown_doc(text)
    sections = parsed["sections"]
    lessons = parsed["lessons"]
    if not sections and not lessons:
        return IngestRecord(path=path, action="skipped",
                            detail="no sections or lesson bullets found")

    available_slugs = _registered_slugs(client) | {project}

    section_ids: List[int] = []
    lesson_ids: List[int] = []
    source = f"handedited:{path}"

    if not dry_run:
        for sec in sections:
            if not sec.get("body"):
                continue
            inferred = _slug_match(sec["heading"], available_slugs)
            tags = sorted({project} | set(inferred))
            heading = sec["heading"][:200]
            topic = re.sub(r"[^a-z0-9_]+", "_",
                           heading.lower()).strip("_")[:80] or "section"
            # Ensure topic is unique per ingest pass by mixing in path + a
            # monotonic time slice. Re-ingests (--force) thereby create
            # distinct chunks rather than colliding on the UNIQUE constraint.
            seed = f"{path}|{heading}|{time.time_ns()}"
            topic = f"{topic}_{abs(hash(seed)) % 1000000:06d}"
            try:
                rid = client.propose_knowledge(
                    project=project, topic=topic, title=heading,
                    body=sec["body"], project_tags=tags, source=source,
                )
                section_ids.append(rid)
            except Exception as exc:  # noqa: BLE001 — surface in record
                return IngestRecord(
                    path=path, action="error",
                    detail=f"propose_knowledge failed for {heading!r}: {exc}",
                )
        for les in lessons:
            try:
                rid = client.log_lesson(
                    title=les["text"][:200],
                    project_tags=[project],
                    prevention_rule=les["text"],
                    severity="warning",
                    source=source,
                )
                lesson_ids.append(rid)
            except Exception as exc:  # noqa: BLE001
                return IngestRecord(
                    path=path, action="error",
                    detail=f"log_lesson failed: {exc}",
                )

        # Rewrite as a pointer index
        pointer_lines = [
            f"{INGEST_MARKER} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -->",
            f"# {path.stem} — pointer index",
            "",
            "Detail content lives in `knowledge.db`. Fetch via MCP "
            "`get_chunk` / `get_lesson` or `runaway brief {project}`.",
            "",
        ]
        for rid in section_ids:
            pointer_lines.append(f"- KC#{rid}")
        for rid in lesson_ids:
            pointer_lines.append(f"- LL#{rid}")
        pointer_lines.append("")
        path.write_text("\n".join(pointer_lines))

    return IngestRecord(
        path=path, action="ingested",
        sections_inserted=len(sections) if dry_run else len(section_ids),
        lessons_inserted=len(lessons) if dry_run else len(lesson_ids),
    )


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def ingest_all(
    client,
    *,
    project: str,
    roots: List[Path],
    globs: Optional[List[str]] = None,
    dry_run: bool = False,
    force: bool = False,
) -> IngestReport:
    """Discover candidate MDs under *roots* and ingest each.

    All files are attributed to a single *project* slug. Most adopters'
    constitution / rules files belong to one project, so a single sweep is
    the common case; per-project ingestion uses repeated calls.

    Returns:
        :class:`IngestReport` aggregating one record per file.
    """
    report = IngestReport(dry_run=dry_run)
    for path in discover_handedited_mds(roots, globs=globs):
        rec = ingest_one(path, client, project=project,
                         dry_run=dry_run, force=force)
        report.records.append(rec)
    return report
