#!/usr/bin/env python3
"""
RunawayContext v2 — Lessons Learned + project_context_card CLI.

Read commands:
    --ll-get N                 fetch lesson by id
    --ll-search "query"        FTS over lessons
    --ll-list [--ll-project X] list active lessons (optionally filtered)
    --brief <slug>             show project_context_card for a slug
    --list-projects            list all known project briefs

Write commands:
    --log-lesson  --ll-projects <slug[,slug2]> --ll-title "..." [--ll-what-happened "..."]
                  [--ll-why "..."] [--ll-fix "..."] [--ll-prevention "..."]
                  [--ll-severity {critical,warning,info}]
                  [--ll-conversation <conversation_id>]
    --rebuild-brief <slug>      rebuild a project_context_card from tagged source rows
    --rebuild-md <slug>         regenerate the project's CLAUDE.md from its card

All write commands auto-update affected project_context_card rows.

DB locations resolved via lib/_db.py — set RC_KS_DIR env to override.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import get_conn, attach_sessions
from _project_slugs import normalize_user_input_slugs, CANONICAL_PROJECT_SLUGS


def _json_load(s, default):
    if not s:
        return default
    try:
        v = json.loads(s)
        return v if v is not None else default
    except Exception:
        return default


def _json_dump(v):
    return json.dumps(v, ensure_ascii=False)


def _has_project_tag(row_tags_json, slug):
    tags = _json_load(row_tags_json, [])
    if not isinstance(tags, list):
        return False
    return any(isinstance(t, str) and t.lower() == slug.lower() for t in tags)


def _select_tagged(conn, table, slug, extra_where='', limit=500):
    """Return rows from a table tagged with slug. LIKE pre-filter, then strict check."""
    sql = f"""
        SELECT * FROM {table}
        WHERE project_tags IS NOT NULL
          AND project_tags LIKE ?
          {('AND ' + extra_where) if extra_where else ''}
        LIMIT ?
    """
    pattern = f'%"{slug}"%'
    rows = conn.execute(sql, (pattern, limit)).fetchall()
    return [r for r in rows if _has_project_tag(r['project_tags'], slug)]


# ===== brief / context_card =====

def rebuild_project_brief(slug, write_md=False):
    """Rebuild project_context_card from tagged source rows.

    Sources scanned:
      - lessons_learned WHERE project_tags contains slug AND status='active'
      - knowledge_chunks WHERE project_tags contains slug
    """
    conn = get_conn()
    try:
        lessons = _select_tagged(conn, 'lessons_learned', slug,
                                  extra_where="status='active'")
        # Sort lessons: critical first, then warning, then info; recent first within each
        lessons.sort(key=lambda r: (
            {'critical': 0, 'warning': 1, 'info': 2}.get(r['severity'], 3),
            -(int((r['date_learned'] or '0').replace('-', '') or 0)),
        ))
        chunks = _select_tagged(conn, 'knowledge_chunks', slug)

        lesson_ids = [r['id'] for r in lessons]
        chunk_ids = [r['id'] for r in chunks]
        crit = [r for r in lessons if r['severity'] == 'critical'][:3]
        top_warnings = [{'id': r['id'], 'title': r['title']} for r in crit]

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        existing = conn.execute(
            "SELECT * FROM project_context_card WHERE project_slug = ?",
            (slug,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE project_context_card SET
                    top_warnings = ?, active_lesson_ids = ?, active_chunk_ids = ?,
                    last_rebuilt_at = ?, updated_at = ?
                WHERE project_slug = ?
            """, (_json_dump(top_warnings), _json_dump(lesson_ids),
                  _json_dump(chunk_ids), now, now, slug))
        else:
            conn.execute("""
                INSERT INTO project_context_card
                    (project_slug, top_warnings, active_lesson_ids,
                     active_chunk_ids, last_rebuilt_at)
                VALUES (?, ?, ?, ?, ?)
            """, (slug, _json_dump(top_warnings), _json_dump(lesson_ids),
                  _json_dump(chunk_ids), now))
        conn.commit()
        card = dict(conn.execute(
            "SELECT * FROM project_context_card WHERE project_slug = ?", (slug,)
        ).fetchone())
    finally:
        conn.close()

    if write_md and card.get('md_path'):
        from md_writer import write_project_md
        write_project_md(slug, card['md_path'])

    return card


