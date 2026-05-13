"""HR-1 contract tests — no surprise network egress.

HR-1: only allowlisted modules may import network libraries, and even those
must be gated behind an explicit Config flag. The default ``Client``
construction must refuse to start when a non-allowlisted egress path is loaded.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


_NETWORK_IMPORT_RE = re.compile(
    r"^\s*(import|from)\s+(socket|urllib|http|requests|httpx|aiohttp)\b",
    re.MULTILINE,
)

# Modules permitted to import network libraries. Mirrors the allowlist in
# runaway_context.client._NETWORK_MODULES and the federation worker stub.
_ALLOWED_NETWORK_PATHS = {
    Path("embeddings", "providers", "openai.py"),
    Path("embeddings", "providers", "voyage.py"),
    Path("embeddings", "providers", "ollama.py"),
    Path("metrics", "otlp_exporter.py"),
    # federation/ refresh worker (and any sibling) is allowed; see HR-1 spec.
}


def _iter_py_files(root: Path):
    """Yield every .py file under *root* skipping caches."""
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_hr_01_no_network_imports_in_default_modules(src_root: Path) -> None:
    """HR-1: only allowlisted modules import socket/http/urllib/requests/httpx/aiohttp."""
    offenders = []
    for path in _iter_py_files(src_root):
        rel = path.relative_to(src_root)
        if rel in _ALLOWED_NETWORK_PATHS:
            continue
        if rel.parts and rel.parts[0] == "federation":
            # The federation/ subtree is the documented opt-in T4 surface.
            continue
        text = path.read_text(encoding="utf-8")
        for m in _NETWORK_IMPORT_RE.finditer(text):
            offenders.append((str(rel), m.group(0).strip()))
    assert not offenders, (
        "HR-1 violation: network imports outside the allowlist:\n  "
        + "\n  ".join(f"{f}: {line}" for f, line in offenders)
    )


def test_hr_01_runtime_self_check_runs(client) -> None:
    """HR-1: a default Client construction passes ``_hr1_self_check``."""
    # Construction in the ``client`` fixture would have raised
    # ``NetworkEgressBlocked`` if the self-check tripped.
    assert client.install_dir.exists()


def test_hr_01_openai_module_blocked_by_default(tmp_install: Path) -> None:
    """HR-1: importing the openai provider with no opt-in flag raises ImportError."""
    # The provider performs a top-level guard:  unless Config.network_opt_in
    # has openai=True the module body raises ImportError.
    sys.modules.pop("runaway_context.embeddings.providers.openai", None)
    with pytest.raises(ImportError):
        import runaway_context.embeddings.providers.openai  # noqa: F401
