"""E9 — package restructure to src/runaway_context + entry point."""
from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.feature


def test_e09_runaway_context_importable():
    """E9: import runaway_context succeeds and exposes __version__."""
    mod = importlib.import_module("runaway_context")
    assert hasattr(mod, "__version__")
    assert mod.__version__.startswith("3.")


def test_e09_client_re_exported():
    """E9: runaway_context.Client is the canonical Python entry point."""
    from runaway_context import Client
    assert Client is not None


def test_e09_errors_re_exported():
    """E9: typed exceptions live under the top-level package."""
    from runaway_context import (
        AuditChainBroken,
        BriefBudgetExceeded,
        ConflictReported,
        HardDeleteRefused,
        InvalidProjectSlug,
        MaturationApprovalRequired,
        MigrationAborted,
        NetworkEgressBlocked,
        RunawayContextError,
        TierGateFailed,
    )
    # All inherit from RunawayContextError
    for exc in (
        AuditChainBroken, BriefBudgetExceeded, ConflictReported, HardDeleteRefused,
        InvalidProjectSlug, MaturationApprovalRequired, MigrationAborted,
        NetworkEgressBlocked, TierGateFailed,
    ):
        assert issubclass(exc, RunawayContextError)


def test_e09_cli_module_entry_point_runs(tmp_install):
    """E9: ``python -m runaway_context.cli --help`` exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "runaway_context.cli", "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "runaway" in proc.stdout.lower()
