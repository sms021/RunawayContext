"""Tests for migrator step 11 (memory-orphan scan, v3.2.0)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from runaway_context.migrate import migrate, _count_memory_orphans

pytestmark = pytest.mark.feature


def _make_orphan(memdir: Path, name: str) -> None:
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / name).write_text(
        "---\nname: x\ndescription: d\nmetadata:\n  type: feedback\n---\nbody\n"
    )


def _make_pointer(memdir: Path, name: str) -> None:
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / name).write_text(
        "---\nname: x\ndescription: d\nmetadata:\n  type: pointer\n"
        "  db_table: lessons_learned\n  db_row_id: 1\n---\nstub\n"
    )


def test_count_orphans_empty_tree(tmp_path):
    with patch("runaway_context.migrate.Path.home", return_value=tmp_path):
        # No ~/.claude/projects under tmp_path → 0
        assert _count_memory_orphans() == 0


def test_count_orphans_counts_only_non_pointers(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-a"
    _make_orphan(proj / "memory", "feedback_x.md")
    _make_orphan(proj / "memory", "user_y.md")
    _make_pointer(proj / "memory", "reference_z.md")
    (proj / "memory" / "MEMORY.md").write_text("- index\n")
    with patch("runaway_context.migrate.Path.home", return_value=tmp_path):
        assert _count_memory_orphans() == 2


def test_migrate_reports_step11_orphans(tmp_path):
    proj = tmp_path / ".claude" / "projects" / "-a"
    _make_orphan(proj / "memory", "feedback_x.md")

    db = tmp_path / "knowledge.db"
    with patch("runaway_context.migrate.Path.home", return_value=tmp_path):
        report = migrate(db)

    assert report.succeeded
    assert report.memory_orphans_found == 1
    assert report.memory_ingest_command == "runaway memory ingest --dry-run"
    assert any(s.startswith("step11:memory_orphans=") for s in report.steps_applied)


def test_migrate_step11_zero_orphans_no_command_set(tmp_path):
    """When orphan count is 0, no follow-up command is recommended."""
    db = tmp_path / "knowledge.db"
    with patch("runaway_context.migrate.Path.home", return_value=tmp_path):
        report = migrate(db)
    assert report.succeeded
    assert report.memory_orphans_found == 0
    assert report.memory_ingest_command is None
