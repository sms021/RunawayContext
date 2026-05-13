"""Embedding providers (default-off network surface).

This package contains four provider modules:

  * :mod:`runaway_context.embeddings.providers.local` — no-network
    deterministic fallback used by the default install.
  * :mod:`runaway_context.embeddings.providers.ollama` — loopback to a
    local Ollama server; opt-in.
  * :mod:`runaway_context.embeddings.providers.openai` — OpenAI REST API;
    opt-in.
  * :mod:`runaway_context.embeddings.providers.voyage` — Voyage AI REST
    API; opt-in.

Each opt-in module raises :class:`ImportError` at module-import time when
its corresponding ``Config.network_opt_in`` flag is False (HR-1). Static
HR-1 imports scans can see the network imports inside each opt-in module
but they are allowlisted by the contract test.

Refuses:
    Importing network providers without their opt-in flag set.
"""

from runaway_context.embeddings.providers import (  # noqa: F401
    OPT_IN_KEYS,
    PROVIDER_NAMES,
)

__all__ = ["OPT_IN_KEYS", "PROVIDER_NAMES"]
