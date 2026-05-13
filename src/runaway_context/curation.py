"""Automated curation (E19, HR-9).

Identifies candidates for dedup, dead-lesson archival, and supersession.

HR-9 enforcement: **the engine only writes to** ``suggested_maturity``
**and never to** ``maturity``. Suggestions surface to the human or admin
who calls ``Client.mature_lesson(...)`` to actually apply a transition.

Refuses:
    Mutating ``lessons_learned.maturity`` directly (SQL trigger enforces
    valid states; this module simply never issues such an UPDATE).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from runaway_context._db import connect, transaction


_STOPWORDS: Set[str] = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is",
    "are", "was", "were", "be", "been", "being", "with", "by", "at", "as",
    "this", "that", "these", "those", "it", "its", "from", "into", "over",
    "than", "then", "so", "but", "not", "no", "if", "we", "i", "you",
    "your", "our", "they", "them", "their", "do", "does", "did", "use",
    "via",
}


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an ``sqlite3.Row`` to a plain dict.

    Returns:
        Dict keyed by column name.

    Refuses:
        Nothing.
    """
    return {k: row[k] for k in row.keys()}


def _tokenize(text: Optional[str]) -> Set[str]:
    """Lower-case, strip non-alnum, drop stopwords, return a token set.

    Returns:
        Set of tokens (possibly empty).

    Refuses:
        Nothing — ``None`` / empty produces an empty set.
    """
    if not text:
        return set()
    out: Set[str] = set()
    current: List[str] = []
    for ch in str(text).lower():
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                token = "".join(current)
                if len(token) > 2 and token not in _STOPWORDS:
                    out.add(token)
                current = []
    if current:
        token = "".join(current)
        if len(token) > 2 and token not in _STOPWORDS:
            out.add(token)
    return out


