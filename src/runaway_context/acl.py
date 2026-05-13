"""Visibility ACL filter contract (E24, T4 unlock).

Schema is in place (``visibility`` column on ``knowledge_chunks`` and
``lessons_learned``, plus the ``visibility_levels`` reference table).
This module provides the filter contract; the SSO / identity binding
side is an adopter spec (see ``docs/specs/SSO_INTEGRATION.md``).

Tier semantics:
    * T1, T2 → ``'private'`` only.
    * T3     → ``'private'`` and ``'team'``.
    * T4+    → ``'private'``, ``'team'`` and ``'org'``.

Lower-tier installs simply never see higher-tier rows in retrieval; the
DB still stores them (HR-4 keeps the column).

Refuses:
    Updating visibility to a value not in ``visibility_levels`` (the
    schema trigger also enforces this).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from runaway_context._db import transaction
from runaway_context.audit import append as audit_append


_VALID_LEVELS = ("private", "team", "org")
_VALID_TABLES = ("knowledge_chunks", "lessons_learned")


def current_visibility_level(config: Any) -> str:
    """Resolve the install's maximum visibility level from its tier.

    Args:
        config: ``Config`` instance (or any object exposing ``tier``).

    Returns:
        ``'private'`` for T0..T2, ``'team'`` for T3, ``'org'`` for T4+.

    Refuses:
        Inferring a tier from anything other than ``config.tier``.
    """
    tier = getattr(config, "tier", "T1")
    if not isinstance(tier, str):
        return "private"
    tier = tier.upper().strip()
    if tier in ("T0", "T1", "T2"):
        return "private"
    if tier == "T3":
        return "team"
    # T4, T5, anything higher
    return "org"


def _allowed_for_max(max_level: str) -> List[str]:
    """Return the ordered list of levels visible at ``max_level``.

    Returns:
        List of level strings. Unknown ``max_level`` → ``['private']``
        (closed-by-default, HR-1 spirit).

    Refuses:
        Promoting an unknown level.
    """
    if max_level == "org":
        return ["private", "team", "org"]
    if max_level == "team":
        return ["private", "team"]
    return ["private"]


def filter_rows(
    rows: Iterable[Dict[str, Any]], allowed: List[str]
) -> List[Dict[str, Any]]:
    """Drop rows whose ``visibility`` is not in ``allowed``.

    Rows with no ``visibility`` key, or a NULL value, are treated as
    ``'private'`` (closed-by-default).

    Args:
        rows: Iterable of row dicts.
        allowed: List of allowed visibility levels.

    Returns:
        Filtered list (input order preserved).

    Refuses:
        Returning rows tagged with an unknown level.
    """
    allowed_set = set(allowed)
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        level = r.get("visibility") or "private"
        if level in allowed_set:
            out.append(r)
    return out


class VisibilityFilter:
    """Convenience wrapper bound to a :class:`Config`-shaped object."""

    def __init__(self, config: Any) -> None:
        """Bind to a config instance.

        Returns:
            None.

        Refuses:
            Inferring visibility from a ``None`` config (raises
            ``ValueError``).
        """
        if config is None:
            raise ValueError("VisibilityFilter requires a config object")
        self._config = config

    def allowed(self) -> List[str]:
        """Return the list of visibility levels currently visible.

        Returns:
            List of level strings (e.g. ``['private', 'team']``).

        Refuses:
            Nothing.
        """
        return _allowed_for_max(current_visibility_level(self._config))

    def filter(self, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply :func:`filter_rows` using the current allowed list.

        Args:
            rows: Iterable of row dicts.

        Returns:
            Filtered list.

        Refuses:
            Nothing.
        """
        return filter_rows(rows, self.allowed())


def _level_is_valid(conn: sqlite3.Connection, level: str) -> bool:
    """Check ``level`` against the ``visibility_levels`` reference table.

    Returns:
        True iff a row with that level exists.

    Refuses:
        Soft-pass when the table is missing — returns False so the caller
        rejects rather than silently allowing.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM visibility_levels WHERE level = ? LIMIT 1",
            (level,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def set_visibility(
    conn: sqlite3.Connection,
    table: str,
    record_id: int,
    level: str,
    *,
    actor: str,
) -> None:
    """Update ``visibility`` on a single row and log to ``audit_log``.

    Args:
        conn: Open connection — caller manages transactions.
        table: One of ``'knowledge_chunks'`` or ``'lessons_learned'``.
        record_id: Row id to update.
        level: New visibility level (must exist in ``visibility_levels``).
        actor: Opaque author_id of the caller.

    Returns:
        None.

    Raises:
        ValueError: when ``table`` or ``level`` is not allowed.
        LookupError: when the row does not exist or is soft-deleted.

    Refuses:
        Setting a level not in ``visibility_levels`` (schema trigger AND
        explicit pre-check).
    """
    if table not in _VALID_TABLES:
        raise ValueError(
            "table must be one of {v!r}, got {got!r}".format(
                v=_VALID_TABLES, got=table
            )
        )
    if not isinstance(level, str) or not level.strip():
        raise ValueError("level must be a non-empty string")
    if not _level_is_valid(conn, level):
        raise ValueError(
            "visibility level {l!r} not in visibility_levels".format(l=level)
        )
    row = conn.execute(
        "SELECT id, visibility FROM {t} WHERE id = ? AND deleted_at IS NULL".format(
            t=table
        ),
        (int(record_id),),
    ).fetchone()
    if row is None:
        raise LookupError(
            "{t} row id={id} not found or soft-deleted".format(t=table, id=record_id)
        )
    previous = row["visibility"] if hasattr(row, "keys") else row[1]
    with transaction(conn):
        conn.execute(
            "UPDATE {t} SET visibility = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?".format(t=table),
            (level, int(record_id)),
        )
        audit_append(
            conn,
            actor=actor,
            action="visibility.change",
            target_table=table,
            target_id=int(record_id),
            details={"from": previous, "to": level},
        )
