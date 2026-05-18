"""Tests for tag-rich pointer rewriter (v3.3.3)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from runaway_context.cli import _format_tag_list, main as cli_main

pytestmark = pytest.mark.feature


def test_format_tag_list_empty():
    assert _format_tag_list("[]", "[]") == ""
    assert _format_tag_list("", "") == ""


def test_format_tag_list_project_tags_only():
    out = _format_tag_list('["accounting", "vista"]', "[]")
    assert out == " [accounting, vista]"


def test_format_tag_list_combines_project_and_free():
    out = _format_tag_list('["tooling"]', '["cli", "install"]')
    assert out == " [tooling, cli, install]"


def test_format_tag_list_dedupes():
    """Free tag duplicating a project tag is dropped."""
    out = _format_tag_list('["tooling"]', '["tooling", "cli"]')
    assert out == " [tooling, cli]"


def test_format_tag_list_caps_at_five():
    proj = json.dumps([f"p{i}" for i in range(4)])
    free = json.dumps([f"f{i}" for i in range(4)])
    out = _format_tag_list(proj, free)
    # 4 project + 1 free = 5 cap
    assert out == " [p0, p1, p2, p3, f0]"


def test_format_tag_list_handles_garbage_json():
    """Malformed JSON returns empty (defensive — never crash a rewrite)."""
    assert _format_tag_list("not json", "[]") == ""
    assert _format_tag_list("[]", "not json") == ""


def test_rewrite_emits_tag_rich_pointers(tmp_path, client, monkeypatch):
    """End-to-end: a lesson with project_tags lands in the pointer file with brackets."""
    client.register_slug("tooling")
    client.register_slug("cli")
    client.log_lesson(
        title="LessonWithTags",
        project_tags=["tooling", "cli"],
        severity="info",
    )
    root = tmp_path / "projects"
    memdir = root / "-tooling" / "memory"
    memdir.mkdir(parents=True)
    (memdir / "MEMORY.md").write_text(
        "<!-- AUTO-GENERATED: runaway brief rewrite-pointers -->\nold\n"
    )

    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = cli_main([
        "--install-dir", str(client.install_dir),
        "brief-rewrite-pointers", "--claude-root", str(root),
    ])
    assert rc == 0
    text = (memdir / "MEMORY.md").read_text()
    assert "LessonWithTags" in text
    # The line should now include the bracketed tag list.
    line = next(l for l in text.splitlines() if "LessonWithTags" in l)
    # Client may sort tags; verify both tags are bracketed without caring about order.
    assert "tooling" in line and "cli" in line
    assert "[" in line and "]" in line
