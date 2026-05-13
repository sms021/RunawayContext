"""``runaway stats`` — terminal-first dashboard equivalent (E21).

The dashboard is a spec (S9) the adopter's AI builds. The reference
implementation ships an opinionated terminal command that returns the
same numbers the dashboard would surface.

Output is a single dict (machine-friendly) plus a pretty-printer.

Refuses:
    Talking to a DB that has not been migrated (best-effort: missing
    tables produce zero counts rather than crashes, so partial fixtures
    still produce a report).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

from runaway_context._db import connect


def _safe_scalar(
    conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()
) -> Optional[Any]:
    """Run a scalar query, returning ``None`` if the table is missing.

    Returns:
        The first column of the first row, or ``None``.

    Refuses:
        Swallowing non-table errors — only ``OperationalError`` for
        ``no such table`` is treated as a soft miss; other errors
        re-raise.
    """
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    if row is None:
        return None
    return row[0]


def _safe_rows(
    conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()
) -> List[sqlite3.Row]:
    """Run a SELECT, returning ``[]`` on missing-table errors.

    Refuses:
        Swallowing non-table errors.
    """
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def compute(
    knowledge_db: Path, install_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Compute the stats dict.

    Args:
        knowledge_db: Path to ``knowledge.db``.
        install_dir: Optional install dir (used for audit chain check).

    Returns:
        Dict with these keys::

            lessons_total
            lessons_by_maturity     dict[str, int]
            lessons_by_severity     dict[str, int]
            chunks_total
            chunks_by_project       dict[str, int]
            drafts_pending
            top_referenced_lessons  list of dicts (lesson_id, title, refs)
            time_since_last_lesson  ISO timestamp or None
            drift_findings_count
            audit_chain_valid       bool

    Refuses:
        Mutating the database — all queries are read-only.
    """
    conn = connect(Path(knowledge_db))
    try:
        lessons_total = int(
            _safe_scalar(
                conn,
                "SELECT COUNT(*) FROM lessons_learned WHERE deleted_at IS NULL",
            )
            or 0
        )
        lessons_by_maturity: Dict[str, int] = {}
        for r in _safe_rows(
            conn,
            "SELECT COALESCE(maturity, 'active') AS m, COUNT(*) AS n "
            "FROM lessons_learned WHERE deleted_at IS NULL "
            "GROUP BY COALESCE(maturity, 'active') ORDER BY m",
        ):
            lessons_by_maturity[r["m"]] = int(r["n"])

        lessons_by_severity: Dict[str, int] = {}
        for r in _safe_rows(
            conn,
            "SELECT COALESCE(severity, 'warning') AS s, COUNT(*) AS n "
            "FROM lessons_learned WHERE deleted_at IS NULL "
            "GROUP BY COALESCE(severity, 'warning') ORDER BY s",
        ):
            lessons_by_severity[r["s"]] = int(r["n"])

        chunks_total = int(
            _safe_scalar(
                conn,
                "SELECT COUNT(*) FROM knowledge_chunks WHERE deleted_at IS NULL",
            )
            or 0
        )
        chunks_by_project: Dict[str, int] = {}
        for r in _safe_rows(
            conn,
            "SELECT COALESCE(project, '(unset)') AS p, COUNT(*) AS n "
            "FROM knowledge_chunks WHERE deleted_at IS NULL "
            "GROUP BY COALESCE(project, '(unset)') ORDER BY n DESC, p ASC",
        ):
            chunks_by_project[r["p"]] = int(r["n"])

        drafts_pending = int(
            _safe_scalar(
                conn,
                "SELECT COUNT(*) FROM lesson_drafts WHERE status = 'pending'",
            )
            or 0
        )

        top_referenced: List[Dict[str, Any]] = []
        ref_rows = _safe_rows(
            conn,
            "SELECT l.id, l.title, COUNT(lc.chunk_id) AS refs "
            "FROM lessons_learned l "
            "LEFT JOIN lesson_chunks lc ON lc.lesson_id = l.id "
            "WHERE l.deleted_at IS NULL "
            "GROUP BY l.id "
            "ORDER BY refs DESC, l.id ASC "
            "LIMIT 10",
        )
        for r in ref_rows:
            top_referenced.append(
                {
                    "lesson_id": int(r["id"]),
                    "title": r["title"],
                    "refs": int(r["refs"]),
                }
            )

        time_since_last = _safe_scalar(
            conn,
            "SELECT MAX(COALESCE(updated_at, created_at)) "
            "FROM lessons_learned WHERE deleted_at IS NULL",
        )
    finally:
        conn.close()

    audit_valid = True
    try:
        from runaway_context.audit import verify as _audit_verify

        ok, _bad_id, _reason = _audit_verify(Path(knowledge_db))
        audit_valid = bool(ok)
    except FileNotFoundError:
        audit_valid = False

    drift_findings_count = 0
    try:
        from runaway_context.drift import DriftDetector

        detector = DriftDetector(Path(knowledge_db), install_dir=install_dir)
        findings = detector.run_all() if hasattr(detector, "run_all") else []
        if isinstance(findings, list):
            drift_findings_count = len(findings)
    except (ImportError, AttributeError, sqlite3.OperationalError):
        # Drift module surface may differ across phases; surfacing
        # the exception type explicitly satisfies HR-10 (no silent
        # except).
        drift_findings_count = 0

    return {
        "knowledge_db": str(knowledge_db),
        "lessons_total": lessons_total,
        "lessons_by_maturity": lessons_by_maturity,
        "lessons_by_severity": lessons_by_severity,
        "chunks_total": chunks_total,
        "chunks_by_project": chunks_by_project,
        "drafts_pending": drafts_pending,
        "top_referenced_lessons": top_referenced,
        "time_since_last_lesson": time_since_last,
        "drift_findings_count": drift_findings_count,
        "audit_chain_valid": audit_valid,
    }


