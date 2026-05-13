"""Database connection helper.

`_db.py` is internal (prefixed `_`). It provides the canonical way to open
a knowledge.db connection with foreign keys ON, sessions.db ATTACHed
on demand, and a nest-safe transaction context manager.

Refuses:
    Opening a DB that has not been migrated to v3 (the validation lives in
    ``Client.__init__``; this module is just connection plumbing).
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


def connect(
    knowledge_db: Path,
    attach_sessions: Optional[Path] = None,
    timeout: float = 5.0,
) -> sqlite3.Connection:
    """Open a knowledge.db connection with FK on and (optionally) sessions.db attached.

    Returns:
        sqlite3.Connection, FK enabled, busy-timeout set.

    Refuses:
        Nothing — connection always succeeds (SQLite creates the file on demand);
        the caller is responsible for confirming a v3 schema is present.
    """
    conn = sqlite3.connect(str(knowledge_db), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if attach_sessions is not None and Path(attach_sessions).exists():
        conn.execute("ATTACH DATABASE ? AS sessions", (str(attach_sessions),))
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Nest-safe transaction context manager.

    If the connection is not currently inside a transaction, this opens
    ``BEGIN IMMEDIATE`` and commits/rollbacks on exit. If the connection is
    *already* inside a transaction (e.g. the caller wrapped a higher-level
    operation), this uses a uniquely-named ``SAVEPOINT`` and releases or
    rolls back to that savepoint on exit. Outer commits remain the
    responsibility of the outermost ``transaction()`` block.

    Returns:
        Yields the same connection. The caller uses normal ``conn.execute``.

    Refuses:
        Nothing — propagates any exception from the user block, rolling
        back the savepoint or top-level transaction first.
    """
    if conn.in_transaction:
        sp_name = "_rc_sp_" + secrets.token_hex(8)
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            yield conn
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            raise
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
