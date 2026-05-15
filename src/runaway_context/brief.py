"""Brief regenerator (E20-adjacent) + HR-5 budget enforcement.

The brief is the per-project tier-3 artifact stored at
``project_context_card.md_path``. It is rebuilt from the database whenever
the card or its referenced lessons / chunks change. The PRESERVE block
(between ``<!-- PRESERVE_START -->`` and ``<!-- PRESERVE_END -->``)
survives regeneration so adopters can hand-edit a project description
without losing it on the next rebuild.

HR-5: the regenerator counts lines before writing and refuses to ship a
brief larger than the configured cap (default 150). The user gets a clear
``BriefBudgetExceeded`` instead of a silent over-cap write.

Refuses:
    Writing past the line cap (raises :class:`BriefBudgetExceeded`).
    Regenerating a project that has no ``project_context_card`` row
    (raises :class:`ValueError`).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from runaway_context._db import connect, transaction
from runaway_context.config import DEFAULT_BRIEF_LINE_CAP
from runaway_context.errors import BriefBudgetExceeded, BriefClobberRefused


PRESERVE_START = "<!-- PRESERVE_START -->"
PRESERVE_END = "<!-- PRESERVE_END -->"

BANNER_HEAD = "<!-- AUTO-GENERATED — DO NOT HAND-EDIT."
BANNER = (
    f"{BANNER_HEAD}\n"
    "This file is regenerated from knowledge.db / project_context_card.\n"
    "Edit content via the Client.log_lesson / propose_knowledge methods,\n"
    "then rebuild via Client.regen_brief(<slug>).\n"
    "The only block preserved across rebuilds is between\n"
    "PRESERVE_START / PRESERVE_END below. -->"
)


def _is_v3_brief(path: Path) -> bool:
    """True iff *path* exists and its first 256 bytes contain the v3 banner head.

    A "v3 brief" is any file the writer previously generated — identified by the
    AUTO-GENERATED banner at the top. Any other content (a hand-edited CLAUDE.md,
    a README, a Constitution, an empty file the user created intentionally) is
    treated as user-authored and the writer refuses to overwrite it.

    Refuses:
        Nothing — pure inspection.
    """
    if not path.exists():
        return False
    try:
        head = path.read_bytes()[:256].decode("utf-8", errors="replace")
    except OSError:
        return False
    return BANNER_HEAD in head


def _json_load(value: Optional[str], default: Any) -> Any:
    """Parse a JSON column or return *default* on failure / empty input."""
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return default
    if parsed is None:
        return default
    return parsed


def _extract_preserve_block(existing_md: Optional[str]) -> str:
    """Return the content between PRESERVE markers, or empty string when absent."""
    if not existing_md:
        return ""
    pattern = re.escape(PRESERVE_START) + r"(.*?)" + re.escape(PRESERVE_END)
    match = re.search(pattern, existing_md, re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def _resolve_titles(
    conn: sqlite3.Connection,
    table: str,
    ids: List[int],
) -> Dict[int, str]:
    """Bulk-fetch titles for a list of record ids in *table*.

    Returns:
        ``{id: title}`` dict. Ids without rows are absent from the result.

    Refuses:
        Querying tables outside the allowlist (``knowledge_chunks`` /
        ``lessons_learned``). Raises :class:`ValueError`.
    """
    if table not in ("knowledge_chunks", "lessons_learned"):
        raise ValueError(f"table not allowed: {table!r}")
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, title FROM {table} "
        f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
        ids,
    ).fetchall()
    return {int(r["id"]): (r["title"] or "") for r in rows}


def _section_lines(
    label: str,
    ids: List[int],
    titles: Dict[int, str],
    prefix: str,
    max_per: int,
) -> Tuple[List[str], bool]:
    """Build the lines for a single referenced-record section.

    Returns:
        ``(lines, truncated)`` — ``truncated`` is True when more ids existed
        than the section was allowed to display.

    Refuses:
        Nothing — pure formatter.
    """
    if not ids:
        return [], False
    lines: List[str] = [f"## {label} ({len(ids)})"]
    shown = ids[:max_per]
    for record_id in shown:
        title = (titles.get(int(record_id)) or "?")[:120]
        lines.append(f"- {prefix}#{record_id} — {title}")
    truncated = len(ids) > max_per
    if truncated:
        lines.append(
            f"- _... +{len(ids) - max_per} more — query via Client.list_* for the full list_"
        )
    lines.append("")
    return lines, truncated


def _compose_brief(
    conn: sqlite3.Connection,
    project: str,
    card: sqlite3.Row,
    preserved: str,
) -> Tuple[str, List[str]]:
    """Compose the brief markdown body for a single project.

    Returns:
        ``(content, truncated_sections)`` — the rendered markdown and a list
        of section labels that were trimmed.

    Refuses:
        Nothing — composition is pure given resolved data.
    """
    if not preserved:
        title = project
        preserved = (
            f"\n## {title}\n\n"
            "(One-paragraph overview of this project — what it does, who owns it, "
            "why it exists. Edit ONLY between PRESERVE_START / PRESERVE_END "
            "markers; everything else regenerates.)\n"
        )

    out: List[str] = [BANNER, "", PRESERVE_START, preserved.strip(), PRESERVE_END, ""]

    warnings = _json_load(card["top_warnings"], [])
    warning_ids: List[int] = []
    for entry in warnings:
        if isinstance(entry, dict) and "id" in entry:
            warning_ids.append(int(entry["id"]))
        elif isinstance(entry, int):
            warning_ids.append(entry)
    truncated_sections: List[str] = []
    if warning_ids:
        warning_titles = _resolve_titles(conn, "lessons_learned", warning_ids)
        out.append("## Top Warnings (read first)")
        for warning_id in warning_ids:
            title = (warning_titles.get(warning_id) or "?")[:120]
            out.append(f"- **LL#{warning_id}**: {title}")
        out.append("")

    lesson_ids = [int(x) for x in _json_load(card["active_lesson_ids"], [])]
    chunk_ids = [int(x) for x in _json_load(card["active_chunk_ids"], [])]
    lesson_titles = _resolve_titles(conn, "lessons_learned", lesson_ids)
    chunk_titles = _resolve_titles(conn, "knowledge_chunks", chunk_ids)

    section_lines, lessons_truncated = _section_lines(
        "Lessons Learned", lesson_ids, lesson_titles, "LL", 25,
    )
    out.extend(section_lines)
    if lessons_truncated:
        truncated_sections.append("Lessons Learned")

    section_lines, chunks_truncated = _section_lines(
        "Knowledge Chunks", chunk_ids, chunk_titles, "KS", 20,
    )
    out.extend(section_lines)
    if chunks_truncated:
        truncated_sections.append("Knowledge Chunks")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out.append("---")
    out.append(
        f"_Brief regenerated {now} from knowledge.db. Project slug: `{project}`._"
    )
    out.append(
        "_Drill into any pointer via Client.get_lesson / Client.get_chunk._"
    )

    return "\n".join(out), truncated_sections


def snapshot_before_write(
    install_dir: Path,
    project: str,
    md_path: Path,
) -> Optional[int]:
    """Snapshot the current brief contents into ``brief_snapshots`` before overwrite.

    Returns:
        The new ``brief_snapshots.id``, or None when there is no existing
        file to snapshot.

    Refuses:
        Snapshotting a missing or unreadable file (returns None instead of
        raising — the caller still gets a clean slate to write to).
    """
    md_path = Path(md_path)
    if not md_path.exists():
        return None
    try:
        content = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    line_count = len(content.splitlines())

    knowledge_db = Path(install_dir) / "knowledge.db"
    conn = connect(knowledge_db)
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO brief_snapshots "
                "(project, md_path, content, line_count, saved_at, note) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
                (project, str(md_path), content, line_count, "pre-regen snapshot"),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def regenerate(
    install_dir: Path,
    project: str,
    dry_run: bool = False,
    cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Rebuild a project's brief from ``project_context_card``.

    Workflow:
        1. Read the card for *project*.
        2. Resolve referenced lesson / chunk titles.
        3. Compose markdown with the PRESERVE block carried over.
        4. Count lines. If over *cap*, raise :class:`BriefBudgetExceeded`.
        5. Snapshot the prior md (if any), then write the new content
           (skipped when ``dry_run`` is True).

    Returns:
        ``{content, line_count, written, md_path, truncated_sections,
        snapshot_id}``.

    Raises:
        BriefBudgetExceeded: when the brief would exceed *cap* lines (HR-5).
        BriefClobberRefused: when ``md_path`` exists and is not a v3-generated
            brief (HR-5 no-clobber).
        ValueError: when no ``project_context_card`` exists for *project*.

    Refuses:
        Writing past the configured cap. The cap defaults to
        :data:`DEFAULT_BRIEF_LINE_CAP` (150). Pass ``cap=N`` to override.

        Overwriting a target that lacks the v3 AUTO-GENERATED banner — protects
        user-authored CLAUDE.md / README / Constitution files from being
        clobbered when ``project_context_card.md_path`` happens to point at them.
    """
    install_dir = Path(install_dir)
    knowledge_db = install_dir / "knowledge.db"
    conn = connect(knowledge_db)
    try:
        card = conn.execute(
            "SELECT * FROM project_context_card WHERE project = ?",
            (project,),
        ).fetchone()
        if card is None:
            raise ValueError(
                f"no project_context_card for project={project!r}"
            )

        effective_cap = cap if cap is not None else (card["md_line_cap"] or DEFAULT_BRIEF_LINE_CAP)
        md_path_value = card["md_path"]
        md_path = Path(md_path_value) if md_path_value else None

        preserved = ""
        if md_path and md_path.exists():
            try:
                preserved = _extract_preserve_block(md_path.read_text(encoding="utf-8"))
            except OSError:
                preserved = ""

        content, truncated_sections = _compose_brief(conn, project, card, preserved)
        line_count = len(content.splitlines())

        if line_count > effective_cap:
            prune_hint = (
                "Brief exceeds tier budget: produced "
                f"{line_count} lines but cap is {effective_cap}. "
                "Reduce active_lesson_ids / active_chunk_ids in the project "
                "context card, or move stale lessons to maturity='internalized' "
                "so they drop from the brief but stay queryable (HR-5)."
            )
            raise BriefBudgetExceeded(prune_hint)

        snapshot_id: Optional[int] = None
        written = False
        if not dry_run:
            if md_path is None:
                raise ValueError(
                    f"project {project!r} has no md_path set on its context card"
                )
            if md_path.exists() and not _is_v3_brief(md_path):
                raise BriefClobberRefused(
                    f"refusing to overwrite {md_path}: file exists and is not a "
                    "v3-generated brief (no AUTO-GENERATED banner found in the "
                    "first 256 bytes). Retarget project_context_card.md_path at "
                    "a sibling file such as CLAUDE_BRIEF.md so the brief lives "
                    "alongside the user's content (HR-5 no-clobber)."
                )
            snapshot_id = snapshot_before_write(install_dir, project, md_path)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(content, encoding="utf-8")
            with transaction(conn):
                conn.execute(
                    "UPDATE project_context_card SET last_rebuilt = CURRENT_TIMESTAMP "
                    "WHERE project = ?",
                    (project,),
                )
            written = True

        return {
            "content": content,
            "line_count": line_count,
            "written": written,
            "md_path": str(md_path) if md_path else None,
            "truncated_sections": truncated_sections,
            "snapshot_id": snapshot_id,
            "cap": effective_cap,
        }
    finally:
        conn.close()
