"""Author identity helpers (HR-6).

`author_id = sha256(install_id + local_username)[:12]`.
Never contains email, hostname, IP, or any other PII.

Refuses:
    Any input that would produce an email-shaped author_id.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import uuid
from pathlib import Path


def get_or_create_install_id(install_dir: Path) -> str:
    """Return the install_id, creating it if missing. Stored at <install_dir>/install_id.

    Returns:
        16-char hex install id.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    path = install_dir / "install_id"
    if path.exists():
        val = path.read_text().strip()
        if val:
            return val
    val = uuid.uuid4().hex[:16]
    path.write_text(val)
    return val


def derive_author_id(install_id: str, username: str) -> str:
    """Compute the 12-char opaque author_id from install_id + username.

    HR-6: opaque, no PII. The hashing is one-way; we never store the inputs
    alongside the output.

    Returns:
        12-char hex string. Never contains '@' or '.'.
    """
    if not install_id or not username:
        raise ValueError("install_id and username are required")
    digest = hashlib.sha256(f"{install_id}|{username}".encode("utf-8")).hexdigest()
    return digest[:12]


def current_author_id(install_dir: Path) -> str:
    """Resolve the author_id for the currently-running user.

    Reads SUDO_USER (preferred) or USER from environment; falls back to getpass.
    Combines with the install_id to produce an opaque, stable id.
    """
    install_id = get_or_create_install_id(install_dir)
    username = os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
    return derive_author_id(install_id, username)
