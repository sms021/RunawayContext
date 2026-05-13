"""Internal slug helpers — pure functions, no DB I/O.

Public slug lifecycle (CLI, registry operations) lives in slugs_lifecycle.py.
This module is just the path → slug derivation and validation primitives.

Refuses:
    Empty / whitespace / clearly-junk paths (the v2 miner safety net).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

_JUNK_FRAGMENTS = (
    "node_modules", "vendor/", ".bak", ".backup.", "__pycache__",
    ".git/", ".venv/", "venv/", "/tmp/", ".cache/",
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,63}$")


def is_valid_slug_format(slug: str) -> bool:
    """Return True iff slug matches the canonical format (lowercase, snake, ≤64 chars)."""
    if not isinstance(slug, str):
        return False
    return bool(_SLUG_RE.fullmatch(slug))


def slug_from_path(path: str) -> Optional[str]:
    """Derive a canonical slug from a filesystem path. Junk → None.

    Returns:
        Lowercased, snake_cased final non-junk segment, or None.

    Refuses:
        Paths inside node_modules, vendor/, backups, caches.
    """
    if not path:
        return None
    p = str(path)
    for junk in _JUNK_FRAGMENTS:
        if junk in p:
            return None
    base = Path(p).name.lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    if not base:
        return None
    if not _SLUG_RE.fullmatch(base):
        return None
    return base


def coalesce_slugs(slugs: Iterable[str]) -> list:
    """Dedupe + sort while preserving lowercase format. Invalid slugs are dropped."""
    return sorted({s for s in slugs if isinstance(s, str) and is_valid_slug_format(s)})
