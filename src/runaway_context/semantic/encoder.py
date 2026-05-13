"""Embedding provider abstraction (E12).

This module defines the :class:`Provider` protocol and a reference fallback
implementation that has no network and no machine-learning dependency. Real
providers (sentence-transformers, OpenAI, Voyage, Ollama) plug in through
:func:`load_provider`, which lazy-imports the corresponding module so a
default install never touches network code (HR-1).

The fallback :class:`LocalDeterministicProvider` is a hash-based encoder —
it is NOT a learned embedding. It exists so that the schema, retrieval
pipeline, and contract tests work end-to-end on a stock Python install
without ML libraries. Adopters who want real embeddings either:

  * load ``sentence-transformers`` in their environment and replace
    :func:`load_provider` for the matching name, or
  * enable an opt-in network provider via
    :attr:`runaway_context.config.Config.network_opt_in`.

Refuses:
    Returning a network provider when the install does not have its
    ``network_opt_in`` flag set (raises :class:`ImportError` via the
    target provider module). The opt-in guard lives inside each provider
    module so the static HR-1 import scan picks it up.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import struct
from typing import Iterable, List, Optional, Protocol, runtime_checkable

from runaway_context.config import Config


_FALLBACK_DIM = 384


@runtime_checkable
class Provider(Protocol):
    """Embedding provider contract.

    A provider turns text into a fixed-dimension vector of float32 values.
    Every concrete implementation MUST expose ``provider_name`` and ``dim``
    as plain attributes and implement :meth:`embed`.

    Returns:
        Implementations return a list of vectors, one per input text.

    Raises:
        Implementations raise :class:`ImportError` at module load if a
        required opt-in flag is not set (HR-1).

    Refuses:
        Implementations refuse to silently truncate / pad — any caller that
        passes a text larger than the provider's window receives the
        provider's documented error (typically :class:`ValueError`).
    """

    provider_name: str
    dim: int

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Return a list of vectors, one per input text."""
        ...


