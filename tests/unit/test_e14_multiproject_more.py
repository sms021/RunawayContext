"""E14 — multiproject — extra branches: switch, compose_brief, state."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runaway_context.multiproject import (
    ProjectStack,
    compose_brief,
    stack_from_state,
    state_from_stack,
    switch,
)
from runaway_context.brief import PRESERVE_START, PRESERVE_END

pytestmark = pytest.mark.feature


def test_project_stack_invalid_depth():
    with pytest.raises(ValueError):
        ProjectStack(max_depth=0)


def test_project_stack_invalid_push():
    s = ProjectStack(max_depth=3)
    with pytest.raises(ValueError):
        s.push("")
    with pytest.raises(ValueError):
        s.push("   ")


def test_project_stack_pop_empty():
    s = ProjectStack(max_depth=2)
    assert s.pop() is None
    assert s.current() is None


def test_switch_requires_stack_type():
    with pytest.raises(TypeError):
        switch("not a stack", "alpha")


def test_state_round_trip():
    s = ProjectStack(max_depth=3)
    s.push("a")
    s.push("b")
    state = state_from_stack(s)
    s2 = stack_from_state(state)
    assert s2.stack() == ["a", "b"]


def test_stack_from_state_requires_dict():
    with pytest.raises(TypeError):
        stack_from_state(["not", "a", "dict"])


def test_stack_from_state_ignores_bad_entries():
    """Non-string entries in the persisted stack are filtered out."""
    s = stack_from_state({"stack": ["good", 42, None, "", "  ", "also_good"]})
    assert s.stack() == ["good", "also_good"]


def test_compose_brief_requires_positive_cap():
    with pytest.raises(ValueError):
        compose_brief(Path("."), ["x"], total_line_cap=0)


def test_compose_brief_requires_non_empty_projects(tmp_install):
    with pytest.raises(ValueError):
        compose_brief(tmp_install, [], total_line_cap=50)


def test_compose_brief_multi_project_elision(seeded_client):
    """When projects don't fit, later ones are elided with a marker."""
    install = seeded_client.install_dir
    # Register a second slug + brief
    seeded_client.register_slug("other")
    db = seeded_client._knowledge_db

    md_a = install / "briefs" / "tooling" / "CLAUDE.md"
    md_b = install / "briefs" / "other" / "CLAUDE.md"
    md_a.parent.mkdir(parents=True, exist_ok=True)
    md_b.parent.mkdir(parents=True, exist_ok=True)
    body_a = "\n".join([
        "# Tooling",
        PRESERVE_START,
        "tooling description",
        PRESERVE_END,
        "<!-- AUTO-GENERATED",
        "ignore me",
        "-->",
    ] + [f"line a{i}" for i in range(40)])
    md_a.write_text(body_a)
    md_b.write_text("# Other\n" + "\n".join(f"line b{i}" for i in range(50)))

    # Need a lesson in 'other' to make top_warnings exercise the resolver
    lid = seeded_client.log_lesson(
        title="other lesson", project_tags=["other"], severity="warning",
    )
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling", "[]", "[]", "[]", str(md_a), 200),
        )
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("other", "[]", "[]", json.dumps([{"id": lid}]), str(md_b), 200),
        )
        conn.commit()
    finally:
        conn.close()

    out = compose_brief(install, ["tooling", "other"], total_line_cap=20)
    assert len(out.splitlines()) <= 20


def test_compose_brief_no_db(tmp_path):
    """compose_brief works gracefully when knowledge.db doesn't exist."""
    install = tmp_path / "install"
    install.mkdir()
    out = compose_brief(install, ["never_registered"], total_line_cap=50)
    assert isinstance(out, str)
    # Body block placeholder appears
    assert "no brief on disk" in out


def test_compose_brief_warning_block_malformed(seeded_client):
    """compose_brief tolerates malformed top_warnings JSON gracefully."""
    install = seeded_client.install_dir
    db = seeded_client._knowledge_db
    md_path = install / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# Tooling\nbody\n")
    conn = sqlite3.connect(str(db))
    try:
        # Malformed JSON in top_warnings
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling", "[]", "[]", "not json", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    out = compose_brief(install, ["tooling"], total_line_cap=40)
    assert isinstance(out, str)
