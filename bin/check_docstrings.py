#!/usr/bin/env python3
"""HR-14 enforcer — every public symbol has a non-empty docstring.

Walks src/runaway_context/ ast-style. A symbol is public iff its name does
not start with `_`. Public symbols include module-level functions, classes,
class methods, and async functions.

Returns:
    Exit 0 if every public symbol has a docstring. Exit 1 with line refs if not.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def collect_missing(path: Path) -> list:
    text = path.read_text()
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [(path, e.lineno or 0, f"syntax error: {e}")]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _is_public(node.name):
                continue
            doc = ast.get_docstring(node)
            if not doc or not doc.strip():
                out.append((path, node.lineno, f"{type(node).__name__} {node.name}"))
    return out


def main() -> int:
    root = Path(__file__).resolve().parent.parent / "src" / "runaway_context"
    if not root.exists():
        print("HR-14 OK — no src yet.")
        return 0
    missing = []
    for f in sorted(root.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        # _-prefixed files are internal; their *public* surface (non-_ names) is
        # still required to have docstrings.
        missing.extend(collect_missing(f))
    if missing:
        print("HR-14 violation: public symbols missing docstrings:")
        for f, lineno, what in missing:
            print(f"  {f}:{lineno} {what}")
        return 1
    print("HR-14 OK — all public symbols have docstrings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
