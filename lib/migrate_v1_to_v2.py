#!/usr/bin/env python3
"""
RunawayContext v1 → v2 migration.

Existing v1 users have a single sessions.db with knowledge_chunks + lessons_learned
+ sessions all colocated. v2 splits this into knowledge.db (knowledge) +
sessions.db (transcripts).

This script:
  1. BACKS UP your v1 sessions.db to sessions.db.v1.bak
  2. Lifts knowledge_chunks + lessons_learned into a new knowledge.db
  3. Renames the original sessions table → session_logs (v2 name)
  4. Applies the v2 schema migrations to both DBs
  5. Verifies row counts are preserved

DOES NOT run discovery/scraping — for that, use run_RunawayContext.md (the
fresh-install path is for users with no existing RunawayContext setup).

Usage:
    python3 lib/migrate_v1_to_v2.py --v1-db ~/_knowledge/sessions.db
    python3 lib/migrate_v1_to_v2.py --v1-db ~/_knowledge/sessions.db --dry-run
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent / 'schema'


def detect_v1(db_path):
    """Return True if db_path looks like a v1 RunawayContext sessions.db."""
    if not Path(db_path).exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()
    # v1 fingerprint: knowledge_chunks + lessons_learned + sessions all in same DB
    return {'knowledge_chunks', 'lessons_learned', 'sessions'}.issubset(tables)


def row_counts(db_path, tables):
    conn = sqlite3.connect(db_path)
    try:
        out = {}
        for t in tables:
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        return out
    finally:
        conn.close()


def apply_sql_file(db_path, sql_path, tolerant=True):
    """Apply a schema migration. Tolerant mode swallows 'duplicate column' errors."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    sql = Path(sql_path).read_text()
    try:
        conn.executescript(sql)
    except sqlite3.OperationalError as e:
        if not tolerant or 'duplicate column name' not in str(e).lower():
            raise
        # Re-run statement-by-statement, ignoring duplicate-column errors
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e2:
                if 'duplicate column name' not in str(e2).lower():
                    raise
    finally:
        conn.commit()
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--v1-db', default=os.path.expanduser('~/_knowledge/sessions.db'),
                    help='Path to your v1 sessions.db')
    ap.add_argument('--ks-dir',
                    help='Where to put the new knowledge.db + sessions.db (default: same dir as v1-db)')
    ap.add_argument('--dry-run', action='store_true',
                    help="Inspect only — don't write anything")
    args = ap.parse_args()

    v1_db = Path(args.v1_db).expanduser()
    if not detect_v1(str(v1_db)):
        print(f"ERROR: {v1_db} doesn't look like a v1 RunawayContext sessions.db.")
        print("       (expected to find knowledge_chunks + lessons_learned + sessions tables)")
        print()
        print("If this is a fresh install, run setup_db.py instead:")
        print("  python3 lib/setup_db.py")
        sys.exit(1)

    ks_dir = Path(args.ks_dir or v1_db.parent).expanduser()
    knowledge_db = ks_dir / 'knowledge.db'
    sessions_db  = ks_dir / 'sessions.db'
    backup_path = v1_db.with_suffix('.db.v1.bak')

    print(f"=== v1 → v2 migration ===")
    print(f"  v1 DB:        {v1_db}")
    print(f"  ks-dir:       {ks_dir}")
    print(f"  knowledge.db: {knowledge_db}")
    print(f"  sessions.db:  {sessions_db}")
    print(f"  v1 backup:    {backup_path}")
    print()

    # --- 1. Read v1 row counts ---
    v1_counts = row_counts(str(v1_db), ['knowledge_chunks', 'lessons_learned', 'sessions'])
    print(f"  v1 row counts:")
    for t, n in v1_counts.items():
        print(f"    {t}: {n}")
    print()

    if args.dry_run:
        print("  [dry-run] would:")
        print(f"    1. cp {v1_db} {backup_path}")
        print(f"    2. lift knowledge_chunks + lessons_learned → {knowledge_db}")
        print(f"    3. rename sessions → session_logs in {sessions_db}")
        print(f"    4. apply v2 migrations to both DBs")
        print(f"    5. verify counts match")
        return

    # --- 2. Backup ---
    if knowledge_db.exists() or sessions_db.exists():
        print(f"  ERROR: {knowledge_db} or {sessions_db} already exists.")
        print(f"         Move them aside first, or run with --ks-dir pointing somewhere new.")
        sys.exit(1)
    shutil.copy2(v1_db, backup_path)
    print(f"  ✓ Backup: {backup_path}")

    # --- 3. Lift knowledge tables into knowledge.db ---
    conn = sqlite3.connect(str(knowledge_db))
    conn.execute(f"ATTACH '{backup_path}' AS old")
    conn.execute("CREATE TABLE knowledge_chunks AS SELECT * FROM old.knowledge_chunks")
    conn.execute("CREATE TABLE lessons_learned  AS SELECT * FROM old.lessons_learned")
    conn.execute("DETACH old")
    conn.commit()
    conn.close()
    print(f"  ✓ Lifted knowledge_chunks + lessons_learned → knowledge.db")

    # --- 4. Apply v2 migrations to knowledge.db ---
    apply_sql_file(str(knowledge_db), SCHEMA_DIR / '000_knowledge_db.sql', tolerant=True)
    apply_sql_file(str(knowledge_db), SCHEMA_DIR / '001_lessons_learned_v2.sql', tolerant=True)
    print(f"  ✓ Applied v2 schema to knowledge.db")

    # --- 5. Build sessions.db: copy v1 sessions → session_logs ---
    conn = sqlite3.connect(str(sessions_db))
    conn.execute(f"ATTACH '{backup_path}' AS old")
    conn.execute("CREATE TABLE session_logs AS SELECT * FROM old.sessions")
    conn.execute("DETACH old")
    conn.commit()
    conn.close()
    apply_sql_file(str(sessions_db), SCHEMA_DIR / '002_sessions_db.sql', tolerant=True)
    print(f"  ✓ Built sessions.db with v1 sessions → session_logs")

    # --- 6. Verify ---
    v2_knowledge = row_counts(str(knowledge_db), ['knowledge_chunks', 'lessons_learned', 'project_context_card'])
    v2_sessions  = row_counts(str(sessions_db), ['session_logs'])

    print()
    print(f"  v2 row counts:")
    for t, n in v2_knowledge.items():
        print(f"    knowledge.db.{t}: {n}")
    for t, n in v2_sessions.items():
        print(f"    sessions.db.{t}: {n}")

    issues = []
    if v2_knowledge.get('knowledge_chunks') != v1_counts.get('knowledge_chunks'):
        issues.append("knowledge_chunks count mismatch")
    if v2_knowledge.get('lessons_learned') != v1_counts.get('lessons_learned'):
        issues.append("lessons_learned count mismatch")
    if v2_sessions.get('session_logs') != v1_counts.get('sessions'):
        issues.append("sessions count mismatch")

    if issues:
        print()
        print("  ⚠ ISSUES:")
        for i in issues:
            print(f"    - {i}")
        print(f"  Recover from: {backup_path}")
        sys.exit(2)

    print()
    print(f"  ✓ Migration complete. v1 backup retained at {backup_path}")
    print(f"    You can keep that backup as long as you want; v2 doesn't read it.")
    print(f"    Original v1 sessions.db is unchanged at {v1_db} — you can delete or rename it now.")


if __name__ == '__main__':
    main()
