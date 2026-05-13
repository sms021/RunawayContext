"""HR-12 contract tests — every public surface has a test.

HR-12: each Client public method and each MCP tool must be referenced from at
least one test file. We do *not* assert implementation details here — only
that the test suite has a hook for every surface element.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from runaway_context import mcp_server
from runaway_context.client import Client

pytestmark = pytest.mark.contract


def _public_client_methods():
    return [
        name
        for name, member in inspect.getmembers(Client)
        if not name.startswith("_") and inspect.isfunction(member)
    ]


def _gather_test_corpus() -> str:
    """Concatenate every test file's text once (cheap given the suite size)."""
    repo_root = Path(__file__).resolve().parent.parent
    chunks = []
    for path in repo_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_hr_12_every_client_method_has_a_test() -> None:
    """HR-12: every public Client method appears in at least one test file."""
    corpus = _gather_test_corpus()
    missing = []
    for name in _public_client_methods():
        if name in corpus:
            continue
        missing.append(name)
    assert not missing, (
        f"HR-12: Client methods without test coverage: {missing}"
    )


def test_hr_12_every_mcp_tool_has_a_test() -> None:
    """HR-12: every MCP tool name appears in at least one test file."""
    corpus = _gather_test_corpus()
    missing = []
    for tool in mcp_server._TOOLS:
        name = tool["name"]
        if name in corpus:
            continue
        missing.append(name)
    assert not missing, (
        f"HR-12: MCP tools without test coverage: {missing}"
    )
