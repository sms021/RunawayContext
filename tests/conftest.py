"""Shared pytest fixtures for RunawayContext v3 contract and feature tests.

Every test in `tests/contract/` and `tests/unit/` gets an isolated install
directory (function-scoped) so the suite never touches the user's real
`~/_knowledge`. The fixtures wire through Config / Client construction so
each test can use the public surface without bootstrapping repetitively.

Refuses:
    Implicit reuse of the user's real install (we always patch RC_KS_DIR).
"""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

# Ensure the in-repo src/ tree is importable when running from the repo
# root (the harness sets PYTHONPATH=src, but belt-and-suspenders covers
# editor-driven runs as well).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"


# HR-1 isolation: provider tests intentionally import network-capable modules
# after toggling the opt-in flag. Once imported, those modules live in
# sys.modules — and Client.__init__'s HR-1 self-check then refuses to start
# unless the flag is still True. Pop them after each test so later tests
# see a clean module graph.
_NETWORK_PROVIDER_MODULES = (
    "runaway_context.embeddings.providers.openai",
    "runaway_context.embeddings.providers.voyage",
    "runaway_context.embeddings.providers.ollama",
    "runaway_context.metrics.otlp_exporter",
    "runaway_context.federation.refresh_worker",
)


@pytest.fixture(autouse=True)
def _hr1_cleanup_network_providers():
    """Pop opt-in network modules from sys.modules after each test (HR-1 hygiene)."""
    yield
    for name in _NETWORK_PROVIDER_MODULES:
        sys.modules.pop(name, None)
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture()
def tmp_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide an isolated install directory and patch RC_KS_DIR for the test.

    Yields:
        Path to the temporary install directory.
    """
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RC_KS_DIR", str(install_dir))
    # Also point metrics DB at a tmp location so we never write to ~/_knowledge
    monkeypatch.setenv("RC_METRICS_DB", str(install_dir / "metrics.db"))
    yield install_dir


@pytest.fixture()
def fresh_db(tmp_install: Path) -> Path:
    """Apply ``migrate.migrate`` to a fresh knowledge.db under ``tmp_install``.

    Returns:
        Path to the migrated knowledge.db.
    """
    from runaway_context.migrate import migrate

    knowledge_db = tmp_install / "knowledge.db"
    sessions_db = tmp_install / "sessions.db"
    metrics_db = tmp_install / "metrics.db"
    migrate(
        knowledge_db=knowledge_db,
        sessions_db=sessions_db,
        metrics_db=metrics_db,
    )
    return knowledge_db


@pytest.fixture()
def client(tmp_install: Path, fresh_db: Path):
    """Return a :class:`runaway_context.Client` bound to the fresh install.

    Returns:
        Live Client pointed at the tmp install directory.
    """
    from runaway_context.client import Client

    return Client(install_dir=tmp_install)


@pytest.fixture()
def seeded_client(client):
    """Return a Client with a registered slug, two lessons, and two chunks.

    Returns:
        Client preloaded with the ``tooling`` slug and seed rows.
    """
    client.register_slug("tooling", description="tooling fixture slug")
    client.log_lesson(
        title="Always check before bulk delete",
        project_tags=["tooling"],
        what_happened="Bulk delete wiped staging data",
        prevention_rule="Always confirm with --dry-run before destructive ops",
        severity="critical",
        blast_radius=4,
        frequency=2,
        reversibility=4,
    )
    client.log_lesson(
        title="Tooling smoke check",
        project_tags=["tooling"],
        what_happened="Smoke test discovered missing dependency",
        prevention_rule="Run smoke test in CI before release",
        severity="warning",
    )
    client.propose_knowledge(
        project="tooling",
        topic="cli-entrypoint",
        title="CLI entrypoint",
        body="The runaway CLI entry point is wired via pyproject.toml.",
        tags=["cli", "entrypoint"],
    )
    client.propose_knowledge(
        project="tooling",
        topic="install-flow",
        title="Install flow",
        body="`runaway init --non-interactive` creates a working install.",
        tags=["install"],
    )
    return client


@pytest.fixture()
def src_root() -> Path:
    """Return the canonical src/runaway_context/ directory.

    Returns:
        Path to the package's src tree.
    """
    return _SRC / "runaway_context"


@pytest.fixture()
def repo_root() -> Path:
    """Return the repository root directory.

    Returns:
        Path to the v3 reference repo root.
    """
    return _REPO_ROOT
