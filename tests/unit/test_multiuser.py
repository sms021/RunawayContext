"""Tests for the multi-user provisioner (v3.2.0)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from runaway_context import multiuser as mu

pytestmark = pytest.mark.feature


def _fake_pwd_entry(name="alice", uid=1001, home="/home/alice"):
    """Build a mock pwd struct that quacks like a real pwd entry."""
    m = MagicMock()
    m.pw_name = name
    m.pw_uid = uid
    m.pw_dir = home
    return m


def test_enumerate_excludes_root_by_default(tmp_path):
    fake = [
        _fake_pwd_entry(name="root", uid=0, home=str(tmp_path)),
        _fake_pwd_entry(name="alice", uid=1001, home=str(tmp_path)),
    ]
    (tmp_path / ".claude").mkdir()
    with patch("runaway_context.multiuser.pwd.getpwall", return_value=fake):
        out = mu.enumerate_users()
    assert all(u.username != "root" for u in out)
    assert any(u.username == "alice" for u in out)


def test_enumerate_filters_by_min_uid(tmp_path):
    fake = [
        _fake_pwd_entry(name="systemd", uid=200, home=str(tmp_path)),
        _fake_pwd_entry(name="alice", uid=1001, home=str(tmp_path)),
    ]
    (tmp_path / ".claude").mkdir()
    with patch("runaway_context.multiuser.pwd.getpwall", return_value=fake):
        out = mu.enumerate_users(min_uid=1000)
    assert [u.username for u in out] == ["alice"]


def test_enumerate_requires_claude_dir(tmp_path):
    """Without ~/.claude AND no explicit include, user is filtered out."""
    no_claude = tmp_path / "alice"
    no_claude.mkdir()
    fake = [_fake_pwd_entry(name="alice", uid=1001, home=str(no_claude))]
    with patch("runaway_context.multiuser.pwd.getpwall", return_value=fake):
        assert mu.enumerate_users() == []
    # Explicit include overrides the filter
    with patch("runaway_context.multiuser.pwd.getpwall", return_value=fake):
        out = mu.enumerate_users(extra_usernames=["alice"])
    assert [u.username for u in out] == ["alice"]
    assert out[0].has_claude_dir is False


def test_provision_user_refuses_root(tmp_path):
    profile = mu.UserProfile(username="root", uid=0, home=tmp_path,
                             has_claude_dir=True)
    result = mu.provision_user(profile, dry_run=True)
    assert result.succeeded is False
    assert "root" in (result.skipped_reason or "")


def test_provision_user_skipped_when_no_runaway_bin(tmp_path):
    """When `runaway` is not found, the user is skipped with a clear reason."""
    profile = mu.UserProfile(username="alice", uid=1001, home=tmp_path,
                             has_claude_dir=True)
    with patch("runaway_context.multiuser._can_switch_user", return_value=None), \
         patch("runaway_context.multiuser._runaway_bin_for", return_value=None):
        result = mu.provision_user(profile, dry_run=True)
    assert result.skipped_reason is not None
    assert "not found on PATH" in result.skipped_reason


def test_provision_user_dry_run_skips_subprocess(tmp_path):
    profile = mu.UserProfile(username="alice", uid=1001, home=tmp_path,
                             has_claude_dir=True)
    with patch("runaway_context.multiuser._can_switch_user", return_value=None), \
         patch("runaway_context.multiuser._runaway_bin_for",
               return_value="/usr/local/bin/runaway"), \
         patch("runaway_context.multiuser.subprocess.run") as mrun:
        result = mu.provision_user(profile, dry_run=True)
    assert result.doctor_returncode == 0
    assert "[dry-run]" in (result.doctor_stdout or "")
    mrun.assert_not_called()


def test_provision_user_runs_subprocess(tmp_path):
    profile = mu.UserProfile(username="alice", uid=1001, home=tmp_path,
                             has_claude_dir=True)
    fake_proc = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("runaway_context.multiuser._can_switch_user", return_value=None), \
         patch("runaway_context.multiuser._runaway_bin_for",
               return_value="/usr/local/bin/runaway"), \
         patch("runaway_context.multiuser.subprocess.run",
               return_value=fake_proc) as mrun:
        result = mu.provision_user(profile, dry_run=False)
    assert result.succeeded
    cmd = mrun.call_args.args[0]
    assert "/usr/local/bin/runaway" in cmd
    assert "doctor" in cmd
    assert "--fix-all" in cmd
    assert "--yes" in cmd


def test_provision_all_filters_by_username(tmp_path):
    fake = [
        _fake_pwd_entry(name="alice", uid=1001, home=str(tmp_path)),
        _fake_pwd_entry(name="bob", uid=1002, home=str(tmp_path)),
    ]
    (tmp_path / ".claude").mkdir()
    with patch("runaway_context.multiuser.pwd.getpwall", return_value=fake), \
         patch("runaway_context.multiuser._can_switch_user", return_value=None), \
         patch("runaway_context.multiuser._runaway_bin_for", return_value="/x/runaway"):
        report = mu.provision_all(usernames=["alice"], dry_run=True)
    assert [r.username for r in report.results] == ["alice"]
    assert report.counts() == {"succeeded": 1, "skipped": 0, "failed": 0}


def test_report_to_dict_shape(tmp_path):
    profile = mu.UserProfile(username="alice", uid=1001, home=tmp_path,
                             has_claude_dir=True)
    res = mu.ProvisionResult(username="alice", doctor_returncode=0,
                             doctor_stdout="ok")
    sweep = mu.ProvisionSweepReport(results=[res], dry_run=False)
    d = mu.report_to_dict(sweep)
    assert d["counts"]["succeeded"] == 1
    assert d["results"][0]["username"] == "alice"
