"""Predictive drift rules (E7).

`DriftDetector` runs a fixed set of checks against ``knowledge.db`` and the
installation directory, producing findings the user can act on before the
context actually drifts. Each rule is a small, focused query.

Rules:

1. ``_rule_brief_overrun`` — a ``project_context_card.md_path`` file exceeds
   its ``md_line_cap`` (critical).
2. ``_rule_stack_overload`` — more than 30 active/scar lessons per project
   (warning).
3. ``_rule_orphaned_chunks`` — chunks not referenced by any lesson and not
   touched in 180+ days (info).
4. ``_rule_unreviewed_drafts`` — ``lesson_drafts`` rows stuck pending >30 days
   (warning).
5. ``_rule_aging_scars`` — lessons stuck at ``maturity='scar'`` for more than
   30 days without maturation (warning).

Refuses:
    Operating on a DB that cannot be opened — raises ``sqlite3.OperationalError``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

from runaway_context._db import connect

_LOGGER = logging.getLogger(__name__)


class DriftDetector:
    """Runs predictive drift rules over ``knowledge.db``.

    Refuses:
        Nothing directly. Individual rules may raise ``sqlite3.OperationalError``
        when expected tables are missing; callers should run on a v3-migrated DB.
    """

    def __init__(self, knowledge_db: Path, install_dir: Optional[Path] = None) -> None:
        """Construct a detector bound to a knowledge DB.

        Args:
            knowledge_db: Path to ``knowledge.db``.
            install_dir: Optional install root. Used to resolve relative
                ``md_path`` values from ``project_context_card``.

        Returns:
            None.

        Refuses:
            Nothing.
        """
        self._knowledge_db = Path(knowledge_db)
        self._install_dir = Path(install_dir) if install_dir is not None else None

    # ------------------------------------------------------------------ entrypoint

    def check(self) -> List[Dict[str, Any]]:
        """Run every drift rule and return the combined findings.

        Returns:
            List of dicts: ``{rule, severity, target, message, suggestion}``.

        Refuses:
            Nothing — missing tables produce zero findings for that rule.
        """
        conn = connect(self._knowledge_db)
        findings: List[Dict[str, Any]] = []
        try:
            for rule in (
                self._rule_brief_overrun,
                self._rule_stack_overload,
                self._rule_orphaned_chunks,
                self._rule_unreviewed_drafts,
                self._rule_aging_scars,
            ):
                findings.extend(rule(conn))
        finally:
            conn.close()
        return findings

    # ------------------------------------------------------------------ helpers

    def _resolve_md_path(self, raw: str) -> Path:
        """Resolve a ``md_path`` value to an absolute path.

        If the path is relative, it is anchored at ``install_dir`` when set;
        otherwise relative paths are resolved against the current working
        directory.

        Returns:
            Path object (may or may not exist on disk).

        Refuses:
            Nothing.
        """
        p = Path(raw)
        if p.is_absolute():
            return p
        if self._install_dir is not None:
            return (self._install_dir / p).resolve()
        return p.resolve()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        """Return True if ``table`` exists in the connected DB.

        Returns:
            Boolean.

        Refuses:
            Nothing.
        """
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _count_file_lines(path: Path) -> int:
        """Count lines in a text file. Returns 0 if the file is unreadable.

        Returns:
            Integer line count.

        Refuses:
            Nothing — unreadable files report 0 so callers don't crash on
            partial filesystems.
        """
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for _ in fh)
        except OSError as exc:
            _LOGGER.warning("drift: could not read %s for line count: %s", path, exc)
            return 0

    # ------------------------------------------------------------------ rules

    def _rule_brief_overrun(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Flag any project brief whose MD file exceeds its line cap.

        Returns:
            List of findings (zero or more).

        Refuses:
            Nothing.
        """
        if not self._table_exists(conn, "project_context_card"):
            return []
        out: List[Dict[str, Any]] = []
        rows = conn.execute(
            "SELECT project, md_path, COALESCE(md_line_cap, 150) AS cap "
            "FROM project_context_card WHERE md_path IS NOT NULL AND md_path != ''"
        ).fetchall()
        for row in rows:
            project = row["project"]
            md_path_raw = row["md_path"]
            cap = int(row["cap"] or 150)
            md_path = self._resolve_md_path(md_path_raw)
            if not md_path.exists():
                continue
            actual = self._count_file_lines(md_path)
            if actual > cap:
                out.append(
                    {
                        "rule": "brief_overrun",
                        "severity": "critical",
                        "target": str(md_path),
                        "message": (
                            "Project '{p}' brief at {path} is {actual} lines "
                            "(cap {cap})."
                        ).format(p=project, path=md_path, actual=actual, cap=cap),
                        "suggestion": (
                            "Prune the active lesson/chunk lists for '{p}' and "
                            "regenerate the brief, or raise md_line_cap if the "
                            "project genuinely needs more context."
                        ).format(p=project),
                    }
                )
        return out

    def _rule_stack_overload(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Flag projects with more than 30 scar/active lessons.

        Counts each lesson once per project in its ``project_tags`` JSON array
        (or via the legacy ``project`` column if tags are absent).

        Returns:
            List of findings.

        Refuses:
            Nothing.
        """
        if not self._table_exists(conn, "lessons_learned"):
            return []
        # Check whether maturity column exists (v3 additive)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lessons_learned)")}
        has_maturity = "maturity" in cols
        has_deleted = "deleted_at" in cols

        per_project: Dict[str, int] = {}
        where_parts = []
        if has_maturity:
            where_parts.append("maturity IN ('scar', 'active')")
        else:
            where_parts.append("COALESCE(status, 'active') = 'active'")
        if has_deleted:
            where_parts.append("deleted_at IS NULL")
        where_sql = " AND ".join(where_parts)

        rows = conn.execute(
            "SELECT project, project_tags FROM lessons_learned WHERE " + where_sql
        ).fetchall()
        for row in rows:
            slugs = set()
            tags_raw = row["project_tags"] if "project_tags" in row.keys() else None
            if tags_raw:
                try:
                    parsed = json.loads(tags_raw)
                except (ValueError, TypeError) as exc:
                    _LOGGER.warning(
                        "drift: project_tags JSON parse failed (%s); skipping row",
                        exc,
                    )
                    parsed = None
                if isinstance(parsed, list):
                    for s in parsed:
                        if isinstance(s, str) and s:
                            slugs.add(s)
            if not slugs and row["project"]:
                slugs.add(row["project"])
            for s in slugs:
                per_project[s] = per_project.get(s, 0) + 1

        out: List[Dict[str, Any]] = []
        for project, count in per_project.items():
            if count > 30:
                out.append(
                    {
                        "rule": "stack_overload",
                        "severity": "warning",
                        "target": project,
                        "message": (
                            "Project '{p}' has {n} scar/active lessons (>30). "
                            "Briefs will struggle to stay within their line cap."
                        ).format(p=project, n=count),
                        "suggestion": (
                            "Mature stable lessons (--mature LL#N --to stable) "
                            "or archive obsolete ones to keep the active stack "
                            "lean."
                        ),
                    }
                )
        return out

    def _rule_orphaned_chunks(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Flag chunks no lesson references that haven't been touched in 180+ days.

        Returns:
            List of findings (one per orphaned chunk, capped at 50 to avoid
            wall-of-text output).

        Refuses:
            Nothing.
        """
        if not self._table_exists(conn, "knowledge_chunks"):
            return []
        if not self._table_exists(conn, "lesson_chunks"):
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_chunks)")}
        has_deleted = "deleted_at" in cols
        deleted_filter = "AND kc.deleted_at IS NULL" if has_deleted else ""

        rows = conn.execute(
            f"""
            SELECT kc.id, kc.project, kc.topic, kc.title,
                   COALESCE(kc.updated_at, kc.created_at) AS touched
            FROM knowledge_chunks kc
            LEFT JOIN lesson_chunks lc ON lc.chunk_id = kc.id
            WHERE lc.chunk_id IS NULL
              AND COALESCE(kc.updated_at, kc.created_at) < datetime('now', '-180 days')
              {deleted_filter}
            ORDER BY touched ASC
            LIMIT 50
            """
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "rule": "orphaned_chunks",
                    "severity": "info",
                    "target": "chunk#{}".format(row["id"]),
                    "message": (
                        "Chunk #{id} '{title}' (project={p}) has no lesson "
                        "references and was last touched {t}."
                    ).format(
                        id=row["id"],
                        title=(row["title"] or row["topic"] or "")[:60],
                        p=row["project"],
                        t=row["touched"],
                    ),
                    "suggestion": (
                        "Either link this chunk to a lesson, soft-delete it "
                        "with a deletion_reason, or confirm it's still needed."
                    ),
                }
            )
        return out

    def _rule_unreviewed_drafts(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Flag pending lesson drafts older than 30 days.

        Returns:
            List of findings.

        Refuses:
            Nothing.
        """
        if not self._table_exists(conn, "lesson_drafts"):
            return []
        rows = conn.execute(
            """
            SELECT id, title, proposed_at, proposed_by
            FROM lesson_drafts
            WHERE status = 'pending'
              AND proposed_at < datetime('now', '-30 days')
            ORDER BY proposed_at ASC
            """
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "rule": "unreviewed_drafts",
                    "severity": "warning",
                    "target": "draft#{}".format(row["id"]),
                    "message": (
                        "Draft #{id} '{title}' has been pending since {when} "
                        "(proposed by {by})."
                    ).format(
                        id=row["id"],
                        title=(row["title"] or "")[:60],
                        when=row["proposed_at"],
                        by=row["proposed_by"] or "?",
                    ),
                    "suggestion": (
                        "Review and approve, reject, or merge this draft. "
                        "Stale drafts erode the trust users put in capture."
                    ),
                }
            )
        return out

    def _rule_aging_scars(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Flag lessons stuck at maturity='scar' for more than 30 days.

        Returns:
            List of findings.

        Refuses:
            Nothing.
        """
        if not self._table_exists(conn, "lessons_learned"):
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lessons_learned)")}
        if "maturity" not in cols:
            return []
        has_deleted = "deleted_at" in cols
        has_changed = "maturity_changed_at" in cols
        deleted_filter = "AND deleted_at IS NULL" if has_deleted else ""
        # Use whichever timestamp best represents "when did this become a scar".
        if has_changed:
            ref_expr = "COALESCE(maturity_changed_at, created_at)"
        else:
            ref_expr = "created_at"
        rows = conn.execute(
            f"""
            SELECT id, title, project, {ref_expr} AS ref_at
            FROM lessons_learned
            WHERE maturity = 'scar'
              AND {ref_expr} < datetime('now', '-30 days')
              {deleted_filter}
            ORDER BY ref_at ASC
            """
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "rule": "aging_scars",
                    "severity": "warning",
                    "target": "LL#{}".format(row["id"]),
                    "message": (
                        "Lesson #{id} '{title}' (project={p}) has been 'scar' "
                        "since {when} — long-lived scars usually want to mature."
                    ).format(
                        id=row["id"],
                        title=(row["title"] or "")[:60],
                        p=row["project"],
                        when=row["ref_at"],
                    ),
                    "suggestion": (
                        "Promote to 'active' once the lesson has been observed "
                        "to hold, or mark as 'internalized' if it's now reflex."
                    ),
                }
            )
        return out


