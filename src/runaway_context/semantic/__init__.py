"""Semantic retrieval package (E12, E13).

Public surface:
    * :class:`Provider` / :class:`LocalDeterministicProvider` —
      embedding provider protocol + no-network fallback.
    * :func:`load_provider` — factory; lazy-imports network providers
      behind their HR-1 opt-in guards.
    * :func:`embed_record` — write embeddings into the sidecar tables.
    * :func:`search_similar` — pure cosine search over a single table.
    * :func:`hybrid_search` — blend FTS5 + vector results.
    * :func:`cosine` — cosine similarity helper.
    * :func:`pack_vector` / :func:`unpack_vector` — float32 BLOB codec.

HR-1: no network imports in this package. Each network provider lives in
:mod:`runaway_context.embeddings.providers` and gates its imports behind
:attr:`runaway_context.config.Config.network_opt_in`.

Refuses:
    Importing network providers without their opt-in flag set.
"""

from runaway_context.semantic.encoder import (  # noqa: F401
    LocalDeterministicProvider,
    Provider,
    load_provider,
    pack_vector,
    unpack_vector,
)
from runaway_context.semantic.index import (  # noqa: F401
    cosine,
    default_provider,
    embed_record,
    hybrid_search,
    search_similar,
)

__all__ = [
    "Provider",
    "LocalDeterministicProvider",
    "load_provider",
    "pack_vector",
    "unpack_vector",
    "cosine",
    "embed_record",
    "search_similar",
    "hybrid_search",
    "default_provider",
]