# ===== Read commands =====

def cmd_ll_get(args):
    conn = get_conn(read_only=True)
    row = conn.execute("SELECT * FROM lessons_learned WHERE id = ?",
                       (args.ll_id,)).fetchone()
    if not row:
        print(f"LL#{args.ll_id} not found")
        return 1
    print(f"\n=== LL#{row['id']}: {row['title']} ===")
    print(f"  Slug:        {row['slug']}")
    print(f"  Severity:    {row['severity']}    Status: {row['status']}")
    print(f"  Projects:    {', '.join(_json_load(row['project_tags'], []))}")
    print(f"  Date:        {row['date_learned']}")
    if row['what_happened']:    print(f"\nWhat happened:\n  {row['what_happened']}")
    if row['why']:               print(f"\nWhy:\n  {row['why']}")
    if row['the_fix']:           print(f"\nThe fix:\n  {row['the_fix']}")
    if row['prevention_rule']:   print(f"\nPrevention rule:\n  {row['prevention_rule']}")
    if row['source_conversation_ref']:
        print(f"\nSource conversation: {row['source_conversation_ref']}")
        # If sessions.db is attached, show the session summary too
        if attach_sessions(conn):
            sess = conn.execute(
                "SELECT session_date, summary FROM s.session_logs WHERE conversation_id = ?",
                (row['source_conversation_ref'],)).fetchone()
            if sess:
                print(f"  Session date: {sess['session_date']}")
                print(f"  Session summary: {sess['summary']}")
    if row['superseded_by']:
        print(f"\n⚠ SUPERSEDED BY LL#{row['superseded_by']}")
    links = conn.execute("""
        SELECT chunk_id, relationship FROM lesson_chunks WHERE lesson_id = ?
    """, (args.ll_id,)).fetchall()
    if links:
        print("\nLinked chunks:")
        for l in links:
            print(f"  KS#{l['chunk_id']} ({l['relationship'] or 'related'})")
    conn.close()
    return 0


def cmd_ll_search(args):
    conn = get_conn(read_only=True)
    try:
        rows = conn.execute("""
            SELECT ll.id, ll.title, ll.severity, ll.status, ll.project_tags, ll.date_learned
            FROM lessons_learned_fts fts
            JOIN lessons_learned ll ON ll.id = fts.rowid
            WHERE lessons_learned_fts MATCH ?
              AND ll.status = 'active'
            ORDER BY rank LIMIT ?
        """, (args.ll_search, args.limit or 20)).fetchall()
    except Exception:
        like = f"%{args.ll_search}%"
        rows = conn.execute("""
            SELECT id, title, severity, status, project_tags, date_learned
            FROM lessons_learned
            WHERE (title LIKE ? OR what_happened LIKE ? OR why LIKE ? OR prevention_rule LIKE ?)
              AND status = 'active'
            ORDER BY date_learned DESC LIMIT ?
        """, (like, like, like, like, args.limit or 20)).fetchall()
    if not rows:
        print(f"No lessons matching '{args.ll_search}'")
        return 0
    print(f"\n{len(rows)} lesson(s):")
    for r in rows:
        sev = {'critical': '🔴', 'warning': '🟡', 'info': 'ℹ️'}.get(r['severity'], '·')
        tags = ', '.join(_json_load(r['project_tags'], []))
        print(f"  {sev} LL#{r['id']:<5} [{tags or '—'}] {r['title']}")
    conn.close()
    return 0


def cmd_ll_list(args):
    conn = get_conn(read_only=True)
    where, params = ['1=1'], []
    if args.ll_project:
        where.append("project_tags LIKE ?")
        params.append(f'%"{args.ll_project}"%')
    if args.ll_severity:
        where.append("severity = ?")
        params.append(args.ll_severity)
    if not args.include_superseded:
        where.append("status = 'active'")
    sql = f"""
        SELECT id, title, severity, status, project_tags, date_learned
        FROM lessons_learned
        WHERE {' AND '.join(where)}
        ORDER BY
          CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
          date_learned DESC
        LIMIT ?
    """
    params.append(args.limit or 50)
    rows = conn.execute(sql, params).fetchall()
    if args.ll_project:
        rows = [r for r in rows if _has_project_tag(r['project_tags'], args.ll_project)]
    if not rows:
        print("No lessons match.")
        return 0
    print(f"\n{len(rows)} lesson(s):")
    for r in rows:
        sev = {'critical': '🔴', 'warning': '🟡', 'info': 'ℹ️'}.get(r['severity'], '·')
        tags = ', '.join(_json_load(r['project_tags'], []))
        print(f"  {sev} LL#{r['id']:<5} [{tags or '—'}] {r['title']}")
    conn.close()
    return 0


