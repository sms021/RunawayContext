"""Multi-user shared-host helper (v3.2.0).

Re-introduces the v2 ``bin/setup_user_protections.sh`` capability as a v3-native
Python module. The goal is to provision RunawayContext for every Claude user on
a shared host: a code-server box, a build agent, a kiosk Pi, anywhere multiple
people share the same machine but each has their own ``~/.claude`` install.

Design
------
Two surfaces:

1. :func:`enumerate_users` — discover candidate users. A "candidate" is a real
   account (UID >= 1000), has a home directory, and either has a
   ``~/.claude`` dir or has been explicitly named on the CLI.

2. :func:`provision_user` — wire a single user's install. The strategy is to
   shell out to ``runaway doctor --fix-all --yes`` under the target user's
   identity (via sudo -u) rather than reimplementing the fix flow. This keeps
   the per-user contract identical to the single-user case and means new
   fixes added to Doctor automatically reach the multi-user path.

Why no symlink farming?
-----------------------
v2's setup_user_protections.sh symlinked shared scripts into each user's
``~/bin``. v3 ships the CLI through ``pip install`` (or the editable repo at
``/var/www/html/_sMs/RunawayContext_v3``), so every user invocation calls the
same code without a symlink farm. We document the recommended install location
but do not enforce it.

Refuses
-------
- Provisioning the root user (UID 0) — Claude Code should never run as root.
- Provisioning when ``sudo -u`` is unavailable and the current process isn't
  already the target user.
- Provisioning when ``runaway`` is not on PATH for the target user.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class UserProfile:
    """One candidate user on the host."""

    username: str
    uid: int
    home: Path
    has_claude_dir: bool


@dataclass
class ProvisionResult:
    """Outcome of provisioning one user."""

    username: str
    skipped_reason: Optional[str] = None
    doctor_returncode: Optional[int] = None
    doctor_stdout: Optional[str] = None
    doctor_stderr: Optional[str] = None
    install_dir: Optional[Path] = None

    @property
    def succeeded(self) -> bool:
        return self.skipped_reason is None and self.doctor_returncode == 0


@dataclass
class ProvisionSweepReport:
    """Aggregate of one :func:`provision_all` sweep."""

    results: List[ProvisionResult] = field(default_factory=list)
    dry_run: bool = False

    def counts(self) -> dict:
        out = {"succeeded": 0, "skipped": 0, "failed": 0}
        for r in self.results:
            if r.skipped_reason is not None:
                out["skipped"] += 1
            elif r.succeeded:
                out["succeeded"] += 1
            else:
                out["failed"] += 1
        return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def enumerate_users(
    *,
    extra_usernames: Optional[Iterable[str]] = None,
    min_uid: int = 1000,
) -> List[UserProfile]:
    """List Claude-eligible users on this host.

    Args:
        extra_usernames: explicit names to include regardless of UID — useful
            for system service accounts that legitimately run Claude.
        min_uid: only auto-include users with UID >= this (default 1000 —
            the usual Linux real-user floor).

    Returns:
        List of :class:`UserProfile`. Ordered by UID.

    Refuses:
        Returning the root user (UID 0); add via ``extra_usernames`` if
        absolutely necessary (but Claude Code as root is discouraged).
    """
    out: List[UserProfile] = []
    explicit = set(extra_usernames or ())
    for pw in pwd.getpwall():
        if pw.pw_uid == 0 and pw.pw_name not in explicit:
            continue
        if pw.pw_uid < min_uid and pw.pw_name not in explicit:
            continue
        home = Path(pw.pw_dir)
        if not home.exists():
            continue
        has_claude = (home / ".claude").exists()
        if not has_claude and pw.pw_name not in explicit:
            continue
        out.append(
            UserProfile(
                username=pw.pw_name, uid=pw.pw_uid,
                home=home, has_claude_dir=has_claude,
            )
        )
    out.sort(key=lambda u: u.uid)
    return out


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def _can_switch_user(target_username: str) -> Optional[str]:
    """Return None if we can run as *target_username*, else a reason string."""
    cur = pwd.getpwuid(os.getuid()).pw_name
    if cur == target_username:
        return None
    if shutil.which("sudo") is None:
        return "sudo not available (need to be the target user or root)"
    if os.getuid() != 0:
        return "must run as root or as the target user to use sudo -u"
    return None


def _runaway_bin_for(username: str) -> Optional[str]:
    """Resolve ``runaway`` on PATH for *username*. None if not found."""
    cur = pwd.getpwuid(os.getuid()).pw_name
    if cur == username:
        return shutil.which("runaway")
    # Probe via `sudo -u <user> which runaway`
    if shutil.which("sudo") is None:
        return None
    try:
        proc = subprocess.run(
            ["sudo", "-n", "-u", username, "which", "runaway"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    found = proc.stdout.strip()
    return found or None


def provision_user(
    profile: UserProfile,
    *,
    dry_run: bool = False,
    extra_doctor_args: Optional[List[str]] = None,
) -> ProvisionResult:
    """Wire one user's RunawayContext install via ``runaway doctor --fix-all --yes``.

    The function shells out under *profile.username*'s identity so the resulting
    state files (`~/.claude/mcp.json`, `~/.claude/settings.json`) get the
    correct ownership.

    Args:
        profile: target user.
        dry_run: report what would happen; run no commands.
        extra_doctor_args: appended to the doctor invocation (e.g.
            ``["--install-dir", "/srv/runaway/sshort"]``).

    Returns:
        :class:`ProvisionResult`. Errors are encoded in the result rather
        than raised so a sweep can continue.

    Refuses:
        Provisioning root (returns skipped_reason). Provisioning when
        ``runaway`` is not on the target user's PATH (returns skipped_reason).
    """
    result = ProvisionResult(username=profile.username, install_dir=None)

    if profile.uid == 0:
        result.skipped_reason = "refused: target user is root"
        return result

    switch_err = _can_switch_user(profile.username)
    if switch_err:
        result.skipped_reason = switch_err
        return result

    runaway_bin = _runaway_bin_for(profile.username)
    if runaway_bin is None:
        result.skipped_reason = (
            f"`runaway` not found on PATH for user {profile.username!r} — "
            "install RunawayContext system-wide or add the install dir to "
            "the user's PATH before re-running."
        )
        return result

    cmd = [runaway_bin, "doctor", "--fix-all", "--yes"]
    if extra_doctor_args:
        cmd.extend(extra_doctor_args)

    cur = pwd.getpwuid(os.getuid()).pw_name
    if cur != profile.username:
        cmd = ["sudo", "-n", "-u", profile.username, "-H"] + cmd

    if dry_run:
        result.doctor_returncode = 0
        result.doctor_stdout = "[dry-run] " + " ".join(cmd)
        return result

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            env={**os.environ, "HOME": str(profile.home)},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.skipped_reason = f"subprocess error: {exc}"
        return result

    result.doctor_returncode = proc.returncode
    result.doctor_stdout = proc.stdout[-4000:]  # cap tail to avoid log floods
    result.doctor_stderr = proc.stderr[-4000:]
    return result


def provision_all(
    *,
    usernames: Optional[Iterable[str]] = None,
    dry_run: bool = False,
    extra_doctor_args: Optional[List[str]] = None,
    min_uid: int = 1000,
) -> ProvisionSweepReport:
    """Discover and provision every eligible user on this host.

    Args:
        usernames: if provided, restrict the sweep to these names (must still
            satisfy the existence/PATH checks).
        dry_run: report only, run nothing.
        extra_doctor_args: passed through to each :func:`provision_user` call.
        min_uid: forwarded to :func:`enumerate_users`.

    Returns:
        :class:`ProvisionSweepReport`.
    """
    profiles = enumerate_users(
        extra_usernames=usernames, min_uid=min_uid,
    )
    if usernames:
        keep = set(usernames)
        profiles = [p for p in profiles if p.username in keep]
    report = ProvisionSweepReport(dry_run=dry_run)
    for p in profiles:
        report.results.append(
            provision_user(p, dry_run=dry_run, extra_doctor_args=extra_doctor_args)
        )
    return report


def report_to_dict(report: ProvisionSweepReport) -> dict:
    """JSON-friendly view of *report*."""
    return {
        "dry_run": report.dry_run,
        "counts": report.counts(),
        "results": [
            {
                "username": r.username,
                "succeeded": r.succeeded,
                "skipped_reason": r.skipped_reason,
                "doctor_returncode": r.doctor_returncode,
                "doctor_stdout_tail": r.doctor_stdout,
                "doctor_stderr_tail": r.doctor_stderr,
            }
            for r in report.results
        ],
    }
