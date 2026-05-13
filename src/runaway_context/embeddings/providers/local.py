"""Local no-network embedding provider.

This module is a thin re-export of
:class:`runaway_context.semantic.encoder.LocalDeterministicProvider` so that
adopters can ``from runaway_context.embeddings.providers.local import
build_provider`` for symmetry with the network-capable provider modules.

HR-1: this module imports nothing network-capable. It exists only to give
the provider registry an "always-available" entry that does not require
any opt-in flag.

Refuses:
    Anything network-related — by construction, the imports below contain
    no network libraries.
"""

from __future__ import annotations

from typing import Optional

from runaway_context.config import Config
from runaway_context.semantic.encoder import LocalDeterministicProvider


def build_provider(
    name: str = "sentence-transformers-MiniLM-L6-v2",
    config: Optional[Config] = None,
) -> LocalDeterministicProvider:
    """Construct the deterministic local fallback provider.

    Returns:
        A :class:`LocalDeterministicProvider` keyed on *name*.

    Refuses:
        Network access. Negative / zero dim (raises :class:`ValueError`
        in the provider constructor).
    """
    if "mpnet" in name:
        return LocalDeterministicProvider(provider_name=name, dim=768)
    return LocalDeterministicProvider(provider_name=name, dim=384)


__all__ = ["build_provider"]