def cmd_brief(args):
    """First stop when entering a project — show the manifest."""
    conn = get_conn(read_only=True)
    slug = args.brief
    card = conn.execute(
        "SELECT * FROM project_context_card WHERE project_slug = ?", (slug,)
    ).fetchone()
    if not card:
        print(f"\nNo brief exists for '{slug}'.")
        print(f"  Create one: python3 lib/ll_brief.py --rebuild-brief {slug}")
        for table, label in [('lessons_learned', 'lessons'),
                              ('knowledge_chunks', 'chunks')]:
            count = sum(1 for _ in _select_tagged(conn, table, slug))
            if count:
                print(f"  ({count} tagged {label} are queryable but no card yet)")
        conn.close()
        return 1

    print(f"\n=== Project Brief: {card['title'] or card['project_slug']} ===")
    if card['overview']:
        print(f"\n{card['overview']}")
    if card['owner']:
        print(f"\nOwner: {card['owner']}")

    warnings = _json_load(card['top_warnings'], [])
    if warnings:
        print(f"\n🔴 Top warnings:")
        for w in warnings:
            print(f"  - LL#{w['id']}: {w['title']}")

    def _print_kit(kind, ids, table, name_col):
        if not ids:
            return
        rows = conn.execute(
            f"SELECT id, {name_col} AS name FROM {table} "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        by_id = {r['id']: r['name'] for r in rows}
        print(f"\n{kind} ({len(ids)}):")
        for i in ids[:15]:
            print(f"  - {kind[0].upper()}{kind[1:]}#{i}: {(by_id.get(i, '?') or '?')[:80]}")
        if len(ids) > 15:
            print(f"  ... +{len(ids) - 15} more")

    _print_kit('LL',  _json_load(card['active_lesson_ids'], []), 'lessons_learned', 'title')
    _print_kit('KS',  _json_load(card['active_chunk_ids'], []),  'knowledge_chunks', 'title')

    print(f"\nLast rebuilt: {card['last_rebuilt_at']}")
    if card['md_path']:
        print(f"MD path: {card['md_path']}")
    conn.close()
    return 0


def cmd_rebuild_brief(args):
    card = rebuild_project_brief(args.rebuild_brief, write_md=not args.no_md)
    print(f"\nRebuilt brief for '{card['project_slug']}':")
    print(f"  lessons:     {len(_json_load(card['active_lesson_ids'], []))}")
    print(f"  chunks:      {len(_json_load(card['active_chunk_ids'], []))}")
    print(f"  warnings:    {len(_json_load(card['top_warnings'], []))}")
    print(f"  last rebuilt:{card['last_rebuilt_at']}")
    return 0


def cmd_rebuild_md(args):
    conn = get_conn(read_only=True)
    card = conn.execute(
        "SELECT md_path FROM project_context_card WHERE project_slug = ?",
        (args.rebuild_md,)
    ).fetchone()
    conn.close()
    if not card:
        print(f"No brief for {args.rebuild_md}; rebuild first with --rebuild-brief",
              file=sys.stderr)
        return 1
    if not card['md_path']:
        print(f"No md_path set for {args.rebuild_md}; set it via --set-md-path",
              file=sys.stderr)
        return 1
    from md_writer import write_project_md
    result = write_project_md(args.rebuild_md, card['md_path'])
    print(f"Wrote {result['lines']} lines to {card['md_path']}")
    return 0


def cmd_list_projects(args):
    conn = get_conn(read_only=True)
    cards = conn.execute(
        "SELECT project_slug, title, last_rebuilt_at "
        "FROM project_context_card ORDER BY project_slug"
    ).fetchall()
    print(f"\n{len(cards)} project(s) with a brief:")
    for c in cards:
        print(f"  {c['project_slug']:<30} {c['title'] or '—':<30} "
              f"rebuilt {c['last_rebuilt_at'] or 'never'}")
    conn.close()
    return 0


# ===== Write =====

def cmd_log_lesson(args):
    if not args.ll_title:
        print("ERROR: --ll-title required", file=sys.stderr)
        return 1
    if not args.ll_slug:
        slug = args.ll_title.lower()
        slug = ''.join(c if c.isalnum() else '_' for c in slug).strip('_')[:60]
    else:
        slug = args.ll_slug

    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO lessons_learned
                (slug, title, what_happened, why, the_fix, prevention_rule,
                 severity, project_tags, source_conversation_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, args.ll_title,
            args.ll_what_happened or '',
            args.ll_why or '',
            args.ll_fix or '',
            args.ll_prevention or '',
            args.ll_severity or 'warning',
            _json_dump(args.ll_projects),
            args.ll_conversation,
        ))
        ll_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"Logged LL#{ll_id}: {args.ll_title}")
    print(f"  tagged: {', '.join(args.ll_projects)}")
    for slug in args.ll_projects:
        rebuild_project_brief(slug, write_md=not args.no_md)
        print(f"  rebuilt brief for {slug}")
    return 0