class LocalDeterministicProvider:
    """No-network deterministic fallback encoder.

    Produces a 384-dim float32 vector seeded by ``sha256(text)``. Two calls
    with the same text return the same vector; two calls with different
    text return cosine-distinguishable vectors. This is sufficient for
    contract tests and for environments without ML libraries.

    This is NOT a learned embedding. Adopters who want sentence-transformers
    semantics should install ``sentence-transformers`` and replace this
    provider in :func:`load_provider`.

    Returns:
        Vectors of length :data:`_FALLBACK_DIM` whose components are
        deterministic real numbers derived from the text hash.

    Refuses:
        Network access. The class imports nothing network-capable.
    """

    provider_name = "sentence-transformers-MiniLM-L6-v2"
    dim = _FALLBACK_DIM

    def __init__(self, provider_name: Optional[str] = None, dim: Optional[int] = None) -> None:
        """Construct a fallback provider.

        Returns:
            A provider instance with the requested name / dim.

        Refuses:
            Negative or zero ``dim`` — raises :class:`ValueError`.
        """
        if dim is not None:
            if dim <= 0:
                raise ValueError("dim must be positive")
            self.dim = int(dim)
        if provider_name is not None:
            self.provider_name = str(provider_name)

    def _vector_for(self, text: str) -> List[float]:
        """Expand a stable SHA-256 stream into ``self.dim`` float components.

        Returns:
            A list of ``self.dim`` floats in roughly [-1, 1].

        Refuses:
            Nothing — pure function over the input bytes.
        """
        raw = text.encode("utf-8", errors="replace")
        out: List[float] = []
        counter = 0
        # Each sha256 yields 32 bytes → 8 float32 floats once interpreted.
        while len(out) < self.dim:
            buf = hashlib.sha256(raw + counter.to_bytes(4, "little")).digest()
            # Treat each pair of bytes as an unsigned 16-bit int → scale to [-1, 1].
            for i in range(0, len(buf), 2):
                if len(out) >= self.dim:
                    break
                u = (buf[i] << 8) | buf[i + 1]
                out.append((u / 32767.5) - 1.0)
            counter += 1
        # L2-normalize so cosine == dot product.
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed an iterable of texts.

        Returns:
            A list of float vectors, one per text, all of length :attr:`dim`.

        Raises:
            TypeError: if *texts* is not iterable of strings.

        Refuses:
            Silently coercing non-string input. Callers must pass strings.
        """
        if isinstance(texts, str):
            raise TypeError("texts must be a list of strings, not a single string")
        result: List[List[float]] = []
        for text in texts:
            if not isinstance(text, str):
                raise TypeError(f"expected str, got {type(text).__name__}")
            result.append(self._vector_for(text))
        return result


_NETWORK_PROVIDER_MODULES = {
    "openai-text-embedding-3-small": "runaway_context.embeddings.providers.openai",
    "openai-text-embedding-3-large": "runaway_context.embeddings.providers.openai",
    "voyage-2": "runaway_context.embeddings.providers.voyage",
    "voyage-large-2": "runaway_context.embeddings.providers.voyage",
    "ollama-nomic-embed-text": "runaway_context.embeddings.providers.ollama",
}

_LOCAL_PROVIDER_NAMES = {
    "sentence-transformers-MiniLM-L6-v2",
    "sentence-transformers-mpnet-base-v2",
    "local-deterministic",
}


def load_provider(name: str, *, config: Optional[Config] = None) -> Provider:
    """Resolve a provider name to a concrete :class:`Provider` instance.

    The default install path returns a :class:`LocalDeterministicProvider`
    for any ``sentence-transformers-*`` name (no network, no ML deps). A
    real sentence-transformers backend can be plugged in by adopters who
    have the library installed — they swap this factory.

    Network-capable providers (``openai-*``, ``voyage-*``, ``ollama-*``)
    are lazy-imported from :mod:`runaway_context.embeddings.providers`. The
    target module raises :class:`ImportError` at module-import time if the
    corresponding ``Config.network_opt_in`` flag is False (HR-1).

    Returns:
        A :class:`Provider` ready to call :meth:`Provider.embed`.

    Raises:
        ValueError: when *name* is not a recognised provider id.
        ImportError: when *name* requires opt-in network access and the
            opt-in flag for that provider is not set.

    Refuses:
        Importing a network provider module unless its opt-in flag is True.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("provider name must be a non-empty string")

    if name in _LOCAL_PROVIDER_NAMES or name == "local-deterministic":
        # Different sentence-transformers names have different dims in the wild;
        # the fallback uses 384 for MiniLM-shaped names and 768 for mpnet-shaped.
        if "mpnet" in name:
            return LocalDeterministicProvider(provider_name=name, dim=768)
        return LocalDeterministicProvider(provider_name=name, dim=_FALLBACK_DIM)

    module_path = _NETWORK_PROVIDER_MODULES.get(name)
    if module_path is None:
        raise ValueError(
            f"unknown embedding provider {name!r}; expected one of "
            f"{sorted(_LOCAL_PROVIDER_NAMES | set(_NETWORK_PROVIDER_MODULES))}"
        )
    # Lazy import — the provider module's top-level guard re-raises ImportError
    # if its network_opt_in flag is False (HR-1).
    module = importlib.import_module(module_path)
    factory = getattr(module, "build_provider", None)
    if factory is None:
        raise ImportError(
            f"provider module {module_path!r} does not expose build_provider()"
        )
    return factory(name=name, config=config or Config.load())


def pack_vector(vec: Iterable[float]) -> bytes:
    """Pack a float vector into little-endian float32 BLOB form.

    Returns:
        ``bytes`` ready to store in ``embedding_payload.vec``.

    Refuses:
        Nothing — converts any iterable of numbers.
    """
    values = [float(x) for x in vec]
    return struct.pack("<" + "f" * len(values), *values)


def unpack_vector(blob: bytes, dim: int) -> List[float]:
    """Decode a float32 BLOB back into a Python list of floats.

    Returns:
        A list of ``dim`` floats.

    Raises:
        ValueError: when ``len(blob)`` does not match ``dim * 4``.

    Refuses:
        Returning a partial vector — length mismatch is a hard error.
    """
    expected = dim * 4
    if len(blob) != expected:
        raise ValueError(
            f"vector blob length {len(blob)} does not match dim*4={expected}"
        )
    return list(struct.unpack("<" + "f" * dim, blob))


__all__ = [
    "Provider",
    "LocalDeterministicProvider",
    "load_provider",
    "pack_vector",
    "unpack_vector",
]
