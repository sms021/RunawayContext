#!/usr/bin/env python3
"""
RunawayContext v2 — schema bootstrap.

Applies all v2 schema migrations to knowledge.db (and optionally sessions.db).
Idempotent — safe to re-run. Designed for fresh installs and existing v2 installs.

For v1 → v2 migration, use migrate_v1_to_v2.py instead — DON'T run this on a v1
sessions.db, you'll get a confusing mix.

Usage:
    python3 lib/setup_db.py                              # ~/_knowledge/{knowledge,sessions}.db
    python3 lib/setup_db.py --ks-dir /path/to/_knowledge
    python3 lib/setup_db.py --no-sessions                # skip session-capture DB
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent / 'schema'

# (filename, target — 'knowledge' or 'sessions')
MIGRATIONS = [
    ('000_knowledge_db.sql',         'knowledge'),
    ('001_lessons_learned_v2.sql',   'knowledge'),
    ('002_sessions_db.sql',          'sessions'),
]


def apply_sql_file(db_path, sql_file):
    """Apply a SQL file to a SQLite DB. Treat 'duplicate column name' as
    benign (idempotent re-run); fail loud on anything else."""
    sql = sql_file.read_text()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(sql)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if 'duplicate column name' in msg:
            # Re-run on already-migrated DB — treat each ALTER as idempotent
            for stmt in sql.split(';'):
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e2:
                    if 'duplicate column name' not in str(e2).lower():
                        raise
        else:
            raise
    finally:
        conn.commit()
        conn.close()


def main():
    ap = argparse.ArgumentParser(description='Bootstrap or upgrade RunawayContext v2 schema')
    default_ks = os.environ.get('RC_KS_DIR', os.path.expanduser('~/_knowledge'))
    ap.add_argument('--ks-dir', default=default_ks,
                    help='Where the DBs live (default: $RC_KS_DIR or ~/_knowledge)')
    ap.add_argument('--no-sessions', action='store_true',
                    help="Skip sessions.db creation (no session capture)")
    args = ap.parse_args()

    ks_dir = Path(args.ks_dir).expanduser()
    ks_dir.mkdir(parents=True, exist_ok=True)

    knowledge_db = ks_dir / 'knowledge.db'
    sessions_db  = ks_dir / 'sessions.db'

    print(f"  ks-dir:       {ks_dir}")
    print(f"  knowledge.db: {knowledge_db}")
    print(f"  sessions.db:  {sessions_db}{' (skipped)' if args.no_sessions else ''}")
    print()

    for filename, target in MIGRATIONS:
        if target == 'sessions' and args.no_sessions:
            continue
        path = SCHEMA_DIR / filename
        if not path.exists():
            print(f"  ✗ MISSING schema file: {path}")
            sys.exit(1)
        target_db = knowledge_db if target == 'knowledge' else sessions_db
        try:
            apply_sql_file(str(target_db), path)
            print(f"  ✓ {filename} → {target}.db")
        except Exception as e:
            print(f"  ✗ FAILED {filename} → {target}.db: {e}")
            sys.exit(1)

    print()
    print("Schema applied. Verify with:")
    print(f"  sqlite3 {knowledge_db} '.tables'")
    if not args.no_sessions:
        print(f"  sqlite3 {sessions_db} '.tables'")


if __name__ == '__main__':
    main()
