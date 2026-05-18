"""Tests for runaway_context.memory_ingest (v3.2.0)."""
from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

from runaway_context import memory_ingest as mi

pytestmark = pytest.mark.feature


def _write_memory_md(path: Path, *, mtype: str, name: str, description: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # NOTE: don't use textwrap.dedent here — multi-line `body` values defeat
    # dedent's common-prefix detection and leave the frontmatter indented.
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        f"metadata:\n  type: {mtype}\n---\n{body}\n"
    )


def _make_claude_tree(tmp_path: Path, proj_dirname: str = "-var-www-html") -> Path:
    """Build a fake ~/.claude/projects/<proj>/memory/ tree."""
    root = tmp_path / "claude" / "projects"
    (root / proj_dirname / "memory").mkdir(parents=True)
    return root


def test_discover_memory_dirs_finds_all(tmp_path: Path):
    root = _make_claude_tree(tmp_path, "-a-b")
    (root / "-c-d" / "memory").mkdir(parents=True)
    dirs = mi.discover_memory_dirs(claude_projects_root=root)
    assert len(dirs) == 2
    assert all(d.name == "memory" for d in dirs)


def test_discover_memory_mds_excludes_index(tmp_path: Path):
    root = _make_claude_tree(tmp_path)
    memdir = root / "-var-www-html" / "memory"
    (memdir / "MEMORY.md").write_text("- pointer\n")
    _write_memory_md(memdir / "feedback_foo.md", mtype="feedback",
                     name="foo", description="d", body="b")
    out = mi.discover_memory_mds(memdir)
    assert [p.name for p in out] == ["feedback_foo.md"]


def test_parse_memory_md_roundtrip(tmp_path: Path):
    p = tmp_path / "u.md"
    _write_memory_md(p, mtype="user", name="me", description="role: dev",
                     body="user is a developer\nmulti line\n")
    parsed = mi.parse_memory_md(p)
    assert parsed["frontmatter"]["name"] == "me"
    assert parsed["frontmatter"]["description"] == "role: dev"
    assert parsed["frontmatter"]["metadata"]["type"] == "user"
    assert "developer" in parsed["body"]


def test_parse_memory_md_rejects_no_frontmatter(tmp_path: Path):
    p = tmp_path / "bare.md"
    p.write_text("just a body\n")
    with pytest.raises(ValueError):
        mi.parse_memory_md(p)


def test_rewrite_as_pointer_shape(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("old\n")
    mi.rewrite_as_pointer(p, table="lessons_learned", row_id=42,
                          name="x", description="desc")
    text = p.read_text()
    assert "type: pointer" in text
    assert "db_table: lessons_learned" in text
    assert "db_row_id: 42" in text


def test_ingest_one_inserts_lesson_and_rewrites(tmp_path, client):
    client.register_slug("tooling")
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    p = memdir / "feedback_no_mocks.md"
    _write_memory_md(p, mtype="feedback", name="no-mocks",
                     description="don't mock the DB in tests",
                     body="Reason: prior migration burn.")

    rec = mi.ingest_one(p, client, dry_run=False)
    assert rec.action == "inserted", rec.detail
    assert rec.table == "lessons_learned"
    assert rec.row_id is not None

    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT title, source FROM lessons_learned WHERE id = ?",
            (rec.row_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "don't mock the DB in tests"
    assert row[1].startswith("memory:")
    # File is now a pointer
    assert "type: pointer" in p.read_text()


def test_ingest_one_inserts_chunk_for_reference_type(tmp_path, client):
    client.register_slug("tooling")
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    p = memdir / "reference_grafana.md"
    _write_memory_md(p, mtype="reference", name="graf-board",
                     description="grafana latency dashboard",
                     body="URL: grafana.internal/d/api-latency")
    rec = mi.ingest_one(p, client)
    assert rec.action == "inserted"
    assert rec.table == "knowledge_chunks"


def test_ingest_one_skips_pointer_stubs(tmp_path, client):
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    p = memdir / "ptr.md"
    mi.rewrite_as_pointer(p, table="knowledge_chunks", row_id=1, name="n", description="d")
    rec = mi.ingest_one(p, client)
    assert rec.action == "already_pointer"


def test_ingest_one_errors_on_unknown_project(tmp_path, client):
    # Don't register the slug.
    root = _make_claude_tree(tmp_path, "-unknown-proj")
    memdir = root / "-unknown-proj" / "memory"
    p = memdir / "feedback_x.md"
    _write_memory_md(p, mtype="feedback", name="x", description="d", body="b")
    rec = mi.ingest_one(p, client)
    assert rec.action == "error"
    assert "no canonical slug" in (rec.detail or "")


def test_ingest_one_links_existing_match(tmp_path, client):
    client.register_slug("tooling")
    # Pre-seed a lesson the importer will match by (project, title).
    existing_id = client.log_lesson(
        title="known-title", project_tags=["tooling"], severity="info",
    )
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    p = memdir / "feedback_dup.md"
    _write_memory_md(p, mtype="feedback", name="dup",
                     description="known-title", body="body")
    rec = mi.ingest_one(p, client)
    assert rec.action == "linked"
    assert rec.row_id == existing_id
    assert "type: pointer" in p.read_text()


def test_ingest_all_dry_run_writes_nothing(tmp_path, client):
    client.register_slug("tooling")
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    p = memdir / "feedback_x.md"
    _write_memory_md(p, mtype="feedback", name="x", description="desc-x", body="b")

    report = mi.ingest_all(client, claude_projects_root=root, dry_run=True)
    assert report.dry_run is True
    assert report.counts().get("inserted") == 1
    # File untouched
    assert "type: feedback" in p.read_text()
    # DB untouched
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM lessons_learned WHERE title='desc-x'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_ingest_all_is_idempotent(tmp_path, client):
    client.register_slug("tooling")
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    for i, t in enumerate(["feedback", "reference", "rule"]):
        p = memdir / f"{t}_{i}.md"
        _write_memory_md(p, mtype=t, name=f"n{i}", description=f"d{i}", body=f"b{i}")

    r1 = mi.ingest_all(client, claude_projects_root=root)
    assert r1.counts().get("inserted") == 3
    # Second sweep: every file is now a pointer
    r2 = mi.ingest_all(client, claude_projects_root=root)
    assert r2.counts().get("already_pointer") == 3
    assert "inserted" not in r2.counts()


def test_cli_memory_ingest_dry_run(tmp_path, client, monkeypatch):
    """End-to-end through the CLI handler."""
    client.register_slug("tooling")
    root = _make_claude_tree(tmp_path, "-tooling")
    memdir = root / "-tooling" / "memory"
    p = memdir / "feedback_e.md"
    _write_memory_md(p, mtype="feedback", name="e", description="cli-desc", body="b")

    from runaway_context import cli as cli_mod
    # Capture stdout
    import io
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = cli_mod.main([
        "--install-dir", str(client.install_dir),
        "memory", "ingest", "--dry-run",
        "--claude-root", str(root),
    ])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["dry_run"] is True
    assert out["counts"].get("inserted") == 1
