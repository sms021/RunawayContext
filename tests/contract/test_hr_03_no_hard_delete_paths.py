"""HR-3 contract tests — no silent hard-delete paths.

HR-3: every destructive write is recoverable. The public Client has no
``hard_delete`` / ``delete_lesson`` / ``delete_chunk`` method. The CLI exposes
exactly one guarded hard-delete path which refuses without both safety flags.
"""
from __future__ import annotations

import inspect
import subprocess
import sys

import pytest

from runaway_context import mcp_server
from runaway_context.client import Client

pytestmark = pytest.mark.contract


_FORBIDDEN_METHOD_NAMES = (
    "hard_delete",
    "delete_lesson",
    "delete_chunk",
    "delete_record",
    "wipe",
    "purge",
)


def test_hr_03_client_has_no_hard_delete_method() -> None:
    """HR-3: Client must not expose any hard-delete method."""
    public_names = [n for n in dir(Client) if not n.startswith("_")]
    offenders = [n for n in public_names if n in _FORBIDDEN_METHOD_NAMES]
    assert not offenders, (
        f"HR-3 violation: Client exposes hard-delete-style method(s) {offenders!r}"
    )


def test_hr_03_soft_delete_only_marks_row(seeded_client) -> None:
    """HR-3: soft_delete marks the row deleted but keeps it queryable."""
    lessons = seeded_client.list_lessons(project="tooling")
    assert lessons, "fixture should seed at least one lesson"
    target_id = lessons[0]["id"]

    seeded_client.soft_delete(
        table="lessons_learned",
        record_id=target_id,
        actor="tester",
        reason="HR-3 spec test",
    )

    # Default list_lessons hides soft-deleted rows.
    after = seeded_client.list_lessons(project="tooling")
    assert all(row["id"] != target_id for row in after)

    # The row still exists in the underlying table.
    import sqlite3
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT id, deleted_at FROM lessons_learned WHERE id = ?",
            (target_id,),
        ).fetchone()
        assert row is not None
        assert row[1] is not None  # deleted_at populated

        snap = conn.execute(
            "SELECT COUNT(*) FROM record_versions "
            "WHERE table_name = 'lessons_learned' AND record_id = ?",
            (target_id,),
        ).fetchone()
        assert int(snap[0]) >= 1, "soft_delete must snapshot into record_versions"
    finally:
        conn.close()


def test_hr_03_cli_hard_delete_requires_both_flags(tmp_install) -> None:
    """HR-3: ``runaway db hard-delete`` exits 2 without both safety flags."""
    # PYTHONPATH=src so the subprocess can import runaway_context even when
    # the package hasn't been pip-installed into the system python (see #13
    # — INSTALL_PROMPT step 5 runs pytest BEFORE verifying step 4 took).
    import os
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    # No flags
    proc = subprocess.run(
        [sys.executable, "-m", "runaway_context.cli",
         "--install-dir", str(tmp_install),
         "db", "hard-delete", "--table", "lessons_learned", "--id", "1"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)


def test_hr_03_mcp_does_not_expose_hard_delete() -> None:
    """HR-3: MCP tool registry has no hard-delete style tool."""
    names = {t["name"] for t in mcp_server._TOOLS}
    forbidden = {n for n in names if any(bad in n for bad in _FORBIDDEN_METHOD_NAMES)}
    assert not forbidden, (
        f"HR-3 violation: MCP exposes hard-delete tool(s) {forbidden!r}"
    )
