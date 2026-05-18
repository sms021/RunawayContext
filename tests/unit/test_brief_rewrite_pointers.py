"""Tests for `runaway brief-rewrite-pointers` (v3.2.0)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.feature


def _make_claude_tree(root: Path, slug_dirname: str) -> Path:
    memdir = root / slug_dirname / "memory"
    memdir.mkdir(parents=True)
    return memdir


def test_rewrite_skips_when_slug_unresolved(tmp_path, client, monkeypatch):
    """Memory dir whose name does not map to an active slug -> skipped."""
    root = tmp_path / "projects"
    memdir = _make_claude_tree(root, "-ghost-project")
    (memdir / "MEMORY.md").write_text(
        "<!-- AUTO-GENERATED: runaway brief rewrite-pointers -->\nold\n"
    )

    from runaway_context import cli as cli_mod
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = cli_mod.main([
        "--install-dir", str(client.install_dir),
        "brief-rewrite-pointers", "--claude-root", str(root),
    ])
    assert rc == 0
    report = json.loads(buf.getvalue())
    assert any("ghost-project" in s["path"] for s in report["files_skipped"])
    assert all(r["reason"] == "unresolved slug" for r in report["files_skipped"])


def test_rewrite_refuses_to_clobber_hand_edited(tmp_path, client, monkeypatch):
    """A MEMORY.md without an AUTO-GENERATED marker is left alone."""
    client.register_slug("tooling")
    root = tmp_path / "projects"
    memdir = _make_claude_tree(root, "-tooling")
    handcrafted = "# My personal notes\n\nThis is my hand-written brain dump.\n"
    (memdir / "MEMORY.md").write_text(handcrafted)

    from runaway_context import cli as cli_mod
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = cli_mod.main([
        "--install-dir", str(client.install_dir),
        "brief-rewrite-pointers", "--claude-root", str(root),
    ])
    assert rc == 0
    # File preserved
    assert (memdir / "MEMORY.md").read_text() == handcrafted


def test_rewrite_overwrites_auto_generated(tmp_path, client, monkeypatch):
    """A MEMORY.md with the AUTO-GENERATED marker is regenerated from DB rows."""
    client.register_slug("tooling")
    client.log_lesson(
        title="LessonAlpha", project_tags=["tooling"], severity="info",
    )
    client.propose_knowledge(
        project="tooling", topic="topic1", title="ChunkAlpha", body="b",
    )
    root = tmp_path / "projects"
    memdir = _make_claude_tree(root, "-tooling")
    (memdir / "MEMORY.md").write_text(
        "<!-- AUTO-GENERATED: runaway brief rewrite-pointers -->\nold\n"
    )

    from runaway_context import cli as cli_mod
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = cli_mod.main([
        "--install-dir", str(client.install_dir),
        "brief-rewrite-pointers", "--claude-root", str(root),
    ])
    assert rc == 0
    text = (memdir / "MEMORY.md").read_text()
    assert "LessonAlpha" in text
    assert "ChunkAlpha" in text
    assert text.startswith("<!-- AUTO-GENERATED")


def test_dry_run_writes_nothing(tmp_path, client, monkeypatch):
    """--dry-run reports rewrite candidates but touches no files."""
    client.register_slug("tooling")
    root = tmp_path / "projects"
    memdir = _make_claude_tree(root, "-tooling")
    original = "<!-- AUTO-GENERATED -->\nold body\n"
    (memdir / "MEMORY.md").write_text(original)

    from runaway_context import cli as cli_mod
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = cli_mod.main([
        "--install-dir", str(client.install_dir),
        "brief-rewrite-pointers", "--dry-run", "--claude-root", str(root),
    ])
    assert rc == 0
    assert (memdir / "MEMORY.md").read_text() == original
