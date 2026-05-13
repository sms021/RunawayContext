"""Audit log + hash-chain verifier (E22, HR-7).

``audit_log`` is append-only (enforced by SQL triggers) and every row's
``this_hash`` is computed from the previous row's ``this_hash`` plus the new
row's payload. Tampering with any row breaks the chain.

This module provides:

* :py:func:`append` — write a new audit_log row inside a caller-managed
  transaction. Returns the new ``this_hash``.
* :py:func:`verify` — walk ``audit_log`` in id order and detect tampering.
* :py:func:`cli_verify` — convenience CLI wrapper that prints + exits.

Hash format (deterministic, reproducible)::

    this_hash = sha256(
        previous_hash
        || '|' || actor
        || '|' || action
        || '|' || target_table
        || '|' || target_id
        || '|' || occurred_at
        || '|' || details_json
    ).hexdigest()[:32]

`details_json = json.dumps(details, sort_keys=True, separators=(',',':'))`
when ``details`` is a dict, otherwise the empty string.

Refuses:
    Computing a hash with no underlying connection (``append``) or running
    verification on a missing DB file (``verify``).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from runaway_context.errors import AuditChainBroken

# Standard separator used inside the hash payload. Keep this stable forever —
# changing it would invalidate every chain in the wild.
_HASH_SEP = "|"
_HASH_LEN = 32


def _stable_details(details: Optional[Dict[str, Any]]) -> str:
    """Encode ``details`` as deterministic JSON for hashing.

    Returns:
        Sort-keyed, no-whitespace JSON string when ``details`` is a non-empty
        dict; otherwise the empty string.

    Refuses:
        Nothing — non-serializable values are coerced via ``default=str``.
    """
    if not details:
        return ""
    return json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(
    previous_hash: Optional[str],
    actor: Optional[str],
    action: str,
    target_table: Optional[str],
    target_id: Optional[int],
    occurred_at: str,
    details_json: str,
) -> str:
    """Compute the chained hash for an audit row.

    Returns:
        The first ``_HASH_LEN`` chars of the sha256 hex digest.

    Refuses:
        Nothing.
    """
    parts = [
        previous_hash or "",
        actor or "",
        action or "",
        target_table or "",
        "" if target_id is None else str(target_id),
        occurred_at or "",
        details_json or "",
    ]
    payload = _HASH_SEP.join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:_HASH_LEN]


def append(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    target_table: Optional[str] = None,
    target_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Append a new ``audit_log`` row and return its ``this_hash``.

    The caller manages the transaction; this function never commits. The
    previous hash is read with ``SELECT ... ORDER BY id DESC LIMIT 1`` on the
    same connection, so reads inside an open transaction see the rows the
    caller has already written.

    Args:
        conn: Open ``sqlite3.Connection`` (already in a transaction is fine).
        actor: opaque ``author_id`` of the actor performing the action.
        action: short verb, e.g. ``'lesson.create'``, ``'maturity.change'``.
        target_table: optional table name the action references.
        target_id: optional row id within ``target_table``.
        details: optional dict of additional fields, encoded as JSON.

    Returns:
        The newly-computed ``this_hash`` (32 chars of sha256).

    Refuses:
        Inserts blocked by the schema's append-only triggers will surface
        ``sqlite3.IntegrityError``; not handled here.
    """
    details_json = _stable_details(details)

    prev_row = conn.execute(
        "SELECT this_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if prev_row is None:
        previous_hash = None
    else:
        # Accept both row factory styles.
        try:
            previous_hash = prev_row["this_hash"]
        except (TypeError, IndexError, KeyError):
            previous_hash = prev_row[0]

    # Insert with a generated occurred_at so the hash matches the stored row.
    occurred_row = conn.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()
    try:
        occurred_at = occurred_row["now"]
    except (TypeError, IndexError, KeyError):
        occurred_at = occurred_row[0]

    this_hash = _compute_hash(
        previous_hash=previous_hash,
        actor=actor,
        action=action,
        target_table=target_table,
        target_id=target_id,
        occurred_at=occurred_at,
        details_json=details_json,
    )

    conn.execute(
        "INSERT INTO audit_log "
        "(occurred_at, actor, action, target_table, target_id, details, "
        " previous_hash, this_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            occurred_at,
            actor,
            action,
            target_table,
            target_id,
            details_json if details_json else None,
            previous_hash,
            this_hash,
        ),
    )
    return this_hash


def verify(knowledge_db: Path) -> Tuple[bool, Optional[int], Optional[str]]:
    """Walk ``audit_log`` in id order and verify the hash chain.

    Returns:
        ``(True, None, None)`` if the chain is intact, otherwise
        ``(False, first_bad_id, reason)`` identifying the first broken row.

    Refuses:
        Missing DB file raises ``FileNotFoundError``.
    """
    knowledge_db = Path(knowledge_db)
    if not knowledge_db.exists():
        raise FileNotFoundError(str(knowledge_db))

    conn = sqlite3.connect(str(knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        # If audit_log doesn't exist yet, treat as a clean (empty) chain.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if row is None:
            return (True, None, None)

        expected_prev: Optional[str] = None
        cur = conn.execute(
            "SELECT id, occurred_at, actor, action, target_table, target_id, "
            "       details, previous_hash, this_hash "
            "FROM audit_log ORDER BY id ASC"
        )
        for r in cur:
            stored_prev = r["previous_hash"]
            stored_this = r["this_hash"]
            details_text = r["details"] or ""

            # Stored previous_hash must match the running chain pointer.
            if (stored_prev or None) != (expected_prev or None):
                return (
                    False,
                    int(r["id"]),
                    "previous_hash mismatch (expected {e!r}, got {s!r})".format(
                        e=expected_prev, s=stored_prev
                    ),
                )

            # Recompute this_hash and compare.
            recomputed = _compute_hash(
                previous_hash=stored_prev,
                actor=r["actor"],
                action=r["action"],
                target_table=r["target_table"],
                target_id=r["target_id"],
                occurred_at=r["occurred_at"],
                details_json=details_text,
            )
            if recomputed != stored_this:
                return (
                    False,
                    int(r["id"]),
                    "this_hash mismatch (expected {e!r}, got {s!r})".format(
                        e=recomputed, s=stored_this
                    ),
                )
            expected_prev = stored_this

        return (True, None, None)
    finally:
        conn.close()


def cli_verify(knowledge_db: Path) -> int:
    """Run :py:func:`verify` and print a human-readable verdict.

    Returns:
        ``0`` on success, ``2`` on a detected break.

    Refuses:
        Raises :py:class:`runaway_context.errors.AuditChainBroken` when the
        chain is broken — so Python callers can ``try/except`` the failure
        even though the CLI form returns an exit code.
    """
    ok, bad_id, reason = verify(Path(knowledge_db))
    if ok:
        sys.stdout.write("audit_log: OK (chain intact)\n")
        return 0
    msg = "audit_log: BROKEN at id={id}: {reason}\n".format(
        id=bad_id, reason=reason
    )
    sys.stderr.write(msg)
    raise AuditChainBroken(msg.strip())