def run_check(
    knowledge_db: Path, install_dir: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Module-level wrapper that constructs a ``DriftDetector`` and runs it.

    Returns:
        List of findings as produced by :py:meth:`DriftDetector.check`.

    Refuses:
        Same as :py:meth:`DriftDetector.check`.
    """
    return DriftDetector(knowledge_db, install_dir=install_dir).check()


def print_report(findings: List[Dict[str, Any]], stream: Optional[TextIO] = None) -> None:
    """Print a human-readable drift report to ``stream`` (default stdout).

    Returns:
        None.

    Refuses:
        Nothing.
    """
    out = stream if stream is not None else sys.stdout
    if not findings:
        out.write("No drift detected.\n")
        return
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    ordered = sorted(
        findings,
        key=lambda f: (severity_order.get(f.get("severity", "info"), 3), f.get("rule", "")),
    )
    out.write("Drift findings ({n}):\n".format(n=len(ordered)))
    out.write("-" * 60 + "\n")
    for f in ordered:
        sev = (f.get("severity") or "info").upper()
        rule = f.get("rule", "?")
        target = f.get("target", "?")
        message = f.get("message", "")
        suggestion = f.get("suggestion", "")
        out.write("[{sev}] {rule} :: {target}\n".format(sev=sev, rule=rule, target=target))
        if message:
            out.write("    {msg}\n".format(msg=message))
        if suggestion:
            out.write("    -> {sug}\n".format(sug=suggestion))
        out.write("\n")
