"""Maturation curve and suggestion engine (E3, HR-9).

Lessons move along the six-state curve::

    scar -> active -> stable -> internalized -> superseded -> archived

The engine ONLY writes to the ``suggested_maturity`` column family. Actual
``maturity`` mutations go through :func:`apply_transition`, which is the
sole writer for that column (called by ``Client.mature_lesson``).

Refuses:
    Any attempt to set ``maturity`` without an explicit ``actor`` argument
    (HR-9: maturation transitions require explicit approval).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from runaway_context._db import connect, transaction
from runaway_context.errors import MaturationApprovalRequired


STATES: Tuple[str, ...] = (
    "scar",
    "active",
    "stable",
    "internalized",
    "superseded",
    "archived",
)

# Age thresholds (in days)
_SCAR_TO_ACTIVE_DAYS = 7
_ACTIVE_TO_STABLE_DAYS = 60
_ACTIVE_TO_STABLE_REVISIT_COUNT = 3
_STABLE_TO_INTERNALIZED_DAYS = 180


def _now_utc() -> datetime:
    """Return the current UTC datetime (naive, matches SQLite CURRENT_TIMESTAMP)."""
    return datetime.utcnow()


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse a SQLite TEXT/DATETIME value to ``datetime`` or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    # SQLite emits 'YYYY-MM-DD HH:MM:SS' or ISO with 'T'.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _count_revisions(conn: sqlite3.Connection, lesson_id: int) -> int:
    """Return the number of recorded versions of a lesson.

    Used as a cheap proxy for "how often this lesson has been touched/cited".
    Falls back to 0 if record_versions is empty or missing.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM record_versions "
        "WHERE table_name = 'lessons_learned' AND record_id = ?",
        (lesson_id,),
    ).fetchone()
    return int(row[0]) if row else 0


