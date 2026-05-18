"""Tests for `runaway adopt` orchestrator (v3.3.3)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from runaway_context import adopt

pytestmark = pytest.mark.feature


def test_dry_run_reports_steps_without_writing(client, tmp_path):
    tmp_install = client.install_dir
    """No --apply -> report only, file system untouched."""
    # Build a fake Claude tree with a memory MD
    claude_root = tmp_path / "claude" / "projects"
    memdir = claude_root / "-tooling" / "memory"
    memdir.mkdir(parents=True)
    md_path = memdir / "feedback_x.md"
    md_path.write_text(
        "---\nname: x\ndescription: d\nmetadata:\n  type: feedback\n---\nbody\n"
    )

    # And a hand-edited CLAUDE.md to discover
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("## Section\nbody\n")

    # Register the slug so memory ingest doesn't error
    client.register_slug("tooling")
    client.register_slug("general")

    # Patch scan to return nothing extra
    with patch("runaway_context.doctor.scan_install_candidates", return_value=[]):
        report = adopt.run(
            target_install_dir=tmp_install,
            project_roots=[proj],
            apply=False,
            claude_projects_root=claude_root,
        )

    assert report.apply is False
    names = [s.name for s in report.steps]
    assert "scan_install_candidates" in names
    assert "memory_ingest" in names
    assert "markdown_ingest" in names
    assert "brief_rewrite_pointers" in names

    # Files untouched
    assert "type: pointer" not in md_path.read_text()
    assert "INGESTED" not in (proj / "CLAUDE.md").read_text()


def test_apply_runs_memory_and_markdown_ingest(client, tmp_path):
    tmp_install = client.install_dir
    """--apply actually runs the ingestors. Counts come from each one."""
    claude_root = tmp_path / "claude" / "projects"
    memdir = claude_root / "-tooling" / "memory"
    memdir.mkdir(parents=True)
    (memdir / "feedback_y.md").write_text(
        "---\nname: y\ndescription: dy\nmetadata:\n  type: feedback\n---\nbody-y\n"
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("## Setup\nintro\n## Notes\nstuff\n")

    client.register_slug("tooling")
    client.register_slug("general")

    with patch("runaway_context.doctor.scan_install_candidates", return_value=[]):
        report = adopt.run(
            target_install_dir=tmp_install,
            project_roots=[proj],
            project_slug="general",
            apply=True,
            claude_projects_root=claude_root,
        )

    # memory_ingest step succeeded and counted the file
    mem = next(s for s in report.steps if s.name == "memory_ingest")
    assert mem.status == "ok"
    assert mem.details["counts"].get("inserted") == 1

    # markdown_ingest step inserted sections
    md = next(s for s in report.steps if s.name == "markdown_ingest")
    assert md.status == "ok"
    assert md.details["counts"].get("ingested") == 1

    # CLAUDE.md was rewritten with INGEST_MARKER
    assert "INGESTED" in (proj / "CLAUDE.md").read_text()


def test_apply_refused_without_target_install(tmp_path):
    """--apply against a path with no knowledge.db -> recommended_command in
    steps, but the orchestrator itself doesn't crash. The CLI handler is the
    one that exits 2; here we just verify the module is well-behaved."""
    target = tmp_path / "nonexistent"
    target.mkdir()
    with patch("runaway_context.doctor.scan_install_candidates", return_value=[]):
        # apply=True will attempt to instantiate Client which raises since
        # there's no DB. The orchestrator captures it in the step.
        report = adopt.run(
            target_install_dir=target,
            project_roots=[tmp_path],
            apply=True,
            claude_projects_root=tmp_path / "claude",
        )
    mem = next(s for s in report.steps if s.name == "memory_ingest")
    assert mem.status == "error"


def test_existing_v3_install_marked_as_target(client, tmp_path):
    tmp_install = client.install_dir
    """A scan-hit at the target dir is flagged is_target=True (no migrate
    recommendation)."""
    fake_candidate = {
        "path": str(tmp_install / "knowledge.db"),
        "shape": "v3",
        "row_counts": {},
        "notes": "",
    }
    with patch("runaway_context.doctor.scan_install_candidates",
               return_value=[fake_candidate]):
        report = adopt.run(
            target_install_dir=tmp_install, apply=False,
            claude_projects_root=tmp_path,
        )
    candidate_step = next(
        s for s in report.steps if s.name.startswith("candidate:")
    )
    assert candidate_step.details.get("is_target") is True
    assert candidate_step.recommended_command is None


def test_foreign_db_yields_import_legacy_recommendation(client, tmp_path):
    tmp_install = client.install_dir
    """A discovered foreign-shape DB triggers an import-legacy recommendation."""
    foreign_path = tmp_path / "old" / "knowledge.db"
    fake_candidate = {
        "path": str(foreign_path), "shape": "foreign",
        "row_counts": {"lessons_learned": 87},
        "notes": "has lessons but no chunks",
    }
    with patch("runaway_context.doctor.scan_install_candidates",
               return_value=[fake_candidate]):
        report = adopt.run(
            target_install_dir=tmp_install, apply=False,
            claude_projects_root=tmp_path,
        )
    step = next(s for s in report.steps if "old" in s.name)
    assert step.status == "recommended"
    assert step.recommended_command is not None
    assert "import-legacy" in step.recommended_command


def test_v2_db_yields_migrate_recommendation(client, tmp_path):
    tmp_install = client.install_dir
    v2_path = tmp_path / "v2" / "knowledge.db"
    fake = {
        "path": str(v2_path), "shape": "v2",
        "row_counts": {"knowledge_chunks": 3, "lessons_learned": 5},
        "notes": "",
    }
    with patch("runaway_context.doctor.scan_install_candidates",
               return_value=[fake]):
        report = adopt.run(
            target_install_dir=tmp_install, apply=False,
            claude_projects_root=tmp_path,
        )
    step = next(s for s in report.steps if "v2" in s.name)
    assert step.recommended_command is not None
    assert "db migrate" in step.recommended_command
