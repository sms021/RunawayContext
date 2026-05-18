"""Tests for `runaway memory ingest --map` (v3.3.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from runaway_context import memory_ingest as mi

pytestmark = pytest.mark.feature


def _write_memory_md(path: Path, *, mtype: str, name: str, description: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        f"metadata:\n  type: {mtype}\n---\n{body}\n"
    )


def test_explicit_map_overrides_heuristic(tmp_path, client):
    """--map -unknown-dir=tooling routes the dir's MDs to the 'tooling' slug."""
    client.register_slug("tooling")
    root = tmp_path / "projects"
    memdir = root / "-unknown-dir-name" / "memory"
    _write_memory_md(memdir / "feedback_x.md", mtype="feedback",
                     name="x", description="d", body="b")

    # Without --map this would error (no slug matches '-unknown-dir-name')
    rec_no_map = mi.ingest_one(memdir / "feedback_x.md", client, dry_run=True)
    assert rec_no_map.action == "error"

    # Rewrite the file (the no-map call would have rewritten on success but
    # since it errored it's still raw — start fresh anyway).
    _write_memory_md(memdir / "feedback_x.md", mtype="feedback",
                     name="x", description="d", body="b")

    rec_with_map = mi.ingest_one(
        memdir / "feedback_x.md", client, dry_run=True,
        explicit_map={"-unknown-dir-name": "tooling"},
    )
    assert rec_with_map.action == "inserted"


def test_explicit_map_in_ingest_all(tmp_path, client):
    """ingest_all threads explicit_map through to each ingest_one call."""
    client.register_slug("tooling")
    root = tmp_path / "projects"
    memdir = root / "-arbitrary" / "memory"
    _write_memory_md(memdir / "feedback_y.md", mtype="feedback",
                     name="y", description="dy", body="b")

    report = mi.ingest_all(
        client, claude_projects_root=root,
        explicit_map={"-arbitrary": "tooling"},
    )
    assert report.counts().get("inserted") == 1


def test_map_dict_only_matches_exact_dirname(tmp_path, client):
    """Map keys are dirname strings; no fuzzy matching."""
    client.register_slug("tooling")
    root = tmp_path / "projects"
    memdir = root / "-a-real-dir" / "memory"
    _write_memory_md(memdir / "feedback.md", mtype="feedback",
                     name="z", description="d", body="b")

    # Map references a different dirname — should NOT match.
    rec = mi.ingest_one(
        memdir / "feedback.md", client, dry_run=True,
        explicit_map={"-different-dir": "tooling"},
    )
    # Falls through to the heuristic which also fails since '-a-real-dir' is not registered.
    assert rec.action == "error"