class MaturationEngine:
    """Walks lessons_learned and proposes maturity transitions (HR-9).

    The engine NEVER writes to ``maturity`` itself. It writes only to
    ``suggested_maturity`` / ``suggested_maturity_reason`` / ``suggested_at``.
    Applying a suggestion is the job of :func:`apply_transition` (called via
    ``Client.mature_lesson`` under human approval).
    """

    def __init__(self, knowledge_db: Path) -> None:
        """Open a connection to ``knowledge_db`` (must already be v3-migrated).

        Returns:
            None.

        Refuses:
            Implicitly: callers passing a non-existent DB will see sqlite3 errors.
        """
        self._db_path = Path(knowledge_db)

    # ------------------------------------------------------------------ rules

    def _suggest_scar_to_active(self, row: sqlite3.Row, now: datetime) -> Optional[str]:
        """Return a reason string if ``scar`` should advance to ``active``.

        Returns:
            Reason text or None when the rule does not fire.

        Refuses:
            Suggesting movement for any row whose current maturity is not 'scar'.
        """
        if row["maturity"] != "scar":
            return None
        created = _parse_dt(row["created_at"])
        if created is None:
            return None
        age = now - created
        if age >= timedelta(days=_SCAR_TO_ACTIVE_DAYS):
            return (
                "scar older than "
                f"{_SCAR_TO_ACTIVE_DAYS}d (age={age.days}d) — promote to active"
            )
        return None

    def _suggest_active_to_stable(self, conn: sqlite3.Connection,
                                  row: sqlite3.Row, now: datetime) -> Optional[str]:
        """Return a reason string if ``active`` should advance to ``stable``.

        Stability requires the lesson to be old enough AND to show evidence of
        re-engagement (either record_versions count or a last_revisited timestamp).

        Returns:
            Reason text or None when the rule does not fire.

        Refuses:
            Suggesting movement for any row whose current maturity is not 'active'.
        """
        if row["maturity"] != "active":
            return None
        created = _parse_dt(row["created_at"])
        if created is None:
            return None
        age = now - created
        if age < timedelta(days=_ACTIVE_TO_STABLE_DAYS):
            return None
        revisions = _count_revisions(conn, int(row["id"]))
        last_revisited = _parse_dt(row["last_revisited"]) if "last_revisited" in row.keys() else None
        ref_count = 0
        try:
            ref_row = conn.execute(
                "SELECT reference_count FROM lessons_learned WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
            if ref_row is not None and ref_row[0] is not None:
                ref_count = int(ref_row[0])
        except sqlite3.OperationalError:
            ref_count = 0
        re_engaged = (
            revisions >= _ACTIVE_TO_STABLE_REVISIT_COUNT
            or ref_count >= _ACTIVE_TO_STABLE_REVISIT_COUNT
            or last_revisited is not None
        )
        if re_engaged:
            evidence = (
                f"versions={revisions}, refs={ref_count}, "
                f"last_revisited={'yes' if last_revisited else 'no'}"
            )
            return (
                f"active older than {_ACTIVE_TO_STABLE_DAYS}d (age={age.days}d) "
                f"with re-engagement ({evidence}) — promote to stable"
            )
        return None

    def _suggest_stable_to_internalized(self, row: sqlite3.Row,
                                        now: datetime) -> Optional[str]:
        """Return a reason string if ``stable`` should advance to ``internalized``.

        Returns:
            Reason text or None when the rule does not fire.

        Refuses:
            Suggesting movement for any row whose current maturity is not 'stable'.
        """
        if row["maturity"] != "stable":
            return None
        created = _parse_dt(row["created_at"])
        if created is None:
            return None
        age = now - created
        if age >= timedelta(days=_STABLE_TO_INTERNALIZED_DAYS):
            return (
                f"stable older than {_STABLE_TO_INTERNALIZED_DAYS}d "
                f"(age={age.days}d) — promote to internalized"
            )
        return None

    def _suggest_superseded(self, row: sqlite3.Row) -> Optional[str]:
        """Return a reason string if the lesson has been replaced (``superseded_by`` set).

        This fires regardless of the current maturity (per the rule spec).

        Returns:
            Reason text or None when the rule does not fire.

        Refuses:
            Suggesting supersession when ``superseded_by`` is NULL.
        """
        sb = row["superseded_by"] if "superseded_by" in row.keys() else None
        if sb is None:
            return None
        if row["maturity"] == "superseded":
            return None
        return f"superseded_by={sb} present — mark superseded"

    # ----------------------------------------------------------------- driver

    def suggest_transitions(self, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Walk every lesson and compute suggested maturity transitions.

        For each match the engine writes ``suggested_maturity``,
        ``suggested_maturity_reason``, and ``suggested_at`` on the lesson row.
        It never writes ``maturity``.

        Returns:
            A list of ``{lesson_id, from_state, to_state, reason}`` dicts —
            one entry per lesson that earned a suggestion this run.

        Refuses:
            Writing past the maturity column (HR-9). The engine has no path
            to mutate ``maturity``; that is :func:`apply_transition`'s job.
        """
        suggestions: List[Dict[str, Any]] = []
        now = _now_utc()
        conn = connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT id, maturity, created_at, last_revisited, superseded_by "
                "FROM lessons_learned "
                "WHERE deleted_at IS NULL"
            ).fetchall()
            for row in rows:
                current = row["maturity"] or "active"
                reason: Optional[str] = None
                target: Optional[str] = None

                superseded_reason = self._suggest_superseded(row)
                if superseded_reason is not None:
                    target, reason = "superseded", superseded_reason
                else:
                    scar_reason = self._suggest_scar_to_active(row, now)
                    if scar_reason is not None:
                        target, reason = "active", scar_reason
                    else:
                        active_reason = self._suggest_active_to_stable(conn, row, now)
                        if active_reason is not None:
                            target, reason = "stable", active_reason
                        else:
                            stable_reason = self._suggest_stable_to_internalized(row, now)
                            if stable_reason is not None:
                                target, reason = "internalized", stable_reason

                if target is None or reason is None:
                    continue

                suggestions.append({
                    "lesson_id": int(row["id"]),
                    "from_state": current,
                    "to_state": target,
                    "reason": reason,
                })

                if not dry_run:
                    with transaction(conn):
                        conn.execute(
                            "UPDATE lessons_learned "
                            "SET suggested_maturity = ?, "
                            "    suggested_maturity_reason = ?, "
                            "    suggested_at = CURRENT_TIMESTAMP "
                            "WHERE id = ?",
                            (target, reason, int(row["id"])),
                        )
        finally:
            conn.close()
        return suggestions


# ---------------------------------------------------------------- apply_transition

def _previous_audit_hash(conn: sqlite3.Connection) -> Optional[str]:
    """Return the most recent audit_log this_hash, or None when the log is empty."""
    row = conn.execute(
        "SELECT this_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return row[0]


def _compute_hash(previous_hash: Optional[str], actor: str, action: str,
                  target_id: int, timestamp: str) -> str:
    """Compute the chained audit hash for this row (sha256, 16 hex chars)."""
    payload = "|".join([
        previous_hash or "",
        actor,
        action,
        str(target_id),
        timestamp,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def apply_transition(conn: sqlite3.Connection, lesson_id: int, to_state: str,
                     actor: str, reason: Optional[str] = None) -> None:
    """Apply a maturity transition with full audit trail. ONLY writer for ``maturity``.

    The function:
        1. Validates ``to_state`` is in ``maturity_states``.
        2. Validates ``actor`` is non-empty (HR-9).
        3. Reads the current maturity (for the audit detail).
        4. Writes ``maturity``, ``maturity_changed_at``, ``maturity_changed_by``.
        5. Appends an audit_log row with chained sha256.

    Returns:
        None.

    Refuses:
        Empty/missing ``actor`` (raises :class:`MaturationApprovalRequired`).
        Unknown ``to_state`` (raises :class:`ValueError`).
        Missing lesson row (raises :class:`ValueError`).
    """
    if not actor or not str(actor).strip():
        raise MaturationApprovalRequired(
            "apply_transition requires an explicit actor (HR-9)"
        )
    actor = str(actor).strip()

    state_row = conn.execute(
        "SELECT 1 FROM maturity_states WHERE state = ?", (to_state,)
    ).fetchone()
    if state_row is None:
        raise ValueError(f"invalid maturity state: {to_state!r}")

    current_row = conn.execute(
        "SELECT maturity FROM lessons_learned WHERE id = ?", (lesson_id,)
    ).fetchone()
    if current_row is None:
        raise ValueError(f"lesson_id {lesson_id} not found")
    from_state = current_row[0]

    details_obj = {
        "from": from_state,
        "to": to_state,
        "reason": reason,
    }
    details = json.dumps(details_obj, sort_keys=True)

    with transaction(conn):
        conn.execute(
            "UPDATE lessons_learned "
            "SET maturity = ?, "
            "    maturity_changed_at = CURRENT_TIMESTAMP, "
            "    maturity_changed_by = ? "
            "WHERE id = ?",
            (to_state, actor, lesson_id),
        )
        previous_hash = _previous_audit_hash(conn)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        this_hash = _compute_hash(
            previous_hash, actor, "mature_lesson", lesson_id, timestamp,
        )
        conn.execute(
            "INSERT INTO audit_log "
            "(occurred_at, actor, action, target_table, target_id, "
            " details, previous_hash, this_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp,
                actor,
                "mature_lesson",
                "lessons_learned",
                lesson_id,
                details,
                previous_hash,
                this_hash,
            ),
        )
