"""PII scrub pipeline (E18).

Used before any export that could include session transcripts or other
free-form text the user typed. Replaces email addresses, IP addresses
(IPv4 + simple IPv6 patterns), and hostnames with opaque placeholders.

Refuses:
    Nothing — invalid input types (non-string) raise ``TypeError`` so
    callers see the failure immediately (HR-10, no silent failures).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


# --- regex constants ------------------------------------------------------

# Email — RFC-pragmatic, not a full validator. Anchored on the local-part
# rules common to real-world addresses.
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

# IPv4 (each octet 0-255 enforced via alternation), plus a permissive IPv6
# matcher (handles the common ``::`` short form).
_IPV4 = r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)"
_IPV6 = r"(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}|::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
IP_RE = re.compile(r"\b(?:" + _IPV4 + "|" + _IPV6 + r")\b")

# Hostname / FQDN — at least two labels separated by dots, last label is
# alphabetic. Avoids matching version strings like ``3.0.0``.
HOST_RE = re.compile(
    r"\b(?=[A-Za-z0-9-]{1,63}\.)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\b"
)


def scrub_text(text: str) -> str:
    """Replace emails / IPs / hostnames in ``text`` with placeholders.

    The order matters: emails are scrubbed first (they contain ``@`` so a
    bare hostname rule wouldn't match them), then IPs, then hostnames.
    Placeholders are ``<email>``, ``<ip>``, ``<host>``.

    Args:
        text: Input string.

    Returns:
        Scrubbed string.

    Raises:
        TypeError: when ``text`` is not a ``str``.

    Refuses:
        Coercing non-string types silently.
    """
    if not isinstance(text, str):
        raise TypeError(
            "scrub_text requires str, got {t}".format(t=type(text).__name__)
        )
    if not text:
        return text
    out = EMAIL_RE.sub("<email>", text)
    out = IP_RE.sub("<ip>", out)
    out = HOST_RE.sub("<host>", out)
    return out


def scrub_record(
    record: Dict[str, Any],
    fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Return a copy of ``record`` with the named fields scrubbed.

    When ``fields`` is omitted every string value in ``record`` is scrubbed.

    Args:
        record: Mapping of column-name → value.
        fields: Optional iterable of keys to scrub.

    Returns:
        New dict with scrubbed values. Non-string values are left unchanged.

    Raises:
        TypeError: when ``record`` is not a mapping.

    Refuses:
        Mutating the input dict.
    """
    if not isinstance(record, dict):
        raise TypeError(
            "scrub_record requires dict, got {t}".format(t=type(record).__name__)
        )
    keys: List[str]
    if fields is None:
        keys = [k for k, v in record.items() if isinstance(v, str)]
    else:
        keys = list(fields)
    out: Dict[str, Any] = dict(record)
    for k in keys:
        v = out.get(k)
        if isinstance(v, str):
            out[k] = scrub_text(v)
    return out
