"""E8 — `runaway init` non-interactive wizard."""
from __future__ import annotations

from pathlib import Path

import pytest

from runaway_context import init as init_mod
from runaway_context.client import Client

pytestmark = pytest.mark.feature


def test_e08_non_interactive_run_produces_install(tmp_install: Path):
    """E8: init.run --non-interactive yields a working knowledge.db + config."""
    cfg = init_mod.run(install_dir=tmp_install, non_interactive=True)
    assert (tmp_install / "knowledge.db").exists()
    assert (tmp_install / "config.json").exists()
    assert cfg.install_dir == tmp_install


def test_e08_post_install_client_usable(tmp_install: Path):
    """E8: after init.run, Client can be constructed and used."""
    init_mod.run(install_dir=tmp_install, non_interactive=True)
    client = Client(install_dir=tmp_install)
    client.register_slug("tooling")
    lid = client.log_lesson(
        title="post-init lesson", project_tags=["tooling"], severity="info",
    )
    assert lid > 0


def test_e08_install_id_persisted(tmp_install: Path):
    """E8: init writes a stable install_id file."""
    init_mod.run(install_dir=tmp_install, non_interactive=True)
    p = tmp_install / "install_id"
    assert p.exists()
    val = p.read_text().strip()
    assert len(val) == 16
