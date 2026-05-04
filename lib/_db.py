"""
Shared DB helpers — locate knowledge.db and open a connection with FK support.

DB location resolution order:
  1. RC_KS_DIR environment variable (if set) → $RC_KS_DIR/knowledge.db
  2. ~/_knowledge/knowledge.db (default)

To override, export RC_KS_DIR=/path/to/your/_knowledge directory.
"""
import os
import sqlite3
import sys
from pathlib import Path


def ks_dir():
    return Path(os.environ.get('RC_KS_DIR', os.path.expanduser('~/_knowledge')))


def knowledge_db_path():
    return ks_dir() / 'knowledge.db'


def sessions_db_path():
    return ks_dir() / 'sessions.db'


def get_conn(read_only=False):
    """Open a connection to knowledge.db with foreign keys enabled."""
    p = knowledge_db_path()
    if not p.exists():
        print(f"ERROR: {p} not found.", file=sys.stderr)
        print(f"       Run: python3 lib/setup_db.py", file=sys.stderr)
        sys.exit(1)
    if read_only:
        conn = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    else:
        conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def attach_sessions(conn, alias='s'):
    """ATTACH sessions.db onto an existing knowledge.db connection.
    Use case: cross-DB JOIN to pull session content alongside lessons/chunks.

    Caller is responsible for DETACH (or just close the connection).

    Returns True if attached, False if sessions.db doesn't exist (caller should
    expect any cross-DB JOINs to fail — handle gracefully).
    """
    sp = sessions_db_path()
    if not sp.exists():
        return False
    conn.execute(f"ATTACH '{sp}' AS {alias}")
    return True
