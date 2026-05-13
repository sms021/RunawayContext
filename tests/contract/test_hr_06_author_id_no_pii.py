"""HR-6 contract tests — author_id is opaque, no PII.

HR-6: derive_author_id returns a 12-char hex string with no ``@`` or ``.``.
The DB ``authors`` table has CHECK + trigger guards that reject email-shaped
author_ids and email-shaped display names.
"""
from __future__ import annotations

import sqlite3
import string

import pytest

from runaway_context.identity import derive_author_id, get_or_create_install_id

pytestmark = pytest.mark.contract


def test_hr_06_derive_author_id_no_email_chars(tmp_install) -> None:
    """HR-6: derive_author_id never emits '@' or '.' regardless of input."""
    install_id = get_or_create_install_id(tmp_install)
    for username in ("works.sshort@gmail.com", "sshort@parkway.net", "name.with.dots"):
        author_id = derive_author_id(install_id, username)
        assert "@" not in author_id
        assert "." not in author_id
        assert len(author_id) == 12
        assert all(c in string.hexdigits for c in author_id)


def test_hr_06_db_trigger_rejects_email_like_author_id(fresh_db) -> None:
    """HR-6: authors.author_id CHECK refuses values containing '@' or '.'."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO authors (author_id) VALUES (?)",
                ("user@example.com",),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO authors (author_id) VALUES (?)",
                ("john.doe",),
            )
    finally:
        conn.close()


def test_hr_06_db_trigger_rejects_email_display_name(fresh_db) -> None:
    """HR-6: authors.display_name trigger refuses email-shaped values."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO authors (author_id, display_name) VALUES (?, ?)",
                ("abc123def456", "user@example.com"),
            )
        # Valid insert should succeed.
        conn.execute(
            "INSERT INTO authors (author_id, display_name) VALUES (?, ?)",
            ("abc123def456", "Codeful Friend"),
        )
        conn.commit()
    finally:
        conn.close()
