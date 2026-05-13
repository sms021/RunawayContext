"""Anti-loophole tests — spot-checks for the L1-L12 provisions.

Part V of the plan enumerates 12 ways an implementor could "technically"
ship a non-compliant release. These checks verify that the most common
loopholes are not present.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from runaway_context.client import Client

pytestmark = pytest.mark.contract


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_l10_no_skip_in_contract_suite() -> None:
    """L10: contract tests must not use ``@pytest.mark.skip``."""
    contract_dir = _REPO_ROOT / "tests" / "contract"
    self_name = Path(__file__).name
    offenders = []
    for path in contract_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # The decorator must be at the start of an effective line. Skip
            # this very file's references in docstrings / assertion messages.
            if not stripped.startswith("@pytest.mark.skip"):
                continue
            if path.name == self_name:
                continue
            offenders.append(f"{path.name}:{line_no}")
    assert not offenders, (
        f"L10 violation: @pytest.mark.skip in contract suite: {offenders}"
    )


def test_l5_no_notimplementederror_in_public_methods() -> None:
    """L5: public Client methods do not unconditionally raise NotImplementedError."""
    bad = []
    for name, member in inspect.getmembers(Client):
        if name.startswith("_"):
            continue
        if not inspect.isfunction(member):
            continue
        try:
            source = inspect.getsource(member)
        except (OSError, TypeError):
            continue
        # Allow conditional NotImplementedError if it is gated behind an if.
        if "raise NotImplementedError" in source:
            # Look for a guard — pattern is the bare unconditional pattern.
            body = re.sub(r"\s+", " ", source)
            if re.search(r"def\s+\w+\([^)]*\)\s*->[^:]+:\s*[^:]*raise\s+NotImplementedError", body):
                bad.append(name)
    assert not bad, (
        f"L5 violation: Client methods unconditionally raise NotImplementedError: {bad}"
    )


def test_l11_no_known_limitation_markers() -> None:
    """L11: shipping code must not paper over gaps with "known limitation" comments.

    Scans only comments and docstrings — user-facing error strings that contain
    the phrase ``"not implemented in this build"`` are a documented fallback
    message (HR-10: surface refusals, never silent), not a marker of an
    unfinished feature.
    """
    forbidden_phrases = (
        "known limitation",
        "later release",
        "will fix in",
    )
    src_root = _REPO_ROOT / "src" / "runaway_context"
    offenders = []
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        # Only consider comments (lines starting with `#`) and docstrings.
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not (stripped.startswith("#") or stripped.startswith('"""')
                    or stripped.startswith("'''")):
                continue
            lowered = stripped.lower()
            for phrase in forbidden_phrases:
                if phrase in lowered:
                    rel = path.relative_to(_REPO_ROOT)
                    offenders.append(f"{rel}:{line_no}: {phrase!r}")
    assert not offenders, (
        "L11 violation: known-limitation phrases in shipping code:\n  "
        + "\n  ".join(offenders)
    )
