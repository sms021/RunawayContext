"""HR-14 contract tests — every public surface is documented.

HR-14: Client methods, MCP tools, and CLI handlers must have docstrings that
declare what they Return and what they Raise / Refuse.
"""
from __future__ import annotations

import inspect

import pytest

from runaway_context import cli as cli_mod
from runaway_context import mcp_server
from runaway_context.client import Client

pytestmark = pytest.mark.contract


def _has_required_sections(doc: str) -> bool:
    if not doc:
        return False
    has_returns = "Returns:" in doc
    has_refusal = "Raises:" in doc or "Refuses:" in doc
    return has_returns and has_refusal


def test_hr_14_public_methods_have_docstrings() -> None:
    """HR-14: every public Client method has Returns: + (Raises: or Refuses:)."""
    bad = []
    for name, member in inspect.getmembers(Client):
        if name.startswith("_"):
            continue
        if not inspect.isfunction(member):
            continue
        doc = inspect.getdoc(member) or ""
        if not _has_required_sections(doc):
            bad.append(name)
    assert not bad, (
        f"HR-14: Client methods missing required docstring sections: {bad}"
    )


def test_hr_14_mcp_tools_have_descriptions() -> None:
    """HR-14: every MCP tool has a non-empty description."""
    bad = [t["name"] for t in mcp_server._TOOLS
           if not (t.get("description") or "").strip()]
    assert not bad, f"HR-14: MCP tools missing description: {bad}"


def test_hr_14_cli_command_handlers_have_docstrings() -> None:
    """HR-14: every CLI subcommand handler has a Returns: + Raises:/Refuses:."""
    bad = []
    for name, member in inspect.getmembers(cli_mod):
        if not name.startswith("cmd_"):
            continue
        if not inspect.isfunction(member):
            continue
        doc = inspect.getdoc(member) or ""
        if not _has_required_sections(doc):
            bad.append(name)
    assert not bad, (
        f"HR-14: CLI handlers missing required docstring sections: {bad}"
    )
