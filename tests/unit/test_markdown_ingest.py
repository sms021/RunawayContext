"""Tests for runaway_context.markdown_ingest (v3.3.3)."""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from runaway_context import markdown_ingest as mi

pytestmark = pytest.mark.feature


SAMPLE = textwrap.dedent("""\
    # Project Notes

    Some intro prose.

    ## SSO Setup
    The auth check runs first.

    Detail spread over multiple lines.

    ## Drift Detector
    Runs as a stop hook.

    - LL: never edit auth/check.php without testing the redirect flow
    - Rule: every write needs a valid slug

    ## Tooling
    Misc.
""")


def test_parse_returns_sections_and_lessons(tmp_path):
    parsed = mi.parse_markdown_doc(SAMPLE)
    assert len(parsed["sections"]) == 4  # # Project Notes + 3 ## blocks
    assert len(parsed["lessons"]) == 2
    assert parsed["lessons"][0]["kind"] == "ll"
    assert "never edit" in parsed["lessons"][0]["text"]


def test_parse_empty_doc():
    parsed = mi.parse_markdown_doc("")
    assert parsed["sections"] == []
    assert parsed["lessons"] == []


def test_parse_no_headings_just_lesson_bullets():
    """Lesson bullets that appear before any heading still get extracted."""
    parsed = mi.parse_markdown_doc("- Rule: every commit needs a message\n")
    assert parsed["sections"] == []
    assert len(parsed["lessons"]) == 1


def test_discover_finds_claude_md(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".cursor" / "rules").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("# x\n")
    (proj / "AGENTS.md").write_text("# y\n")
    (proj / ".cursor" / "rules" / "main.md").write_text("# z\n")
    out = mi.discover_handedited_mds([proj])
    names = {p.name for p in out}
    assert names == {"CLAUDE.md", "AGENTS.md", "main.md"}


def test_discover_excludes_blocked_roots(tmp_path):
    """Even if we point at /etc/, no files are returned."""
    out = mi.discover_handedited_mds([Path("/etc")])
    assert out == []


def test_ingest_one_inserts_sections_and_lessons(tmp_path, client):
    client.register_slug("tooling")
    md = tmp_path / "CLAUDE.md"
    md.write_text(SAMPLE)

    rec = mi.ingest_one(md, client, project="tooling", dry_run=False)
    assert rec.action == "ingested", rec.detail
    assert rec.sections_inserted >= 3
    assert rec.lessons_inserted == 2

    # The file is now a pointer index
    rewritten = md.read_text()
    assert mi.INGEST_MARKER in rewritten
    assert "KC#" in rewritten
    assert "LL#" in rewritten

    # Rows landed with source='handedited:...'
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        n_chunks = conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE source LIKE 'handedited:%'"
        ).fetchone()[0]
        n_lessons = conn.execute(
            "SELECT COUNT(*) FROM lessons_learned WHERE source LIKE 'handedited:%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_chunks >= 3
    assert n_lessons == 2


def test_ingest_one_idempotent_via_marker(tmp_path, client):
    client.register_slug("tooling")
    md = tmp_path / "CLAUDE.md"
    md.write_text(SAMPLE)
    mi.ingest_one(md, client, project="tooling")
    # Second pass should be a no-op
    rec = mi.ingest_one(md, client, project="tooling")
    assert rec.action == "already_ingested"


def test_ingest_one_force_re_ingests(tmp_path, client):
    client.register_slug("tooling")
    md = tmp_path / "CLAUDE.md"
    md.write_text(SAMPLE)
    mi.ingest_one(md, client, project="tooling")
    md.write_text(SAMPLE)  # restore content
    rec = mi.ingest_one(md, client, project="tooling", force=True)
    assert rec.action == "ingested"


def test_ingest_dry_run_writes_nothing(tmp_path, client):
    client.register_slug("tooling")
    md = tmp_path / "CLAUDE.md"
    original = SAMPLE
    md.write_text(original)
    rec = mi.ingest_one(md, client, project="tooling", dry_run=True)
    assert rec.action == "ingested"
    assert md.read_text() == original
    # DB untouched
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE source LIKE 'handedited:%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_ingest_skips_files_with_no_content(tmp_path, client):
    md = tmp_path / "empty.md"
    md.write_text("just prose, no headings or bullets\n")
    client.register_slug("tooling")
    rec = mi.ingest_one(md, client, project="tooling")
    assert rec.action == "skipped"


def test_ingest_all_sweep(tmp_path, client):
    client.register_slug("tooling")
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("## A\nfoo\n")
    (tmp_path / "AGENTS.md").write_text("## B\nbar\n")
    (tmp_path / ".cursor" / "rules" / "main.md").write_text("## C\nbaz\n")
    report = mi.ingest_all(client, project="tooling", roots=[tmp_path])
    assert report.counts().get("ingested") == 3
