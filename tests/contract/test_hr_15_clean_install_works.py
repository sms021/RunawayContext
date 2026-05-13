"""HR-15 contract tests — a clean install completes end-to-end.

HR-15: ``runaway init --non-interactive`` from a fresh directory must
produce a usable install. The full Docker version is documented in
docs/specs/AIR_GAPPED_INSTALL.md; this test exercises the same flow in a
process-local tmpdir for CI speed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


def test_hr_15_clean_install_smoke(tmp_install: Path) -> None:
    """HR-15: init.run + Client + slug + log_lesson + regen + audit_verify chain.

    Docker version: ``docker run --rm -v $(pwd):/work python:3.11-slim
    bash -c 'pip install -e /work && runaway init --non-interactive &&
    runaway slug register tooling && runaway log-lesson ...'`` — outside
    the scope of this in-process smoke (no Docker available in pytest).
    """
    from runaway_context import init as init_mod
    from runaway_context.client import Client

    cfg = init_mod.run(install_dir=tmp_install, non_interactive=True)
    assert cfg.install_dir == tmp_install
    assert (tmp_install / "knowledge.db").exists()
    assert (tmp_install / "config.json").exists()

    client = Client(install_dir=tmp_install)
    client.register_slug("tooling")
    lesson_id = client.log_lesson(
        title="HR-15 smoke",
        project_tags=["tooling"],
        what_happened="clean-install smoke test",
        prevention_rule="don't break the install",
        severity="info",
    )
    assert lesson_id > 0

    # Wire up a context card so regen has something to write.
    import sqlite3
    import json

    md_path = tmp_install / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(tmp_install / "knowledge.db"))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling", json.dumps([lesson_id]), "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()

    result = client.regen_brief("tooling")
    assert result["written"] is True
    assert Path(result["md_path"]).exists()

    ok, bad_id, reason = client.audit_verify()
    assert ok, f"audit chain broken: {bad_id} {reason}"
