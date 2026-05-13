"""E20 — brief.regenerate — coverage for PRESERVE block + dry-run + cap."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import brief as brief_mod
from runaway_context.brief import (
    PRESERVE_END,
    PRESERVE_START,
    regenerate,
    snapshot_before_write,
    _extract_preserve_block,
    _section_lines,
    _json_load,
)
from runaway_context.errors import BriefBudgetExceeded

pytestmark = pytest.mark.feature


def _wire_card(seeded_client, project="tooling", md_line_cap=200,
               active_lesson_ids=None, active_chunk_ids=None,
               top_warnings=None):
    import json
    md_path = seeded_client.install_dir / "briefs" / project / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project,
             json.dumps(active_lesson_ids or []),
             json.dumps(active_chunk_ids or []),
             json.dumps(top_warnings or []),
             str(md_path), md_line_cap),
        )
        conn.commit()
    finally:
        conn.close()
    return md_path


def test_extract_preserve_block_present():
    text = f"head\n{PRESERVE_START}\ncustom content\n{PRESERVE_END}\ntail"
    assert _extract_preserve_block(text) == "custom content"


def test_extract_preserve_block_absent():
    assert _extract_preserve_block("no markers here") == ""
    assert _extract_preserve_block(None) == ""


def test_section_lines_truncated_marker():
    titles = {i: f"t{i}" for i in range(1, 30)}
    lines, truncated = _section_lines("Lessons", list(range(1, 30)),
                                      titles, "LL", 5)
    assert truncated is True
    assert any("+24 more" in l for l in lines)


def test_section_lines_empty_returns_empty():
    lines, truncated = _section_lines("X", [], {}, "P", 5)
    assert lines == []
    assert truncated is False


def test_json_load_malformed_returns_default():
    assert _json_load(None, []) == []
    assert _json_load("not json", []) == []
    assert _json_load("[1,2]", []) == [1, 2]


def test_regenerate_unknown_project_raises(seeded_client):
    with pytest.raises(ValueError):
        regenerate(seeded_client.install_dir, "no_card_for_this")


def test_regenerate_dry_run_does_not_write(seeded_client):
    md_path = _wire_card(seeded_client)
    out = regenerate(seeded_client.install_dir, "tooling", dry_run=True)
    assert out["written"] is False
    assert md_path.exists() is False or md_path.read_text() == ""


def test_regenerate_writes(seeded_client):
    md_path = _wire_card(seeded_client)
    out = regenerate(seeded_client.install_dir, "tooling")
    assert out["written"] is True
    assert md_path.exists()
    assert "tooling" in md_path.read_text()


def test_regenerate_preserves_block(seeded_client):
    md_path = _wire_card(seeded_client)
    md_path.write_text(
        f"# tooling\n{PRESERVE_START}\nKEEP ME\n{PRESERVE_END}\n"
    )
    out = regenerate(seeded_client.install_dir, "tooling")
    content = md_path.read_text()
    assert "KEEP ME" in content


def test_regenerate_budget_exceeded(seeded_client):
    """Tiny cap → BriefBudgetExceeded."""
    _wire_card(seeded_client, md_line_cap=3)
    with pytest.raises(BriefBudgetExceeded):
        regenerate(seeded_client.install_dir, "tooling")


def test_regenerate_with_active_ids(seeded_client):
    """Including active_lesson_ids triggers _section_lines for lessons."""
    lessons = seeded_client.list_lessons(project="tooling")
    chunks = seeded_client.list_chunks(project="tooling")
    _wire_card(
        seeded_client,
        active_lesson_ids=[lessons[0]["id"]],
        active_chunk_ids=[chunks[0]["id"]],
        top_warnings=[{"id": lessons[0]["id"]}],
    )
    out = regenerate(seeded_client.install_dir, "tooling", dry_run=True)
    assert "LL#" in out["content"]
    assert "KS#" in out["content"]


def test_snapshot_before_write_missing_md(seeded_client):
    """snapshot_before_write returns None when md file is missing."""
    out = snapshot_before_write(
        seeded_client.install_dir, "tooling",
        seeded_client.install_dir / "missing.md",
    )
    assert out is None


def test_snapshot_before_write_writes_row(seeded_client, tmp_path):
    """snapshot_before_write returns int id when md file exists."""
    md_path = seeded_client.install_dir / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# old content\n")
    sid = snapshot_before_write(seeded_client.install_dir, "tooling", md_path)
    assert sid > 0


def test_regenerate_resolve_titles_invalid_table():
    """_resolve_titles refuses unknown tables."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        brief_mod._resolve_titles(conn, "bad_table", [1, 2])
