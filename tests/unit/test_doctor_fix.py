"""Unit tests for doctor_fix interactive fix flow + revert."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runaway_context import doctor_fix as df


pytestmark = pytest.mark.feature


@pytest.fixture(autouse=True)
def isolate_backup_root(tmp_path, monkeypatch):
    """Point doctor-backups at tmp_path so we don't touch ~/.runaway/."""
    monkeypatch.setattr(df, "_BACKUP_ROOT", tmp_path / "doctor-backups")


def test_rewrite_constitution_block_replaces_legacy_section():
    src = (
        "# Header\n"
        "## Session Memory\n"
        "- Database: `_knowledge/sessions.db`\n"
        "- CLI: `python3 _knowledge/sessions.py`\n"
        "- run `python3 _knowledge/sessions.py --save --auto` at end of sessions\n"
        "\n"
        "## Other Section\n"
        "untouched content\n"
    )
    out = df._rewrite_constitution_block(src)
    assert out is not None
    assert "sessions.py" not in out
    assert "runaway brief" in out
    assert "## Other Section" in out
    assert "untouched content" in out


def test_rewrite_constitution_returns_none_when_clean():
    src = "# clean file\nno sessions.py references here\n"
    assert df._rewrite_constitution_block(src) is None


def test_rewrite_memory_md_strips_fetch_detail_block():
    src = (
        "# Auto Memory\n"
        "- LL#1 — first\n"
        "\n"
        "## How to fetch detail\n"
        "Use `python3 ~/_knowledge/sessions.py --ks-get N`\n"
        "Use `python3 ~/_knowledge/sessions.py --ll-get N`\n"
    )
    out = df._rewrite_memory_md(src)
    assert out is not None
    assert "sessions.py" not in out
    assert "runaway search" in out
    assert "LL#1" in out  # other content survives


def test_fix_mcp_creates_new_file(tmp_path: Path, monkeypatch):
    """fix_mcp writes the runaway-context entry into a fresh mcp.json."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    manifest = df.fix_mcp(runaway_cli="/fake/runaway", assume_yes=True)
    assert manifest is not None

    mcp = fake_home / ".claude" / "mcp.json"
    assert mcp.exists()
    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert "runaway-context" in data["mcpServers"]
    assert data["mcpServers"]["runaway-context"]["command"] == "/fake/runaway"


def test_fix_mcp_merges_without_clobber(tmp_path: Path, monkeypatch):
    """An existing mcpServers entry is preserved when we merge."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    mcp = fake_home / ".claude" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(json.dumps({"mcpServers": {"some-other": {"command": "x"}}}))

    df.fix_mcp(runaway_cli="/fake/runaway", assume_yes=True)

    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert "some-other" in data["mcpServers"]
    assert "runaway-context" in data["mcpServers"]


def test_fix_mcp_idempotent_when_already_wired(tmp_path: Path, monkeypatch, capsys):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    mcp = fake_home / ".claude" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(json.dumps({"mcpServers": {"runaway-context": {"command": "x"}}}))

    manifest = df.fix_mcp(assume_yes=True)
    assert manifest is None
    out = capsys.readouterr().out
    assert "already wired" in out


def test_fix_capture_hook_adds_stop_hook(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": []}}))

    fake_script = tmp_path / "capture.sh"
    fake_script.write_text("#!/bin/bash\nexit 0\n")
    df.fix_capture_hook(capture_script=fake_script, assume_yes=True)

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert "hooks" in data
    stop_hooks = data["hooks"]["Stop"]
    assert any(
        str(fake_script.resolve()) in h.get("command", "")
        for group in stop_hooks for h in group.get("hooks", [])
    )


def test_revert_restores_files(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Run a fix
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir()
    original = json.dumps({"original": True, "permissions": {}})
    settings.write_text(original)
    fake_script = tmp_path / "capture.sh"
    fake_script.write_text("")
    df.fix_capture_hook(capture_script=fake_script, assume_yes=True)
    assert settings.read_text() != original

    # Revert it
    batches = df.list_batches()
    assert batches  # at least one
    n = df.revert(batches[0])
    assert n >= 1
    # Settings restored
    assert settings.read_text() == original


def test_list_batches_orders_newest_first(tmp_path: Path):
    """list_batches returns timestamps sorted descending."""
    root = df._BACKUP_ROOT
    root.mkdir(parents=True)
    for ts in ("20260101T000000Z", "20260601T000000Z", "20260301T000000Z"):
        (root / ts).mkdir()
        (root / ts / "manifest.json").write_text("{}")
    batches = df.list_batches()
    assert batches == ["20260601T000000Z", "20260301T000000Z", "20260101T000000Z"]
