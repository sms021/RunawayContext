"""HR-5 contract tests — brief regenerator enforces its line cap.

HR-5: writing the brief past the per-tier line cap raises BriefBudgetExceeded.
The cap defaults to 150; small caps surface refusals at much smaller corpora.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import brief as brief_mod
from runaway_context.errors import BriefBudgetExceeded

pytestmark = pytest.mark.contract


def _seed_card_with_many_lessons(client, project: str, n_lessons: int) -> None:
    """Insert *n_lessons* lessons and wire them into the project_context_card."""
    client.register_slug(project)
    lesson_ids = []
    for i in range(n_lessons):
        lesson_ids.append(
            client.log_lesson(
                title=f"lesson-{i}",
                project_tags=[project],
                severity="info",
            )
        )

    import json
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        md_path = client.install_dir / "briefs" / project / "CLAUDE.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project, json.dumps(lesson_ids), "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()


def test_hr_05_regenerator_refuses_overflow(client) -> None:
    """HR-5: regenerator refuses to write a brief over its cap."""
    _seed_card_with_many_lessons(client, "tooling", n_lessons=200)
    with pytest.raises(BriefBudgetExceeded):
        # Force a tiny cap so the seeded brief exceeds.
        brief_mod.regenerate(
            client.install_dir, "tooling", dry_run=False, cap=20,
        )


def test_hr_05_within_cap_writes_normally(client) -> None:
    """HR-5: under-cap regeneration completes and writes to disk."""
    _seed_card_with_many_lessons(client, "tooling", n_lessons=3)
    result = brief_mod.regenerate(
        client.install_dir, "tooling", dry_run=False, cap=200,
    )
    assert result["written"] is True
    assert result["line_count"] <= 200
    assert Path(result["md_path"]).exists()
