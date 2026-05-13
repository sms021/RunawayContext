"""Multi-project session stacking (E14).

A user may work in multiple projects in a single session. The
:class:`ProjectStack` keeps a bounded LRU stack of active project slugs;
``current()`` returns the top, ``push()`` brings a slug to the top (moving
it if it was already present), ``pop()`` discards the top.

``compose_brief`` concatenates the per-project briefs while respecting
the global tier line cap (HR-5). Top warnings are emitted first, then
each project's body is appended only as long as the budget allows.

Refuses:
    Stack depths above the configured maximum (oldest entries are evicted
    instead). Composing a brief that exceeds the line cap (the writer
    elides additional projects rather than overflow).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from runaway_context._db import connect
from runaway_context.brief import PRESERVE_END, PRESERVE_START
from runaway_context.config import DEFAULT_T1_LINE_CAP


DEFAULT_STACK_DEPTH = 3


@dataclass
class ProjectStack:
    """Bounded LRU stack of active project slugs.

    The most recently pushed slug is at the end of :attr:`stack`. When
    :attr:`max_depth` is reached an additional push evicts the oldest
    entry (the front of the list). Pushing a slug already in the stack
    moves it to the top instead of creating a duplicate.

    Returns:
        Methods return slug strings or ``None``. ``stack()`` returns a copy
        of the underlying list so callers can iterate without aliasing.

    Refuses:
        ``max_depth`` < 1 — raises :class:`ValueError` in
        :meth:`__post_init__`. Empty / whitespace slugs in :meth:`push`.
    """

    max_depth: int = DEFAULT_STACK_DEPTH
    _entries: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate constructor inputs.

        Refuses:
            Non-positive ``max_depth`` (raises :class:`ValueError`).
        """
        if int(self.max_depth) < 1:
            raise ValueError("max_depth must be >= 1")
        self.max_depth = int(self.max_depth)

    def push(self, project: str) -> None:
        """Push *project* onto the stack, evicting the oldest if at capacity.

        If *project* is already on the stack it is moved to the top —
        no duplicate is created.

        Refuses:
            Empty or non-string slugs (raises :class:`ValueError`).
        """
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be a non-empty string")
        slug = project.strip()
        # Move-to-top behavior
        if slug in self._entries:
            self._entries.remove(slug)
        self._entries.append(slug)
        # Evict from the bottom (oldest)
        while len(self._entries) > self.max_depth:
            self._entries.pop(0)

    def pop(self) -> Optional[str]:
        """Remove and return the top of the stack.

        Returns:
            The slug that was on top, or ``None`` when the stack is empty.

        Refuses:
            Nothing — empty stack returns ``None`` instead of raising.
        """
        if not self._entries:
            return None
        return self._entries.pop()

    def current(self) -> Optional[str]:
        """Return the slug currently on top without modifying the stack.

        Returns:
            The top slug or ``None`` when the stack is empty.

        Refuses:
            Nothing — empty stack returns ``None``.
        """
        if not self._entries:
            return None
        return self._entries[-1]

    def stack(self) -> List[str]:
        """Return a copy of the underlying slug list, oldest first.

        Returns:
            ``list[str]`` — modifications to the returned list do not
            affect this :class:`ProjectStack`.
        """
        return list(self._entries)

    def __len__(self) -> int:
        """Return the number of active projects on the stack."""
        return len(self._entries)


def switch(stack: ProjectStack, to: str) -> None:
    """Implement ``/runaway:project <slug>`` switch behavior.

    A switch is equivalent to a :meth:`ProjectStack.push` — the
    destination becomes the current project, prior projects retain their
    relative order (without duplication).

    Refuses:
        Empty *to* slugs (delegated to :meth:`ProjectStack.push`).
    """
    if not isinstance(stack, ProjectStack):
        raise TypeError("stack must be a ProjectStack")
    stack.push(to)


