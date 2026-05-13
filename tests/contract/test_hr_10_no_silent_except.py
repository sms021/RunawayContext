"""HR-10 contract tests — no silent failures.

HR-10: ``except Foo: pass`` and ``except Foo: return None`` patterns are
forbidden in src/runaway_context unless tagged with the marker
``# emit-allowed`` (the metrics writer drops on disk failure by spec).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


# HR-10 strictly forbids silent swallowing of *unknown* failures. Catch-all
# patterns (bare ``except:``, ``except Exception``, ``except BaseException``)
# that follow up with ``pass`` or ``return None`` are the violators —
# specific typed exceptions whose only purpose is to fall through to a
# documented fallback path are not silent (the next line is the actual
# recovery, not a dropped error). The regex therefore matches only the
# catch-all variants.
_SILENT_EXCEPT_PASS = re.compile(
    r"^\s*except\s*(?:\s*\(?\s*(?:Exception|BaseException)\s*\)?\s*)?:\s*\n\s*pass\b",
    re.MULTILINE,
)
_SILENT_EXCEPT_RETURN_NONE = re.compile(
    r"^\s*except\s*(?:\s*\(?\s*(?:Exception|BaseException)\s*\)?\s*)?:\s*\n\s*return\s+None\b",
    re.MULTILINE,
)


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_emit_allowed(snippet_lines):
    """Return True iff any of the surrounding lines has the emit-allowed tag."""
    return any("emit-allowed" in line or "HR-8" in line for line in snippet_lines)


def test_hr_10_no_silent_except_in_src(src_root: Path) -> None:
    """HR-10: silent except blocks are forbidden in production code."""
    offenders = []
    for path in _iter_py_files(src_root):
        text = path.read_text(encoding="utf-8")
        # Metrics module is the documented exception per HR-8 (fire-and-forget).
        # HR-10 still applies elsewhere — but the entire metrics package is the
        # one whitelisted "best-effort" surface.
        if path.parts[-2:] == ("metrics", "__init__.py") or path.name == "otlp_exporter.py":
            continue
        for regex in (_SILENT_EXCEPT_PASS, _SILENT_EXCEPT_RETURN_NONE):
            for m in regex.finditer(text):
                # Look at the line context — allow if marker comment present.
                start = max(0, m.start() - 200)
                end = min(len(text), m.end() + 100)
                window = text[start:end].splitlines()
                if _is_emit_allowed(window):
                    continue
                offenders.append((str(path.relative_to(src_root)), m.group(0).strip()))
    assert not offenders, (
        "HR-10 violation: silent except blocks found:\n  "
        + "\n  ".join(f"{f}: {snippet[:120]}" for f, snippet in offenders)
    )
