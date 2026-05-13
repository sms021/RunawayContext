"""Slug lifecycle registry (E5).

Wraps ``slug_registry`` and ``slug_aliases``. This is the source-of-truth for
"is this slug a real, writable project right now?" — write guards call
:meth:`SlugRegistry.is_valid` before any insert into ``knowledge_chunks`` or
``lessons_learned`` (HR-2).

Operations:

* ``register``   — declare a new canonical slug
* ``alias``      — point a friendly/legacy name at a canonical slug
* ``deprecate``  — retire a slug from writes but keep it queryable
* ``merge``      — fold one slug's data under another canonical slug
* ``resolve``    — follow alias / merge chains to the active canonical
* ``is_valid``   — fast yes/no for write guards

Refuses:
    Slugs that fail :func:`_slugs.is_valid_slug_format` (raises
    :class:`InvalidProjectSlug`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from runaway_context._db import connect, transaction
from runaway_context._slugs import is_valid_slug_format
from runaway_context.errors import InvalidProjectSlug


class SlugRegistry:
    """CRUD + lifecycle operations on the slug registry tables.

    Each method opens its own connection per call. The class is a thin facade
    over SQL; it holds no mutable state across calls.
    """

    def __init__(self, knowledge_db: Path) -> None:
        """Bind the registry to a v3-migrated ``knowledge.db``.

        Returns:
            None.

        Refuses:
            Implicitly: callers passing a non-existent DB will see sqlite3 errors
            on first method call.
        """
        self._db_path = Path(knowledge_db)

    # ----------------------------------------------------------- internal

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    @staticmethod
    def _require_format(slug: str) -> None:
        if not is_valid_slug_format(slug):
            raise InvalidProjectSlug(
                f"slug {slug!r} fails canonical format (lowercase, snake, <=64 chars)"
            )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Optional[str]]:
        return {k: row[k] for k in row.keys()}

    def _fetch(self, conn: sqlite3.Connection, slug: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT slug, status, canonical_slug, description, "
            "       created_at, deprecated_at, merged_at "
            "FROM slug_registry WHERE slug = ?",
            (slug,),
        ).fetchone()

    # ----------------------------------------------------------- public

    def register(self, slug: str, description: Optional[str] = None) -> None:
        """Register a canonical slug. Idempotent (upserts the description).

        Returns:
            None.

        Refuses:
            Slugs that fail the canonical format (raises
            :class:`InvalidProjectSlug`).
        """
        self._require_format(slug)
        conn = self._connect()
        try:
            with transaction(conn):
                existing = self._fetch(conn, slug)
                if existing is None:
                    conn.execute(
                        "INSERT INTO slug_registry (slug, status, description) "
                        "VALUES (?, 'active', ?)",
                        (slug, description),
                    )
                else:
                    if description is not None:
                        conn.execute(
                            "UPDATE slug_registry SET description = ? WHERE slug = ?",
                            (description, slug),
                        )
        finally:
            conn.close()

    def list_active(self) -> List[str]:
        """Return every slug whose status is ``active``, sorted ascending.

        Returns:
            List of slug strings (may be empty).

        Refuses:
            Nothing.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT slug FROM slug_registry WHERE status = 'active' ORDER BY slug"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def list_all(self) -> List[Dict[str, Optional[str]]]:
        """Return every slug registry row (active, deprecated, merged, alias).

        Returns:
            List of dicts (slug, status, canonical_slug, description, created_at,
            deprecated_at, merged_at), sorted by slug ascending.

        Refuses:
            Nothing.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT slug, status, canonical_slug, description, "
                "       created_at, deprecated_at, merged_at "
                "FROM slug_registry ORDER BY slug"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def alias(self, alias_slug: str, canonical: str) -> None:
        """Register ``alias_slug`` as an alias of ``canonical``.

        Side effects:
            * Adds a row to ``slug_aliases``.
            * Inserts or updates ``alias_slug`` in ``slug_registry`` with
              ``status='alias'`` and ``canonical_slug=canonical``.

        Returns:
            None.

        Refuses:
            Alias / canonical that fail the canonical slug format.
            Aliasing to an unknown or non-active canonical
            (raises :class:`InvalidProjectSlug`).
            Self-aliasing (``alias_slug == canonical``).
        """
        self._require_format(alias_slug)
        self._require_format(canonical)
        if alias_slug == canonical:
            raise InvalidProjectSlug("alias and canonical must differ")
        conn = self._connect()
        try:
            with transaction(conn):
                canonical_row = self._fetch(conn, canonical)
                if canonical_row is None or canonical_row["status"] != "active":
                    raise InvalidProjectSlug(
                        f"canonical {canonical!r} must exist and be active"
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO slug_aliases (alias, canonical) "
                    "VALUES (?, ?)",
                    (alias_slug, canonical),
                )
                alias_row = self._fetch(conn, alias_slug)
                if alias_row is None:
                    conn.execute(
                        "INSERT INTO slug_registry "
                        "(slug, status, canonical_slug) "
                        "VALUES (?, 'alias', ?)",
                        (alias_slug, canonical),
                    )
                else:
                    conn.execute(
                        "UPDATE slug_registry "
                        "SET status = 'alias', canonical_slug = ? "
                        "WHERE slug = ?",
                        (canonical, alias_slug),
                    )
        finally:
            conn.close()

    def deprecate(self, slug: str, reason: Optional[str] = None) -> None:
        """Mark ``slug`` as deprecated. Existing rows tagged with it stay queryable.

        Side effects:
            * status -> ``deprecated``
            * deprecated_at -> CURRENT_TIMESTAMP
            * appends ``[DEPRECATED: <reason>]`` to description when reason given

        Returns:
            None.

        Refuses:
            Unknown slug (raises :class:`InvalidProjectSlug`).
            Slug that fails canonical format (raises :class:`InvalidProjectSlug`).
        """
        self._require_format(slug)
        conn = self._connect()
        try:
            with transaction(conn):
                row = self._fetch(conn, slug)
                if row is None:
                    raise InvalidProjectSlug(f"slug {slug!r} is not registered")
                new_description = row["description"]
                if reason:
                    addition = f"[DEPRECATED: {reason}]"
                    if new_description:
                        new_description = f"{new_description}\n{addition}"
                    else:
                        new_description = addition
                conn.execute(
                    "UPDATE slug_registry "
                    "SET status = 'deprecated', "
                    "    deprecated_at = CURRENT_TIMESTAMP, "
                    "    description = ? "
                    "WHERE slug = ?",
                    (new_description, slug),
                )
        finally:
            conn.close()

    def merge(self, from_slug: str, to_slug: str) -> None:
        """Fold ``from_slug`` into ``to_slug``. Adds an alias row as well.

        Returns:
            None.

        Refuses:
            Either slug failing canonical format.
            ``from_slug`` or ``to_slug`` missing from the registry.
            ``to_slug`` whose current status is not ``active``.
            Self-merge (``from_slug == to_slug``).
        """
        self._require_format(from_slug)
        self._require_format(to_slug)
        if from_slug == to_slug:
            raise InvalidProjectSlug("cannot merge a slug into itself")
        conn = self._connect()
        try:
            with transaction(conn):
                from_row = self._fetch(conn, from_slug)
                to_row = self._fetch(conn, to_slug)
                if from_row is None:
                    raise InvalidProjectSlug(
                        f"from_slug {from_slug!r} is not registered"
                    )
                if to_row is None:
                    raise InvalidProjectSlug(
                        f"to_slug {to_slug!r} is not registered"
                    )
                if to_row["status"] != "active":
                    raise InvalidProjectSlug(
                        f"to_slug {to_slug!r} must be active (got {to_row['status']!r})"
                    )
                conn.execute(
                    "UPDATE slug_registry "
                    "SET status = 'merged', "
                    "    canonical_slug = ?, "
                    "    merged_at = CURRENT_TIMESTAMP "
                    "WHERE slug = ?",
                    (to_slug, from_slug),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO slug_aliases (alias, canonical) "
                    "VALUES (?, ?)",
                    (from_slug, to_slug),
                )
        finally:
            conn.close()

    def resolve(self, slug: str) -> Optional[str]:
        """Follow alias/merge chains until an active canonical slug is reached.

        Returns:
            The active canonical slug, or ``None`` if ``slug`` is unknown or
            the chain dead-ends in a non-active row.

        Refuses:
            Nothing — returns ``None`` for malformed input rather than raising,
            because callers (write guards, queries) want a clean fallback.
        """
        if not is_valid_slug_format(slug):
            return None
        conn = self._connect()
        try:
            seen = set()
            current = slug
            while current and current not in seen:
                seen.add(current)
                row = self._fetch(conn, current)
                if row is None:
                    return None
                status = row["status"]
                if status == "active":
                    return current
                canonical = row["canonical_slug"]
                if canonical is None:
                    return None
                current = canonical
            return None
        finally:
            conn.close()

    def is_valid(self, slug: str) -> bool:
        """Return True iff ``slug`` resolves to an active canonical slug.

        Used by write guards (HR-2) to validate ``project_tags`` entries before
        an INSERT into ``knowledge_chunks`` / ``lessons_learned``.

        Returns:
            Boolean.

        Refuses:
            Nothing — returns ``False`` for malformed input.
        """
        return self.resolve(slug) is not None