def _load_card(conn: sqlite3.Connection, project: str) -> Optional[sqlite3.Row]:
    """Fetch the ``project_context_card`` row for *project*.

    Returns:
        The row, or ``None`` if no card exists.
    """
    return conn.execute(
        "SELECT * FROM project_context_card WHERE project = ?",
        (project,),
    ).fetchone()


def _read_brief_lines(md_path: Optional[Path]) -> List[str]:
    """Read a brief file from disk and return its lines.

    Returns:
        The split lines, or ``[]`` when *md_path* is missing or
        unreadable (tolerant by design — compose_brief surfaces the gap
        with a placeholder line so the caller still sees the project).
    """
    if md_path is None:
        return []
    try:
        text = Path(md_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return text.splitlines()


_PRESERVE_RE = re.compile(
    re.escape(PRESERVE_START) + r"(.*?)" + re.escape(PRESERVE_END),
    re.DOTALL,
)


def _extract_preserve(lines: List[str]) -> str:
    """Return the PRESERVE block content from a brief's lines (empty if absent)."""
    if not lines:
        return ""
    text = "\n".join(lines)
    match = _PRESERVE_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def _resolve_warning_titles(
    conn: sqlite3.Connection, ids: List[int]
) -> Dict[int, str]:
    """Bulk-fetch lesson titles for the given ids (active rows only)."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, title FROM lessons_learned "
        f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
        ids,
    ).fetchall()
    return {int(r["id"]): (r["title"] or "?") for r in rows}


def _warning_block(
    conn: sqlite3.Connection, project: str, card: sqlite3.Row
) -> List[str]:
    """Render the project's "Top Warnings" section as a list of markdown lines.

    Returns:
        Markdown lines, or ``[]`` when the card has no warnings, the
        ``top_warnings`` JSON cannot be decoded, or the column is absent.
        Tolerance is intentional: a missing column or malformed payload
        should not block the rest of the brief (HR-5 budget composition).
    """
    try:
        raw = card["top_warnings"]
    except (KeyError, IndexError):
        return []
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return []
    ids: List[int] = []
    for entry in entries:
        if isinstance(entry, dict) and "id" in entry:
            ids.append(int(entry["id"]))
        elif isinstance(entry, int):
            ids.append(entry)
    if not ids:
        return []
    titles = _resolve_warning_titles(conn, ids)
    out: List[str] = [f"### Top Warnings — {project}"]
    for warning_id in ids:
        title = titles.get(warning_id, "?")[:120]
        out.append(f"- **LL#{warning_id}** ({project}): {title}")
    out.append("")
    return out


def _body_block(project: str, lines: List[str]) -> List[str]:
    """Render a section heading + the brief body (minus PRESERVE markers)."""
    if not lines:
        return [f"### {project}", "_(no brief on disk — run regenerate first)_", ""]
    text = "\n".join(lines)
    # Strip PRESERVE markers themselves so they don't appear twice when stacked.
    text = _PRESERVE_RE.sub(lambda m: m.group(1).strip(), text)
    # Drop AUTO-GENERATED banner if present at the head.
    body_lines = text.splitlines()
    cleaned: List[str] = []
    skipping_banner = False
    for line in body_lines:
        if line.startswith("<!-- AUTO-GENERATED"):
            skipping_banner = True
            continue
        if skipping_banner:
            if line.endswith("-->"):
                skipping_banner = False
            continue
        cleaned.append(line)
    return [f"### {project}", *cleaned, ""]


def compose_brief(
    install_dir: Path,
    projects: List[str],
    *,
    total_line_cap: int = DEFAULT_T1_LINE_CAP,
) -> str:
    """Compose a multi-project brief respecting the global tier line cap.

    Strategy:
        1. Emit a single header line.
        2. For each project (in stack order), emit its Top Warnings.
        3. Then for each project, emit its body. Skip the body when the
           remaining budget cannot accommodate it.
        4. If the resulting brief still exceeds *total_line_cap*, trim the
           tail and append an explicit elision marker.

    Returns:
        A markdown string. The brief is line-count-bounded by
        *total_line_cap*; this respects HR-5 — the writer never overflows.

    Raises:
        ValueError: when *total_line_cap* is non-positive or *projects*
            is empty.

    Refuses:
        Overflowing the line cap. Stacking more projects than will fit
        under the cap; later projects are elided with a clear marker.
    """
    if int(total_line_cap) <= 0:
        raise ValueError("total_line_cap must be positive")
    if not projects:
        raise ValueError("projects must be a non-empty list")

    install_dir = Path(install_dir)
    knowledge_db = install_dir / "knowledge.db"
    conn = connect(knowledge_db) if knowledge_db.exists() else None

    header = ["# Active Project Stack", ""]
    header.append("Active (most recent first):")
    for project in reversed(projects):
        header.append(f"- `{project}`")
    header.append("")

    warning_blocks: List[List[str]] = []
    body_blocks: List[List[str]] = []
    for project in projects:
        card = _load_card(conn, project) if conn is not None else None
        md_lines: List[str] = []
        if card is not None and card["md_path"]:
            md_lines = _read_brief_lines(Path(card["md_path"]))
        warning_blocks.append(
            _warning_block(conn, project, card) if (conn is not None and card is not None) else []
        )
        body_blocks.append(_body_block(project, md_lines))

    if conn is not None:
        conn.close()

    out: List[str] = list(header)
    for block in warning_blocks:
        for line in block:
            if len(out) >= total_line_cap:
                break
            out.append(line)
        if len(out) >= total_line_cap:
            break

    elided = 0
    for block in body_blocks:
        if len(out) >= total_line_cap:
            elided += 1
            continue
        # Compute available budget; reserve 2 lines for the trailing footer.
        budget = total_line_cap - len(out) - 2
        if budget <= 0:
            elided += 1
            continue
        if len(block) <= budget:
            out.extend(block)
        else:
            out.extend(block[: max(budget - 1, 1)])
            out.append("_(project body trimmed — drill into the brief for full detail)_")
            elided += 0  # already truncated, not elided

    if elided:
        if len(out) < total_line_cap:
            out.append(
                f"_(+{elided} project(s) elided — cap={total_line_cap})_"
            )

    if len(out) > total_line_cap:
        # Final guard rail — should not happen given the budget math above.
        out = out[: total_line_cap - 1]
        out.append(f"_(brief truncated to line cap {total_line_cap})_")

    return "\n".join(out)


def stack_from_state(state: Dict[str, Any], *, max_depth: int = DEFAULT_STACK_DEPTH) -> ProjectStack:
    """Rehydrate a :class:`ProjectStack` from a serialised state dict.

    Returns:
        A new :class:`ProjectStack` populated with the slugs from
        ``state['stack']`` in order. Unknown / non-list payloads yield an
        empty stack — this is a tolerant reload path.

    Refuses:
        Non-dict *state* — raises :class:`TypeError`.
    """
    if not isinstance(state, dict):
        raise TypeError("state must be a dict")
    stack = ProjectStack(max_depth=max_depth)
    entries = state.get("stack")
    if isinstance(entries, list):
        for slug in entries:
            if isinstance(slug, str) and slug.strip():
                stack.push(slug)
    return stack


def state_from_stack(stack: ProjectStack) -> Dict[str, Any]:
    """Serialise a :class:`ProjectStack` to a JSON-compatible dict.

    Returns:
        ``{"stack": [...], "max_depth": int}`` ready for ``json.dumps``.

    Refuses:
        Nothing — pure projection.
    """
    return {"stack": stack.stack(), "max_depth": stack.max_depth}


__all__ = [
    "DEFAULT_STACK_DEPTH",
    "ProjectStack",
    "compose_brief",
    "switch",
    "stack_from_state",
    "state_from_stack",
]
