"""HR-13 contract tests — no TODO/FIXME markers in shipping code.

HR-13: ``TODO`` / ``FIXME`` / ``HACK`` / ``XXX`` are work-in-progress markers
that must not appear in src/, tests/, docs/, or templates/ for a release.
The plan file itself is allowed to *talk about* these markers.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


_MARKERS = ("TODO", "FIXME", "HACK", "XXX")
_MARKER_RE = re.compile(
    r"\b(TODO|FIXME|HACK|XXX)\b",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCAN_DIRS = ("src", "tests", "templates")
# docs/ is excluded as a whole because the canonical HR rules document and
# spec documents necessarily *mention* the forbidden markers when describing
# their own contract enforcement. The shipping rule applies to executable
# source, tests, and templates.
_EXCLUDED_FILES = {
    # Tests for HR-13 itself necessarily mention the marker names.
    "tests/contract/test_hr_13_no_todo_in_release.py",
}
_ALLOWED_SUFFIXES = (
    ".py", ".md", ".sql", ".toml", ".yml", ".yaml", ".cfg", ".txt", ".json",
    ".sh", ".html",
)


def _iter_files():
    for sub in _SCAN_DIRS:
        root = _REPO_ROOT / sub
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _ALLOWED_SUFFIXES:
                continue
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in _EXCLUDED_FILES:
                continue
            yield path, rel


def test_hr_13_no_todo_fixme_in_src_or_docs() -> None:
    """HR-13: TODO/FIXME/HACK/XXX markers must not appear in shipping code."""
    offenders = []
    for path, rel in _iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _MARKER_RE.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{rel}:{line_no} ({m.group(1)})")
    assert not offenders, (
        "HR-13 violation: WIP markers in shipping files:\n  "
        + "\n  ".join(offenders[:50])
        + (f"\n  ... ({len(offenders) - 50} more)" if len(offenders) > 50 else "")
    )