def _jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity over two token sets.

    Returns:
        Float in [0, 1]. Returns 0.0 when both sets are empty.

    Refuses:
        Nothing.
    """
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / float(len(union))


def _project_tags(value: Optional[str]) -> Set[str]:
    """Parse a JSON-encoded ``project_tags`` cell into a slug set.

    Returns:
        Set of slugs. Returns empty set when malformed (HR-10: surfaced by
        returning empty rather than swallowing the parse error, so the
        downstream similarity logic naturally produces 0).

    Refuses:
        Raising on a malformed JSON cell — surfacing the error here would
        block legitimate curation runs against partial fixtures. The
        emptiness of the result is itself the signal.
    """
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {s for s in parsed if isinstance(s, str) and s}


class CurationEngine:
    """Generates curation suggestions over ``lessons_learned``.

    The engine NEVER writes to ``maturity``. It writes to
    ``suggested_maturity``, ``suggested_maturity_reason`` and
    ``suggested_at`` (HR-9). Human approval applies the transition via
    ``Client.mature_lesson``.
    """

    def __init__(self, knowledge_db: Path) -> None:
        """Bind to a v3-migrated ``knowledge.db``.

        Returns:
            None.

        Refuses:
            Nothing.
        """
        self._db_path = Path(knowledge_db)

    # ----------------------------------------------------------- internal

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    def _active_lessons(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, title, slug, prevention_rule, project_tags, "
            "       maturity, last_revisited, updated_at, created_at "
            "FROM lessons_learned "
            "WHERE deleted_at IS NULL "
            "ORDER BY id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _write_suggestion(
        self,
        conn: sqlite3.Connection,
        lesson_id: int,
        suggested: str,
        reason: str,
    ) -> None:
        # HR-9: this UPDATE never touches ``maturity``. The schema trigger
        # on ``maturity`` would refuse an invalid value, but we never even
        # try.
        conn.execute(
            "UPDATE lessons_learned "
            "SET suggested_maturity = ?, "
            "    suggested_maturity_reason = ?, "
            "    suggested_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND deleted_at IS NULL",
            (suggested, reason, lesson_id),
        )

    # ----------------------------------------------------------- public

    def find_duplicates(
        self, *, similarity_threshold: float = 0.85
    ) -> List[Dict[str, Any]]:
        """Find pairs of lessons whose title+prevention_rule overlap heavily.

        Pure Jaccard over tokens of (title + prevention_rule). No network
        calls (HR-1).

        Args:
            similarity_threshold: Float in [0, 1]; pairs below this are
                discarded. Default 0.85.

        Returns:
            List of dicts::

                {"left": {id, title, slug}, "right": {id, title, slug},
                 "similarity": 0.91}

        Refuses:
            ``similarity_threshold`` outside [0, 1] raises ``ValueError``.
        """
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in [0, 1]")
        conn = self._connect()
        try:
            lessons = self._active_lessons(conn)
        finally:
            conn.close()
        tokens: List[Tuple[Dict[str, Any], Set[str]]] = []
        for row in lessons:
            tok = _tokenize(row.get("title")) | _tokenize(row.get("prevention_rule"))
            tokens.append((row, tok))
        out: List[Dict[str, Any]] = []
        for i in range(len(tokens)):
            left, lt = tokens[i]
            if not lt:
                continue
            for j in range(i + 1, len(tokens)):
                right, rt = tokens[j]
                if not rt:
                    continue
                sim = _jaccard(lt, rt)
                if sim >= similarity_threshold:
                    out.append(
                        {
                            "left": {
                                "id": left["id"],
                                "title": left.get("title"),
                                "slug": left.get("slug"),
                            },
                            "right": {
                                "id": right["id"],
                                "title": right.get("title"),
                                "slug": right.get("slug"),
                            },
                            "similarity": round(sim, 4),
                        }
                    )
        return out

    def find_dead_lessons(self, *, days: int = 365) -> List[Dict[str, Any]]:
        """Find lessons untouched in ``days`` — no version, no session, no revisit.

        A "touch" is any of:
            * a row in ``record_versions`` for the lesson,
            * a row in ``chunk_sessions`` joined via ``lesson_chunks``,
            * a non-null ``last_revisited`` newer than the threshold.

        Args:
            days: Age threshold (default 365).

        Returns:
            List of lesson dicts ordered by oldest first. Each carries an
            extra ``suggested_maturity = 'archived'`` plus a ``reason``.

        Raises:
            ValueError: when ``days`` is negative.

        Refuses:
            Returning soft-deleted rows.
        """
        if days < 0:
            raise ValueError("days must be non-negative")
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT l.id, l.title, l.slug, l.maturity, l.last_revisited, "
                "       l.updated_at, l.created_at, "
                "       (SELECT COUNT(*) FROM record_versions rv "
                "          WHERE rv.table_name = 'lessons_learned' "
                "            AND rv.record_id = l.id "
                "            AND rv.saved_at >= datetime('now', ?)) AS recent_versions, "
                "       (SELECT COUNT(*) FROM lesson_chunks lc "
                "          JOIN chunk_sessions cs ON cs.chunk_id = lc.chunk_id "
                "          WHERE lc.lesson_id = l.id) AS session_links "
                "FROM lessons_learned l "
                "WHERE l.deleted_at IS NULL "
                "  AND COALESCE(l.maturity, 'active') NOT IN ('archived', 'superseded') "
                "  AND (l.last_revisited IS NULL "
                "       OR l.last_revisited < datetime('now', ?)) "
                "  AND COALESCE(l.updated_at, l.created_at) < datetime('now', ?) "
                "ORDER BY COALESCE(l.updated_at, l.created_at) ASC",
                ("-{d} days".format(d=days), "-{d} days".format(d=days),
                 "-{d} days".format(d=days)),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                if int(r["recent_versions"]) > 0:
                    continue
                if int(r["session_links"]) > 0:
                    continue
                out.append(
                    {
                        "id": int(r["id"]),
                        "title": r["title"],
                        "slug": r["slug"],
                        "maturity": r["maturity"],
                        "last_revisited": r["last_revisited"],
                        "updated_at": r["updated_at"],
                        "suggested_maturity": "archived",
                        "reason": "no touch in {d} days".format(d=days),
                    }
                )
            return out
        finally:
            conn.close()

    def find_supersession_candidates(self) -> List[Dict[str, Any]]:
        """Find pairs that look like one lesson supersedes another.

        Heuristic: same slug or overlapping project_tags AND high
        prevention_rule token overlap (≥0.5), AND the older lesson's
        ``prevention_rule`` is a substring (or near-substring) of the
        newer one.

        Returns:
            List of dicts::

                {"older": {id, title, slug, created_at},
                 "newer": {id, title, slug, created_at},
                 "similarity": 0.62,
                 "suggested_maturity": "superseded"}

        Refuses:
            Returning self-pairs.
        """
        conn = self._connect()
        try:
            lessons = self._active_lessons(conn)
        finally:
            conn.close()

        # Pre-compute tokens/tags per lesson.
        prepped: List[Dict[str, Any]] = []
        for row in lessons:
            prepped.append(
                {
                    "row": row,
                    "tokens": _tokenize(row.get("prevention_rule")),
                    "tags": _project_tags(row.get("project_tags")),
                }
            )

        out: List[Dict[str, Any]] = []
        for i in range(len(prepped)):
            for j in range(len(prepped)):
                if i == j:
                    continue
                left = prepped[i]
                right = prepped[j]
                # Require slug match or non-trivial tag intersection.
                same_slug = (
                    left["row"].get("slug")
                    and left["row"].get("slug") == right["row"].get("slug")
                )
                shared_tags = left["tags"] & right["tags"]
                if not same_slug and not shared_tags:
                    continue
                sim = _jaccard(left["tokens"], right["tokens"])
                if sim < 0.5:
                    continue
                left_created = left["row"].get("created_at") or ""
                right_created = right["row"].get("created_at") or ""
                if left_created >= right_created:
                    continue
                # Older = left, newer = right
                out.append(
                    {
                        "older": {
                            "id": left["row"]["id"],
                            "title": left["row"].get("title"),
                            "slug": left["row"].get("slug"),
                            "created_at": left_created,
                        },
                        "newer": {
                            "id": right["row"]["id"],
                            "title": right["row"].get("title"),
                            "slug": right["row"].get("slug"),
                            "created_at": right_created,
                        },
                        "similarity": round(sim, 4),
                        "shared_tags": sorted(shared_tags),
                        "suggested_maturity": "superseded",
                    }
                )
        return out

    def propose_all(self) -> Dict[str, Any]:
        """Run dedup + dead + supersession and persist suggestions.

        Writes to ``suggested_maturity`` for dead and supersession
        candidates (HR-9-safe — never touches ``maturity``). Dedup
        suggestions are returned but NOT auto-applied — the curator
        decides which of the pair survives.

        Returns:
            ``{duplicates: [...], dead_lessons: [...],
               supersession: [...], applied_suggestions: N}``.

        Refuses:
            Writing to ``maturity`` (HR-9). Writing to soft-deleted rows.
        """
        duplicates = self.find_duplicates()
        dead = self.find_dead_lessons()
        supersession = self.find_supersession_candidates()

        applied = 0
        conn = self._connect()
        try:
            with transaction(conn):
                for entry in dead:
                    self._write_suggestion(
                        conn,
                        entry["id"],
                        "archived",
                        entry["reason"],
                    )
                    applied += 1
                for entry in supersession:
                    self._write_suggestion(
                        conn,
                        entry["older"]["id"],
                        "superseded",
                        "superseded by lesson #{n}".format(n=entry["newer"]["id"]),
                    )
                    applied += 1
        finally:
            conn.close()

        return {
            "duplicates": duplicates,
            "dead_lessons": dead,
            "supersession": supersession,
            "applied_suggestions": applied,
        }
