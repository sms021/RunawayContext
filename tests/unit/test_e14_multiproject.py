"""E14 — multi-project session stacking + compose_brief."""
from __future__ import annotations

import json
import sqlite3

import pytest

from runaway_context.multiproject import ProjectStack, compose_brief, switch

pytestmark = pytest.mark.feature


def test_e14_stack_push_pop_current():
    """E14: ProjectStack supports push / pop / current."""
    s = ProjectStack(max_depth=3)
    s.push("alpha")
    s.push("beta")
    s.push("gamma")
    assert s.current() == "gamma"
    assert len(s) == 3
    assert s.pop() == "gamma"
    assert s.current() == "beta"


def test_e14_stack_evicts_oldest_at_capacity():
    """E14: pushing past max_depth evicts the oldest entry."""
    s = ProjectStack(max_depth=2)
    s.push("a")
    s.push("b")
    s.push("c")
    assert s.stack() == ["b", "c"]


def test_e14_stack_move_to_top():
    """E14: pushing a slug already in the stack moves it to the top."""
    s = ProjectStack(max_depth=3)
    s.push("a")
    s.push("b")
    s.push("a")  # already present
    assert s.stack() == ["b", "a"]


def test_e14_switch_pushes():
    """E14: switch() pushes onto the stack."""
    s = ProjectStack(max_depth=3)
    switch(s, "alpha")
    assert s.current() == "alpha"


def test_e14_compose_brief_respects_cap(seeded_client):
    """E14: compose_brief never exceeds total_line_cap."""
    md_path = seeded_client.install_dir / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# tooling\n\n" + "line\n" * 100)
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling", "[]", "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()

    out = compose_brief(seeded_client.install_dir, ["tooling"], total_line_cap=30)
    assert isinstance(out, str)
    assert len(out.splitlines()) <= 30
