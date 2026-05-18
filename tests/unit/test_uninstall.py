"""E25 — uninstall + revert + markdown export.

Three surfaces under test:

* :class:`runaway_context.uninstall.InstallManifest` — captures what an
  install touched.
* :func:`runaway_context.uninstall.export_to_markdown` — portable export.
* :func:`runaway_context.uninstall.uninstall` — orchestration.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from runaway_context import Client, init as init_mod
from runaway_context.cli import main as cli_main
from runaway_context.migrate import migrate
from runaway_context.uninstall import (
    InstallManifest,
    archive_install,
    capture_install_manifest,
    export_to_markdown,
    remove_install,
    revert_modified_files,
    uninstall,
)


pytestmark = pytest.mark.feature


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@pytest.fixture
def ready_install(tmp_path):
    """Provide a freshly-migrated install with one slug, one lesson, one chunk.

    The install lives in a child of *tmp_path* so tests can place archive
    or markdown-export dirs alongside it without those getting nuked when
    the install dir is removed.
    """
    install = tmp_path / "install"
    install.mkdir()
    migrate(
        install / "knowledge.db",
        install / "sessions.db",
        install / "metrics.db",
        backup=False,
    )
    c = Client(install_dir=install)
    c.register_slug("tooling", description="general tooling work")
    c.log_lesson(
        title="never trust mocks blindly",
        project_tags=["tooling"],
        what_happened="mock returned stale data",
        prevention_rule="prefer integration tests",
    )
    c.propose_knowledge(
        project="tooling",
        topic="ci-cache-key",
        title="CI cache invalidation",
        body="bump the cache key when sqlite3 version changes",
    )
    return install


def test_capture_install_manifest_writes_file(ready_install):
    """The manifest file lands at <install_dir>/install_manifest.json."""
    path = capture_install_manifest(ready_install, tier="T2")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["runaway_version"]
    assert data["install_dir"] == str(ready_install)
    assert data["tier_at_install"] == "T2"
    assert any("knowledge.db" in p for p in data["created_paths"])


def test_install_manifest_round_trip(ready_install):
    """Save → load returns equal data."""
    saved = capture_install_manifest(ready_install, tier="T1")
    loaded = InstallManifest.load(ready_install)
    assert loaded is not None
    assert loaded.install_dir == str(ready_install)
    assert loaded.tier_at_install == "T1"


def test_install_manifest_load_missing_returns_none(tmp_path):
    """Missing manifest → None (not an exception)."""
    assert InstallManifest.load(tmp_path) is None


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def test_export_to_markdown_writes_lessons_and_chunks(ready_install, tmp_path):
    """Lessons + chunks land under lessons/ and chunks/ as .md files."""
    out = tmp_path / "export"
    counts = export_to_markdown(ready_install, out)
    assert counts["lessons"] == 1
    assert counts["chunks"] == 1
    lesson_files = list((out / "lessons").rglob("LL-*.md"))
    chunk_files = list((out / "chunks").rglob("*.md"))
    index = out / "index.md"
    assert lesson_files, "no lesson .md files written"
    assert chunk_files, "no chunk .md files written"
    assert index.exists()
    # Lesson markdown carries the title in frontmatter
    text = lesson_files[0].read_text()
    assert "title:" in text
    assert "never trust mocks blindly" in text


def test_export_to_markdown_refuses_non_empty_target(ready_install, tmp_path):
    """Writing into a populated dir without overwrite=True refuses."""
    out = tmp_path / "export"
    out.mkdir()
    (out / "leftover.txt").write_text("preexisting")
    with pytest.raises(FileExistsError):
        export_to_markdown(ready_install, out)


def test_export_to_markdown_overwrite_proceeds(ready_install, tmp_path):
    """overwrite=True allows writing into a populated dir."""
    out = tmp_path / "export"
    out.mkdir()
    (out / "leftover.txt").write_text("preexisting")
    counts = export_to_markdown(ready_install, out, overwrite=True)
    assert counts["lessons"] == 1
    # The pre-existing file should still be there (overwrite is permissive,
    # not a wipe).
    assert (out / "leftover.txt").exists()


def test_export_to_markdown_handles_empty_install(tmp_path):
    """No knowledge.db → counts of 0, no crash."""
    counts = export_to_markdown(tmp_path, tmp_path / "out")
    assert counts == {"lessons": 0, "chunks": 0, "projects": 0}


# ---------------------------------------------------------------------------
# Archive + remove
# ---------------------------------------------------------------------------


def test_archive_install_creates_tarball(ready_install, tmp_path):
    """archive_install produces a .tar.gz containing the install dir."""
    archive_dir = tmp_path / "archives"
    out = archive_install(ready_install, archive_dir)
    assert out.exists()
    assert out.suffix == ".gz"
    with tarfile.open(str(out)) as tf:
        names = tf.getnames()
    assert any("knowledge.db" in n for n in names)
    # Even with no external artifacts, the manifest member is present.
    assert "EXTERNAL_FILES.json" in names


def test_archive_install_sweeps_up_external_briefs(ready_install, tmp_path):
    """A brief file at project_context_card.md_path is added under external/ in the tarball.

    Refuses:
        Files under system roots.
    """
    import sqlite3

    # Place a project brief on a user-controlled path (outside install_dir).
    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()
    brief = proj_dir / "CLAUDE.md"
    brief.write_text("# myproject brief\n\nhand-edited content\n")

    # Register the path on the project_context_card.
    conn = sqlite3.connect(str(ready_install / "knowledge.db"))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, md_path, md_line_cap) VALUES (?, ?, ?)",
            ("tooling", str(brief), 150),
        )
        conn.commit()
    finally:
        conn.close()

    out = archive_install(ready_install, tmp_path / "archives")
    with tarfile.open(str(out)) as tf:
        names = tf.getnames()
        external_payload = tf.extractfile("EXTERNAL_FILES.json").read()

    # External brief lives under external/<absolute-path-without-leading-/>
    expected = "external" + str(brief)
    assert expected in names, f"expected {expected} in {names!r}"
    payload = json.loads(external_payload)
    assert str(brief) in payload["external_files"]


def test_archive_install_skips_system_root_paths(ready_install, tmp_path, monkeypatch):
    """A malformed md_path under /etc is silently skipped (safety rail)."""
    import sqlite3
    conn = sqlite3.connect(str(ready_install / "knowledge.db"))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, md_path, md_line_cap) VALUES (?, ?, ?)",
            ("evil", "/etc/passwd", 150),
        )
        conn.commit()
    finally:
        conn.close()
    out = archive_install(ready_install, tmp_path / "archives")
    with tarfile.open(str(out)) as tf:
        names = tf.getnames()
    assert not any("etc/passwd" in n for n in names)
    # Symlink-resolved form must also be excluded — on macOS /etc resolves
    # to /private/etc, so a naive '/etc/'-prefix block would silently miss
    # Path('/etc/passwd').resolve() == '/private/etc/passwd'.
    assert not any("private/etc/passwd" in n for n in names)


def test_archive_install_blocks_macos_resolved_system_paths(ready_install, tmp_path):
    """Pre-resolved /private/* system paths must also be refused.

    Guards against the symlink-bypass class of bugs where a path is sanitised
    in one form (``/etc/passwd``) but the resolver returns a different form
    (``/private/etc/passwd`` on macOS) that the rail forgot about.
    """
    import sqlite3
    conn = sqlite3.connect(str(ready_install / "knowledge.db"))
    try:
        for evil in ("/private/etc/passwd", "/etc/ssh/ssh_host_rsa_key"):
            conn.execute(
                "INSERT OR REPLACE INTO project_context_card "
                "(project, md_path, md_line_cap) VALUES (?, ?, ?)",
                (f"evil_{evil.replace('/', '_')}", evil, 150),
            )
        conn.commit()
    finally:
        conn.close()
    out = archive_install(ready_install, tmp_path / "archives")
    with tarfile.open(str(out)) as tf:
        names = tf.getnames()
    assert not any("etc/passwd" in n for n in names)
    assert not any("ssh_host_rsa_key" in n for n in names)


def test_archive_install_includes_modified_files_from_manifest(ready_install, tmp_path):
    """A file recorded as ``modified_files`` in the manifest is preserved in the tarball."""
    from runaway_context.uninstall import InstallManifest

    # Create a pre-install-like file outside the install dir.
    touched = tmp_path / "touched.json"
    touched.write_text('{"original": true}')

    # Record it in the manifest.
    m = InstallManifest.fresh(ready_install)
    m.modified_files[str(touched)] = '{"pre_install": true}'  # original content
    m.save(ready_install)

    out = archive_install(ready_install, tmp_path / "archives")
    with tarfile.open(str(out)) as tf:
        names = tf.getnames()
    expected = "external" + str(touched)
    assert expected in names


def test_remove_install_refuses_without_confirm(ready_install):
    """remove_install requires confirm=True."""
    with pytest.raises(PermissionError):
        remove_install(ready_install, confirm=False)


def test_remove_install_with_confirm(ready_install):
    """confirm=True removes the install dir."""
    result = remove_install(ready_install, confirm=True)
    assert result["removed"]
    assert not ready_install.exists()


def test_remove_install_keep_db(ready_install):
    """keep_db=True preserves DBs and templates; removes only config/manifest."""
    capture_install_manifest(ready_install)
    # Touch config.json so there's something to clean
    (ready_install / "config.json").write_text("{}")
    result = remove_install(ready_install, confirm=True, keep_db=True)
    assert ready_install.exists()
    assert (ready_install / "knowledge.db").exists()
    assert any("config.json" in p for p in result["removed"])
    assert any("install_manifest.json" in p for p in result["removed"])


def test_remove_install_refuses_home(tmp_path, monkeypatch):
    """Removal refuses to nuke $HOME or root."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(PermissionError):
        remove_install(tmp_path, confirm=True)


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def test_revert_restores_modified_file(tmp_path):
    """A pre-install file we modified gets put back to its original content."""
    pre = tmp_path / "stuff.json"
    pre.write_text('{"original": true}')
    manifest = InstallManifest.fresh(tmp_path)
    manifest.modified_files[str(pre)] = pre.read_text()
    pre.write_text('{"hacked": true}')
    status = revert_modified_files(manifest)
    assert status[str(pre)] == "restored"
    assert json.loads(pre.read_text()) == {"original": True}


def test_revert_removes_file_that_did_not_exist(tmp_path):
    """If we created a file the user didn't have, revert removes it."""
    created = tmp_path / "new_thing"
    created.write_text("we added this")
    manifest = InstallManifest.fresh(tmp_path)
    manifest.modified_files[str(created)] = ""  # empty = did not pre-exist
    status = revert_modified_files(manifest)
    assert status[str(created)] == "removed"
    assert not created.exists()


# ---------------------------------------------------------------------------
# uninstall() orchestration
# ---------------------------------------------------------------------------


def test_uninstall_dry_run_changes_nothing(ready_install):
    """--dry-run leaves everything in place but reports what would happen."""
    report = uninstall(ready_install, dry_run=True, archive=True, confirm=False)
    assert report["dry_run"] is True
    assert ready_install.exists()
    assert (ready_install / "knowledge.db").exists()


def test_uninstall_archive_then_remove(ready_install, tmp_path):
    """Default path: archive to tarball, then remove."""
    archive_dir = tmp_path / "archives"
    report = uninstall(
        ready_install,
        archive=True,
        archive_dir=archive_dir,
        confirm=True,
    )
    assert report["archive_path"]
    assert Path(report["archive_path"]).exists()
    assert not ready_install.exists()


def test_uninstall_with_markdown_export(ready_install, tmp_path):
    """uninstall --export-markdown dumps content before removal."""
    export = tmp_path / "md"
    report = uninstall(
        ready_install,
        export_markdown=export,
        archive=False,
        confirm=True,
    )
    assert export.exists()
    md = report["markdown_export"]
    assert md["lessons"] == 1
    assert md["chunks"] == 1
    assert (export / "index.md").exists()


def test_uninstall_without_confirm_raises(ready_install):
    """uninstall() without confirm=True refuses on the live path."""
    with pytest.raises(PermissionError):
        uninstall(ready_install, archive=False, confirm=False)


def test_cli_export_markdown(ready_install, tmp_path, capsys):
    """`runaway export-markdown --output ...` writes a markdown tree."""
    out = tmp_path / "md"
    rc = cli_main([
        "--install-dir", str(ready_install),
        "export-markdown", "--output", str(out),
    ])
    assert rc == 0
    assert (out / "index.md").exists()
    body = capsys.readouterr().out
    payload = json.loads(body)
    assert payload["lessons"] == 1


def test_cli_uninstall_refuses_without_yes(ready_install, capsys):
    """`runaway uninstall` refuses without --yes on the live path."""
    rc = cli_main([
        "--install-dir", str(ready_install),
        "uninstall",
    ])
    assert rc == 2
    assert ready_install.exists()


def test_cli_uninstall_dry_run(ready_install, capsys):
    """`runaway uninstall --dry-run` does nothing but reports."""
    rc = cli_main([
        "--install-dir", str(ready_install),
        "uninstall", "--dry-run",
    ])
    assert rc == 0
    assert ready_install.exists()


def test_init_writes_install_manifest(tmp_path):
    """init.run(non_interactive=True) writes the install manifest."""
    init_mod.run(install_dir=tmp_path, non_interactive=True)
    assert (tmp_path / "install_manifest.json").exists()


def test_full_round_trip_install_then_undo(tmp_path):
    """Install, log a lesson, uninstall with markdown export — install dir is gone, markdown tree has the lesson."""
    init_mod.run(install_dir=tmp_path, non_interactive=True)
    c = Client(install_dir=tmp_path)
    c.register_slug("tooling")
    c.log_lesson(title="the round trip works", project_tags=["tooling"])
    export = tmp_path.parent / "export_md"
    archive_dir = tmp_path.parent / "archives"
    report = uninstall(
        tmp_path,
        export_markdown=export,
        archive=True,
        archive_dir=archive_dir,
        confirm=True,
    )
    assert not tmp_path.exists()
    assert (export / "index.md").exists()
    assert report["archive_path"]
    assert Path(report["archive_path"]).exists()
    # The exported markdown contains our lesson title
    lesson_files = list((export / "lessons").rglob("LL-*.md"))
    assert lesson_files
    assert "the round trip works" in lesson_files[0].read_text()
