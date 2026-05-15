"""HR-5 contract tests — brief writer refuses to clobber non-brief files.

The brief writer overwrites only files it previously generated (identified by
the AUTO-GENERATED banner). A target that exists but lacks the banner — a
hand-edited CLAUDE.md, README, Constitution, or any user-authored content —
must trigger BriefClobberRefused with no I/O.

Background: a real install (2026-05-13) had ``project_context_card.md_path``
pointing at a user's hand-edited ``CLAUDE.md``; ``regen_brief`` overwrote 12
days of edits. The fix retargets ``md_path`` at ``CLAUDE_BRIEF.md``; this
contract test enforces that the writer can never silently destroy user content
again, regardless of what ``md_path`` is set to.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runaway_context import brief as brief_mod
from runaway_context.errors import BriefClobberRefused

pytestmark = pytest.mark.contract


def _seed_card(client, project: str, md_path: Path) -> None:
    """Register a slug and create a project_context_card pointing at *md_path*."""
    client.register_slug(project)
    lesson_id = client.log_lesson(
        title="lesson-0",
        project_tags=[project],
        severity="info",
    )
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, "
            "md_path, md_line_cap) VALUES (?, ?, ?, ?, ?, ?)",
            (project, json.dumps([lesson_id]), "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()


def test_hr_05_refuses_to_overwrite_user_authored_claude_md(client, tmp_path) -> None:
    """A hand-edited CLAUDE.md without the AUTO-GENERATED banner is not touched."""
    md_path = tmp_path / "userland" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    user_content = (
        "# My Project\n\n"
        "This is my own CLAUDE.md with twelve days of careful notes.\n"
        "Nothing in here mentions an auto-generated banner.\n"
    )
    md_path.write_text(user_content, encoding="utf-8")

    _seed_card(client, "tooling", md_path)

    with pytest.raises(BriefClobberRefused):
        brief_mod.regenerate(client.install_dir, "tooling", dry_run=False)

    # File untouched
    assert md_path.read_text(encoding="utf-8") == user_content


def test_hr_05_refuses_to_overwrite_arbitrary_readme(client, tmp_path) -> None:
    """A README at md_path is also protected by the no-clobber guard."""
    md_path = tmp_path / "userland" / "README.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# README\n\nNot a v3 brief.\n", encoding="utf-8")

    _seed_card(client, "tooling", md_path)

    with pytest.raises(BriefClobberRefused):
        brief_mod.regenerate(client.install_dir, "tooling", dry_run=False)


def test_hr_05_allows_overwrite_of_prior_v3_brief(client, tmp_path) -> None:
    """A file beginning with the AUTO-GENERATED banner is a prior brief; rewriting is OK."""
    md_path = tmp_path / "userland" / "CLAUDE_BRIEF.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    prior_brief = (
        f"{brief_mod.BANNER}\n\n"
        f"{brief_mod.PRESERVE_START}\n## tooling\n\nold preserve block\n"
        f"{brief_mod.PRESERVE_END}\n\n"
        "## Old Section\n- one entry\n"
    )
    md_path.write_text(prior_brief, encoding="utf-8")

    _seed_card(client, "tooling", md_path)

    result = brief_mod.regenerate(client.install_dir, "tooling", dry_run=False)
    assert result["written"] is True
    new = md_path.read_text(encoding="utf-8")
    assert brief_mod.BANNER_HEAD in new


def test_hr_05_creates_new_target_when_missing(client, tmp_path) -> None:
    """When md_path does not yet exist, regenerate writes a fresh brief."""
    md_path = tmp_path / "userland" / "fresh" / "CLAUDE_BRIEF.md"
    _seed_card(client, "tooling", md_path)

    assert not md_path.exists()
    result = brief_mod.regenerate(client.install_dir, "tooling", dry_run=False)
    assert result["written"] is True
    assert md_path.exists()
    assert brief_mod.BANNER_HEAD in md_path.read_text(encoding="utf-8")[:256]


def test_hr_05_dry_run_does_not_touch_user_file(client, tmp_path) -> None:
    """Dry-run mode bypasses the no-clobber check (no write happens anyway)."""
    md_path = tmp_path / "userland" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    user_content = "# user content, no banner\n"
    md_path.write_text(user_content, encoding="utf-8")

    _seed_card(client, "tooling", md_path)

    # Dry-run should NOT raise — composes content in memory only.
    result = brief_mod.regenerate(
        client.install_dir, "tooling", dry_run=True,
    )
    assert result["written"] is False
    assert md_path.read_text(encoding="utf-8") == user_content
