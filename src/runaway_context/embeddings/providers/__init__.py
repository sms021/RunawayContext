"""Provider registry.

This module enumerates provider names and their corresponding opt-in
keys for :attr:`runaway_context.config.Config.network_opt_in`. It does
NOT import the provider modules — each network provider module guards
its own network imports at module-import time.

Refuses:
    Importing any provider module here. Importing one of the
    ``providers.openai`` / ``providers.voyage`` / ``providers.ollama``
    modules without the corresponding opt-in flag raises ImportError
    at THAT module's import time.
"""

#: Mapping of provider id → ``Config.network_opt_in`` key required to load.
OPT_IN_KEYS = {
    "openai-text-embedding-3-small": "openai",
    "openai-text-embedding-3-large": "openai",
    "voyage-2": "voyage",
    "voyage-large-2": "voyage",
    "ollama-nomic-embed-text": "ollama",
}

#: All provider ids this package knows about.
PROVIDER_NAMES = (
    "local-deterministic",
    "sentence-transformers-MiniLM-L6-v2",
    "sentence-transformers-mpnet-base-v2",
    "ollama-nomic-embed-text",
    "openai-text-embedding-3-small",
    "openai-text-embedding-3-large",
    "voyage-2",
    "voyage-large-2",
)


__all__ = ["OPT_IN_KEYS", "PROVIDER_NAMES"]
