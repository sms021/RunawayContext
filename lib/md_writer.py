"""
RunawayContext v2 — project CLAUDE.md regenerator.

Renders a project_context_card row into a slim auto-generated markdown file at
its md_path. Enforces:
  * Banner declaring auto-generation (hand-edits get wiped).
  * Hard line cap (default from card.md_line_cap, fallback 150).
  * One PRESERVE block — content between PRESERVE_START / PRESERVE_END markers
    survives across regenerations. This is where you put the project overview.
  * Top-N pointers per section, with a "drill via CLI" footer for overflow.

Hand-edits OUTSIDE the PRESERVE block are overwritten on every rebuild.
That's by design — the DB is the source of truth.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import get_conn

PRESERVE_START = '<!-- PRESERVE_START -->'
PRESERVE_END   = '<!-- PRESERVE_END -->'

BANNER = """<!-- AUTO-GENERATED — DO NOT HAND-EDIT.
This file is regenerated from knowledge.db / project_context_card.
Edit content via:  python3 lib/ll_brief.py --log-lesson  (or propose_knowledge.py)
Then rebuild:      python3 lib/ll_brief.py --rebuild-brief <slug>
                   python3 lib/ll_brief.py --rebuild-md <slug>
The only block preserved across rebuilds is between PRESERVE_START / PRESERVE_END below. -->
"""


def _json_load(s, default):
    if not s:
        return default
    try:
        v = json.loads(s)
        return v if v is not None else default
    except Exception:
        return default


def _extract_preserve_block(existing_md):
    if not existing_md:
        return ''
    m = re.search(
        re.escape(PRESERVE_START) + r'(.*?)' + re.escape(PRESERVE_END),
        existing_md, re.DOTALL,
    )
    return m.group(1).strip() if m else ''


def write_project_md(slug, md_path):
    """Regenerate the project's CLAUDE.md from its context card.

    Returns {'path', 'lines', 'truncated_kinds':[]}.
    Raises RuntimeError if no card exists for slug.
    """
    conn = get_conn()
    try:
        card = conn.execute(
            "SELECT * FROM project_context_card WHERE project_slug = ?", (slug,)
        ).fetchone()
        if not card:
            raise RuntimeError(f"No project_context_card for slug '{slug}'")

        line_cap = card['md_line_cap'] or 150

        # Preserve block: pull from existing file; seed if absent
        preserved = ''
        if os.path.exists(md_path):
            with open(md_path) as f:
                preserved = _extract_preserve_block(f.read())
        if not preserved:
            default_overview = (
                "(One-paragraph overview of this project — what it does, who owns it, why it exists. "
                "Edit ONLY between PRESERVE_START / PRESERVE_END markers; everything else regenerates.)"
            )
            title = card['title'] or slug
            overview = card['overview'] or default_overview
            preserved = f"\n## {title}\n\n{overview}\n"

        out = [BANNER, '', PRESERVE_START, preserved.strip(), PRESERVE_END, '']

        # Top warnings
        warnings = _json_load(card['top_warnings'], [])
        if warnings:
            out.append('## ⚠ Top Warnings (read first)')
            for w in warnings:
                out.append(f'- **LL#{w["id"]}**: {w["title"]}')
            out.append('')

        truncated_kinds = []

        def _section(label, ids, table, name_col, prefix, max_per=20):
            if not ids:
                return
            rows = conn.execute(
                f"SELECT id, {name_col} AS name FROM {table} "
                f"WHERE id IN ({','.join('?' * len(ids))})", ids,
            ).fetchall()
            by_id = {r['id']: r['name'] for r in rows}
            out.append(f'## {label} ({len(ids)})')
            shown = ids[:max_per]
            for i in shown:
                name = (by_id.get(i, '?') or '?')[:120]
                out.append(f'- {prefix}#{i} — {name}')
            if len(ids) > max_per:
                out.append(f'- _... +{len(ids) - max_per} more — query via the CLI for full list_')
                truncated_kinds.append(label)
            out.append('')

        _section('Lessons Learned',  _json_load(card['active_lesson_ids'], []),
                 'lessons_learned', 'title', 'LL', 25)
        _section('Knowledge Chunks', _json_load(card['active_chunk_ids'], []),
                 'knowledge_chunks', 'title', 'KS', 20)

        # Footer
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        out.append('---')
        out.append(f'_Brief regenerated {now} from knowledge.db. Project slug: `{slug}`._')
        out.append(f'_Drill into any pointer: `python3 lib/ll_brief.py --ll-get N` or `--ks-get N`._')

        # Hard line cap
        body = '\n'.join(out)
        lines = body.split('\n')
        if len(lines) > line_cap:
            body = '\n'.join(lines[:line_cap - 2])
            body += f'\n\n_(truncated to {line_cap}-line cap; full content queryable via CLI)_\n'

        # Write
        os.makedirs(os.path.dirname(md_path) or '.', exist_ok=True)
        with open(md_path, 'w') as f:
            f.write(body)

        conn.execute(
            "UPDATE project_context_card SET last_md_written_at = ? WHERE project_slug = ?",
            (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), slug),
        )
        conn.commit()
    finally:
        conn.close()

    return {'path': md_path, 'lines': len(body.split('\n')),
            'truncated_kinds': truncated_kinds}
