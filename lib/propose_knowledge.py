#!/usr/bin/env python3
"""
RunawayContext v2 — write knowledge_chunks (or "knowledge proposals" in
multi-curator setups) with required project tagging.

Layer 1 guard: every chunk MUST have a --project slug. Validated against
CANONICAL_PROJECT_SLUGS in lib/_project_slugs.py. source_user is auto-stamped.

Usage:
    python3 lib/propose_knowledge.py --project <slug> --topic <slug> \\
        --title "Display title" --body "The knowledge body" [--tags "tag1,tag2"]

If your install is small enough that you write directly to knowledge_chunks
(not via a proposals queue), this script does the direct insert. If you prefer
a review-queue workflow, edit `INSERT_TARGET = 'queue'` to land in a
knowledge_proposals table for human review (you'll need to add that table —
see schema/extras/proposals.sql for the optional schema).
"""
import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import get_conn
from _project_slugs import normalize_user_input_slugs, CANONICAL_PROJECT_SLUGS

INSERT_TARGET = 'direct'  # or 'queue' if you want a proposals table workflow


def detect_user():
    """Identify the calling user — for source_user attribution."""
    return (os.environ.get('SUDO_USER')
            or os.environ.get('USER')
            or getpass.getuser())


def _validate_project(value):
    try:
        return normalize_user_input_slugs(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def main():
    ap = argparse.ArgumentParser(description='Write a knowledge chunk to the KS')
    ap.add_argument('--project', required=True, type=_validate_project,
                    help='REQUIRED — comma-separated canonical project slugs')
    ap.add_argument('--topic', required=True,
                    help='Short slug for this chunk (UNIQUE per project)')
    ap.add_argument('--title', required=True, help='Display title')
    ap.add_argument('--body', help='Inline body text')
    ap.add_argument('--body-stdin', action='store_true',
                    help='Read body from stdin (for multiline content)')
    ap.add_argument('--tags', default='',
                    help='Comma-separated free-form tags (for cross-cutting concerns)')
    args = ap.parse_args()

    body = args.body or ''
    if args.body_stdin:
        body = sys.stdin.read()
    if not body.strip():
        print("ERROR: --body or --body-stdin required", file=sys.stderr)
        sys.exit(1)

    primary_project = args.project[0]
    tags_list = [t.strip() for t in args.tags.split(',') if t.strip()]

    user = detect_user()

    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO knowledge_chunks (project, project_tags, topic, title, body, tags)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            primary_project,
            __import__('json').dumps(args.project),
            args.topic,
            args.title,
            body,
            __import__('json').dumps(tags_list),
        ))
        chunk_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERROR: duplicate (project, topic)? {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        conn.close()

    print(f"KS#{chunk_id} created (project={primary_project}, tags={args.project}, "
          f"topic={args.topic}, source_user={user})")

    # Auto-rebuild affected briefs
    from ll_brief import rebuild_project_brief
    for slug in args.project:
        rebuild_project_brief(slug, write_md=False)
        print(f"  rebuilt brief for {slug}")


if __name__ == '__main__':
    main()
