"""Three-axis severity helpers (E4).

Severity is captured on lessons via three independent axes — each in the
range 1..5::

    blast_radius   — how many people / systems are affected
    frequency      — how often this lesson would re-fire if ignored
    reversibility  — how hard it is to recover from the failure (high = bad)

The derived ``severity`` (critical / warning / info) is a back-compat
roll-up that mirrors the SQL view ``lessons_derived_severity``. This module
exposes that roll-up to Python callers (CLI, MCP, write guards).

Refuses:
    Axis values outside 1..5 (raises :class:`ValueError`).
"""

from __future__ import annotations

from typing import Optional, Tuple


SEVERITY_LEVELS: Tuple[str, ...] = ("critical", "warning", "info")


def validate_axes(blast_radius: Optional[int] = None,
                  frequency: Optional[int] = None,
                  reversibility: Optional[int] = None) -> None:
    """Validate each provided axis is in the range 1..5 (inclusive).

    ``None`` is allowed on any axis (means "unset"). Booleans are rejected
    explicitly because Python treats ``True`` as ``1`` — that quietly hides
    callers who passed the wrong type.

    Returns:
        None.

    Refuses:
        Any non-None value that is not an ``int`` or that falls outside 1..5.
    """
    for name, value in (
        ("blast_radius", blast_radius),
        ("frequency", frequency),
        ("reversibility", reversibility),
    ):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{name} must be an int in 1..5 (got {type(value).__name__})"
            )
        if value < 1 or value > 5:
            raise ValueError(
                f"{name} out of range: {value} (must be 1..5)"
            )


def derive_severity(blast_radius: Optional[int],
                    frequency: Optional[int],
                    reversibility: Optional[int],
                    fallback: str = "warning") -> str:
    """Return the derived severity bucket for a triple of axes.

    Mirrors the SQL view ``lessons_derived_severity``:

      * all three None -> ``fallback``
      * ``blast_radius >= 4`` or ``reversibility >= 4`` -> ``critical``
      * ``blast_radius >= 2`` or ``frequency >= 3`` -> ``warning``
      * otherwise -> ``info``

    Missing axes are treated as 1 inside the comparison (matches the
    ``COALESCE(..., 1)`` calls in the SQL view).

    Returns:
        One of ``critical`` / ``warning`` / ``info`` — or ``fallback`` when
        every axis is None.

    Refuses:
        Implicitly: callers should pass values that pass :func:`validate_axes`.
        This function does not re-validate; ``derive_severity`` is hot-path.
    """
    if blast_radius is None and frequency is None and reversibility is None:
        return fallback
    br = blast_radius if blast_radius is not None else 1
    fq = frequency if frequency is not None else 1
    rv = reversibility if reversibility is not None else 1
    if br >= 4 or rv >= 4:
        return "critical"
    if br >= 2 or fq >= 3:
        return "warning"
    return "info"


def axis_summary(blast_radius: Optional[int],
                 frequency: Optional[int],
                 reversibility: Optional[int]) -> str:
    """Return a one-line human summary of the three axes (for logs and CLI).

    Example::

        >>> axis_summary(4, 3, 2)
        'blast=4 freq=3 rev=2'
        >>> axis_summary(None, 3, None)
        'blast=- freq=3 rev=-'

    Returns:
        A space-separated ``blast=X freq=Y rev=Z`` string (``-`` for None).

    Refuses:
        Nothing — this is pure formatting.
    """
    def _fmt(v: Optional[int]) -> str:
        return "-" if v is None else str(v)
    return (
        f"blast={_fmt(blast_radius)} "
        f"freq={_fmt(frequency)} "
        f"rev={_fmt(reversibility)}"
    )
