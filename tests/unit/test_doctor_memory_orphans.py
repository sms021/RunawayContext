"""Tests for the doctor check_memory_md_orphans check (v3.2.0)."""
from __future__ import annotations

from pathlib import Path

import pytest

from runaway_context import doctor

pytestmark = pytest.mark.feature


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_no_projects_tree_returns_ok(tmp_path):
    """Missing ~/.claude/projects -> OK status, no orphans."""
    finding = doctor.check_memory_md_orphans(
        claude_projects_root=tmp_path / "nope",
    )
    assert finding.level == "ok"


def test_only_pointer_stubs_returns_ok(tmp_path):
    """Files with `type: pointer` are not orphans."""
    root = tmp_path / "projects"
    memdir = root / "-proj" / "memory"
    _write(memdir / "MEMORY.md", "- index\n")
    _write(
        memdir / "feedback_x.md",
        "---\nname: x\ndescription: d\nmetadata:\n  type: pointer\n"
        "  db_table: lessons_learned\n  db_row_id: 1\n---\nstub\n",
    )
    finding = doctor.check_memory_md_orphans(claude_projects_root=root)
    assert finding.level == "ok"


def test_non_pointer_sibling_flagged_as_orphan(tmp_path):
    """A feedback_*.md without `type: pointer` triggers WARN."""
    root = tmp_path / "projects"
    memdir = root / "-proj" / "memory"
    _write(memdir / "MEMORY.md", "- index\n")
    _write(
        memdir / "feedback_x.md",
        "---\nname: x\ndescription: d\nmetadata:\n  type: feedback\n---\nbody\n",
    )
    finding = doctor.check_memory_md_orphans(claude_projects_root=root)
    assert finding.level == "warn"
    assert "1 auto-memory MD" in finding.message
    assert "runaway memory ingest" in finding.remediation


def test_memory_md_itself_never_counted(tmp_path):
    """The MEMORY.md index file is never an orphan, regardless of content."""
    root = tmp_path / "projects"
    memdir = root / "-proj" / "memory"
    _write(memdir / "MEMORY.md", "- pointer\n")
    finding = doctor.check_memory_md_orphans(claude_projects_root=root)
    assert finding.level == "ok"


def test_orphan_files_truncated_to_sample(tmp_path):
    """The WARN payload caps the sampled `files` list to 20 even when more exist."""
    root = tmp_path / "projects"
    memdir = root / "-proj" / "memory"
    for i in range(30):
        _write(
            memdir / f"feedback_{i:02d}.md",
            f"---\nname: n{i}\ndescription: d{i}\nmetadata:\n  type: feedback\n---\nb\n",
        )
    finding = doctor.check_memory_md_orphans(claude_projects_root=root)
    assert finding.level == "warn"
    assert finding.extra.get("total_orphans") == 30
    assert len(finding.extra.get("files", [])) == 20