# ===== Argparse glue =====

def _validate_project(value):
    try:
        return normalize_user_input_slugs(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def main():
    ap = argparse.ArgumentParser(description='Lessons Learned + project_context_card CLI')

    # Read
    ap.add_argument('--ll-get', dest='ll_id', type=int, help='Fetch lesson by id')
    ap.add_argument('--ll-search', help='FTS search across lessons')
    ap.add_argument('--ll-list', action='store_true', help='List lessons (optionally filtered)')
    ap.add_argument('--ll-project', help='Filter LL list by slug')
    ap.add_argument('--ll-severity-filter', dest='ll_severity', choices=['critical','warning','info'],
                    help='Filter LL list by severity')
    ap.add_argument('--include-superseded', action='store_true')
    ap.add_argument('--brief', help='Show project_context_card for slug')
    ap.add_argument('--rebuild-brief', help='Regenerate project_context_card for slug')
    ap.add_argument('--rebuild-md', help='Regenerate the project CLAUDE.md from its card')
    ap.add_argument('--list-projects', action='store_true')
    ap.add_argument('--no-md', action='store_true', help="When rebuilding brief, skip MD regen")
    ap.add_argument('--limit', type=int, default=50)

    # Write
    ap.add_argument('--log-lesson', action='store_true', help='Insert a new lesson learned')
    ap.add_argument('--ll-title', help='LL title (required for --log-lesson)')
    ap.add_argument('--ll-slug', help='LL slug (auto-derived from title if omitted)')
    ap.add_argument('--ll-projects', type=_validate_project,
                    help='Comma-separated project slugs (REQUIRED for --log-lesson)')
    ap.add_argument('--ll-what-happened', help='LL: the incident')
    ap.add_argument('--ll-why', help='LL: root cause')
    ap.add_argument('--ll-fix', help='LL: what was done')
    ap.add_argument('--ll-prevention', help='LL: prevention rule')
    ap.add_argument('--ll-severity', choices=['critical','warning','info'])
    ap.add_argument('--ll-conversation', help='Source conversation_id (links to sessions.db)')

    args = ap.parse_args()

    if args.log_lesson:
        if not args.ll_projects:
            print("ERROR: --ll-projects required for --log-lesson", file=sys.stderr)
            sys.exit(1)
        sys.exit(cmd_log_lesson(args))
    elif args.ll_id:                     sys.exit(cmd_ll_get(args))
    elif args.ll_search:                 sys.exit(cmd_ll_search(args))
    elif args.ll_list:                   sys.exit(cmd_ll_list(args))
    elif args.brief:                     sys.exit(cmd_brief(args))
    elif args.rebuild_brief:             sys.exit(cmd_rebuild_brief(args))
    elif args.rebuild_md:                sys.exit(cmd_rebuild_md(args))
    elif args.list_projects:             sys.exit(cmd_list_projects(args))
    else:                                ap.print_help()


if __name__ == '__main__':
    main()