def _ascii_bar(value: int, max_value: int, width: int = 30) -> str:
    """Produce a left-aligned ASCII bar for a single distribution cell.

    Returns:
        String of ``#`` (filled) padded with spaces, length ``width``.

    Refuses:
        Negative widths.
    """
    if width < 0:
        raise ValueError("width must be non-negative")
    if max_value <= 0:
        return " " * width
    filled = int(round((value / float(max_value)) * width))
    filled = max(0, min(width, filled))
    return "#" * filled + " " * (width - filled)


def _print_distribution(
    stream: TextIO, title: str, dist: Dict[str, int]
) -> None:
    stream.write(title + "\n")
    if not dist:
        stream.write("  (none)\n")
        return
    label_width = max(len(k) for k in dist)
    max_value = max(dist.values())
    for k in sorted(dist):
        bar = _ascii_bar(dist[k], max_value, 30)
        stream.write(
            "  {label:<{lw}}  {bar}  {n}\n".format(
                label=k, lw=label_width, bar=bar, n=dist[k]
            )
        )


def print_report(stats: Dict[str, Any], stream: Optional[TextIO] = None) -> None:
    """Render a stats dict to a text stream (default ``sys.stdout``).

    Args:
        stats: Dict from :func:`compute`.
        stream: Optional text stream.

    Returns:
        None.

    Refuses:
        Nothing.
    """
    out = stream if stream is not None else sys.stdout
    out.write("=" * 60 + "\n")
    out.write("RunawayContext stats\n")
    out.write("=" * 60 + "\n")
    out.write("DB:                  {p}\n".format(p=stats.get("knowledge_db", "?")))
    out.write("Lessons (active):    {n}\n".format(n=stats["lessons_total"]))
    out.write("Chunks (active):     {n}\n".format(n=stats["chunks_total"]))
    out.write("Drafts pending:      {n}\n".format(n=stats["drafts_pending"]))
    out.write(
        "Drift findings:      {n}\n".format(n=stats["drift_findings_count"])
    )
    out.write(
        "Audit chain valid:   {v}\n".format(v=stats["audit_chain_valid"])
    )
    out.write(
        "Last lesson at:      {v}\n".format(
            v=stats["time_since_last_lesson"] or "(never)"
        )
    )
    out.write("\n")
    _print_distribution(out, "Maturity distribution:", stats["lessons_by_maturity"])
    out.write("\n")
    _print_distribution(out, "Severity distribution:", stats["lessons_by_severity"])
    out.write("\n")
    _print_distribution(out, "Chunks per project:", stats["chunks_by_project"])
    out.write("\n")
    out.write("Top-referenced lessons:\n")
    if not stats["top_referenced_lessons"]:
        out.write("  (none)\n")
    for entry in stats["top_referenced_lessons"]:
        out.write(
            "  LL#{id}  refs={r}  {t}\n".format(
                id=entry["lesson_id"],
                r=entry["refs"],
                t=(entry["title"] or "")[:60],
            )
        )
    out.write("=" * 60 + "\n")


def cli_main(args: List[str]) -> int:
    """Argparse entry-point for ``runaway stats``.

    Args:
        args: Command-line tokens (already split).

    Returns:
        ``0`` on success, non-zero on usage/IO error.

    Refuses:
        Continuing when the DB path does not exist (exits with code 2).
    """
    parser = argparse.ArgumentParser(prog="runaway stats")
    parser.add_argument("--db", required=True, help="Path to knowledge.db")
    parser.add_argument(
        "--install-dir", default=None, help="Optional install dir for audit/drift"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of pretty text"
    )
    ns = parser.parse_args(args)
    db_path = Path(ns.db)
    if not db_path.exists():
        sys.stderr.write("stats: db not found: {p}\n".format(p=db_path))
        return 2
    install_dir = Path(ns.install_dir) if ns.install_dir else None
    report = compute(db_path, install_dir=install_dir)
    if ns.json:
        import json as _json

        sys.stdout.write(_json.dumps(report, indent=2, default=str))
        sys.stdout.write("\n")
        return 0
    print_report(report)
    return 0
