"""Retrieval helpers — FTS5 search and hybrid scoring (E13 surface).

These functions are pure read paths on top of an open :mod:`sqlite3`
connection. They never write, never mutate state, and never reach
network. Semantic / vector retrieval lives in :mod:`runaway_context.semantic`
and is composed with these helpers by :func:`hybrid_score`.

Refuses:
    Empty / whitespace queries (raises :class:`ValueError`) — the FTS5
    engine treats those as match-all and would silently flood callers.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional


_FTS_PHRASE_BREAK = re.compile(r'["\x00]')


def _sanitize_query(query: str) -> str:
    """Convert a free-text query into a safe FTS5 MATCH expression.

    Each whitespace-delimited token is wrapped in double quotes, so it is
    parsed by FTS5 as a literal phrase. This neutralises every MATCH-level
    operator (``-``, ``:``, ``*``, ``+``, ``^``, ``AND/OR/NOT``,
    ``column:term``) that would otherwise be interpreted inside a token —
    a parameterised ``:q`` binding is safe against SQL injection but does
    NOT protect against FTS5's own query-language parser.

    Returns:
        A whitespace-joined sequence of phrase-quoted tokens, suitable for
        passing as the right-hand side of ``MATCH :param``.

    Refuses:
        Returning an empty result when the caller passed a non-empty input
        — the caller's check happens above. This is pure scrub.
    """
    cleaned = _FTS_PHRASE_BREAK.sub(" ", query)
    tokens = cleaned.split()
    return " ".join(f'"{t}"' for t in tokens)


def _project_tag_filter(project: Optional[str], alias: str = "") -> str:
    """Return a SQL fragment matching rows whose project_tags JSON contains *project*.

    Args:
        project: slug to match, or None to skip filtering.
        alias: optional table alias prefix (e.g. ``"c"``); when set, columns
            are qualified as ``alias.project`` / ``alias.project_tags`` to
            avoid ambiguity in FTS joins.

    Returns:
        A SQL fragment beginning with ``AND`` when *project* is set, or an
        empty string when it is None.

    Refuses:
        Nothing — pure formatter.
    """
    if not project:
        return ""
    prefix = f"{alias}." if alias else ""
    # SQLite has no JSON ops guaranteed available; LIKE on the JSON text works
    # because slugs are word-shaped (no quoting tricks).
    return (
        f" AND ({prefix}project = :project "
        f"      OR {prefix}project_tags LIKE '%\"' || :project || '\"%') "
    )


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an sqlite3.Row to a plain dict, decoding JSON columns where safe.

    Returns:
        Dict with the same keys as the row; JSON-shaped columns parsed.

    Refuses:
        Nothing — best-effort conversion.
    """
    out: Dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in ("project_tags", "tags") and isinstance(value, str) and value:
            try:
                out[key] = json.loads(value)
            except (ValueError, TypeError):
                out[key] = value
        else:
            out[key] = value
    return out


def search_chunks_fts(
    conn: sqlite3.Connection,
    query: str,
    project: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Run an FTS5 query against ``knowledge_chunks_fts`` (active rows only).

    Returns:
        A list of dicts, each containing the chunk row plus a numeric
        ``fts_score`` (lower = better, mirroring SQLite's ``bm25`` ranking).

    Raises:
        ValueError: when *query* is empty or whitespace.

    Refuses:
        Returning soft-deleted chunks; ``deleted_at IS NULL`` is enforced.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    safe_query = _sanitize_query(query)
    if not safe_query:
        raise ValueError("query reduced to empty after sanitation")

    params: Dict[str, Any] = {"q": safe_query, "limit": int(limit)}
    project_clause = ""
    if project:
        params["project"] = project
        project_clause = _project_tag_filter(project, alias="c")

    sql = (
        "SELECT c.*, bm25(knowledge_chunks_fts) AS fts_score "
        "FROM knowledge_chunks_fts "
        "JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.rowid "
        "WHERE knowledge_chunks_fts MATCH :q "
        "  AND c.deleted_at IS NULL "
        f"  {project_clause} "
        "ORDER BY fts_score ASC "
        "LIMIT :limit"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_lessons_fts(
    conn: sqlite3.Connection,
    query: str,
    project: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Run an FTS5 query against ``lessons_learned_fts``.

    Returns:
        A list of dicts, each with the lesson row + numeric ``fts_score``.

    Raises:
        ValueError: when *query* is empty or whitespace.

    Refuses:
        Returning soft-deleted lessons (always). Returning archived /
        superseded lessons unless ``include_archived`` is True.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    safe_query = _sanitize_query(query)
    if not safe_query:
        raise ValueError("query reduced to empty after sanitation")

    params: Dict[str, Any] = {"q": safe_query, "limit": int(limit)}
    project_clause = ""
    if project:
        params["project"] = project
        project_clause = _project_tag_filter(project, alias="l")

    status_clause = ""
    if not include_archived:
        status_clause = " AND COALESCE(l.maturity, 'active') NOT IN ('archived', 'superseded') "

    sql = (
        "SELECT l.*, bm25(lessons_learned_fts) AS fts_score "
        "FROM lessons_learned_fts "
        "JOIN lessons_learned l ON l.id = lessons_learned_fts.rowid "
        "WHERE lessons_learned_fts MATCH :q "
        "  AND l.deleted_at IS NULL "
        f"  {status_clause} "
        f"  {project_clause} "
        "ORDER BY fts_score ASC "
        "LIMIT :limit"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def hybrid_score(
    fts_score: float,
    vector_score: Optional[float] = None,
    alpha: float = 0.6,
) -> float:
    """Combine an FTS bm25 score with a vector similarity score.

    The blend is a weighted average. ``alpha`` is the weight of the FTS
    side (0..1); the vector side gets ``1 - alpha``. Lower is better, so
    we normalize bm25's "lower = better" sign with vector cosine's
    "higher = better" by negating the vector contribution.

    Returns:
        A single float; lower = more relevant. If *vector_score* is None
        the FTS score is returned unchanged.

    Refuses:
        ``alpha`` outside ``[0, 1]`` (raises :class:`ValueError`).
    """
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError("alpha must be in [0, 1]")
    if vector_score is None:
        return float(fts_score)
    return float(alpha) * float(fts_score) - float(1.0 - alpha) * float(vector_score)
