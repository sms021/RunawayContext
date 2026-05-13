"""Ollama loopback embedding provider (opt-in).

HR-1: this module imports ``urllib`` at module level so the static HR-1
scanner sees the import. The import is GUARDED behind a runtime check on
:attr:`runaway_context.config.Config.network_opt_in`; with the flag False
the import is never reached and the module raises :class:`ImportError`.

Even though Ollama runs on the loopback interface only, we still classify
it as a network egress path: a misconfigured Ollama URL could point at a
remote host, and the import-time guard removes that risk.

Refuses:
    Loading at all unless ``Config.network_opt_in['ollama']`` is True.
"""

from __future__ import annotations

import json
from typing import List, Optional

from runaway_context.config import Config


_cfg = Config.load()
if not bool(_cfg.network_opt_in.get("ollama", False)):
    raise ImportError(
        "ollama provider requires Config.network_opt_in['ollama'] = True (HR-1). "
        "Set the flag in your install's config.json or pass an explicit Config "
        "to runaway_context.semantic.encoder.load_provider()."
    )

# Network import allowed past this point only because the HR-1 opt-in guard
# above gates module import on an explicit user-set flag.
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402


_DEFAULT_BASE_URL = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_DIM = 768


class OllamaProvider:
    """Embed via a local Ollama HTTP server.

    The provider POSTs to ``/api/embeddings`` and parses the ``embedding``
    field of the JSON response.

    Returns:
        Vectors of length :attr:`dim` per call to :meth:`embed`.

    Raises:
        RuntimeError: when the server returns a non-2xx status or a
            payload that does not contain an ``embedding`` list.

    Refuses:
        Returning a vector whose length disagrees with :attr:`dim`.
    """

    def __init__(self, *, base_url: str = _DEFAULT_BASE_URL,
                 model: str = _DEFAULT_MODEL,
                 dim: int = _DEFAULT_DIM,
                 timeout: float = 30.0) -> None:
        """Construct the provider with a base URL and model name.

        Refuses:
            Negative timeout or zero dim — raises :class:`ValueError`.
        """
        if dim <= 0:
            raise ValueError("dim must be positive")
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = int(dim)
        self.timeout = float(timeout)
        self.provider_name = f"ollama-{model}"

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed each text in *texts* via the Ollama loopback server.

        Returns:
            One vector per input text.

        Raises:
            RuntimeError: when the HTTP call fails or the response shape
                is unexpected.
            TypeError: when *texts* is not a list of strings.

        Refuses:
            Returning partial output. Any failed text raises and aborts.
        """
        if isinstance(texts, str):
            raise TypeError("texts must be a list of strings, not a single string")
        out: List[List[float]] = []
        for text in texts:
            if not isinstance(text, str):
                raise TypeError(f"expected str, got {type(text).__name__}")
            payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
            request = urllib.request.Request(
                f"{self.base_url}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                response = urllib.request.urlopen(request, timeout=self.timeout)
                body = response.read()
            except urllib.error.URLError as exc:
                raise RuntimeError(f"ollama embedding request failed: {exc}") from exc
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError as exc:
                raise RuntimeError(f"ollama returned non-JSON body: {exc}") from exc
            vec = data.get("embedding")
            if not isinstance(vec, list):
                raise RuntimeError(
                    "ollama response did not contain an 'embedding' list"
                )
            if len(vec) != self.dim:
                raise RuntimeError(
                    f"ollama returned dim={len(vec)} but provider expects {self.dim}"
                )
            out.append([float(x) for x in vec])
        return out


def build_provider(
    name: str = "ollama-nomic-embed-text",
    config: Optional[Config] = None,
) -> OllamaProvider:
    """Construct an :class:`OllamaProvider`.

    The model is parsed from *name* (``ollama-<model>``).

    Returns:
        A configured :class:`OllamaProvider`.

    Raises:
        ValueError: when *name* does not start with ``ollama-``.

    Refuses:
        Constructing a provider for a non-ollama name.
    """
    if not name.startswith("ollama-"):
        raise ValueError(f"ollama provider cannot serve {name!r}")
    model = name[len("ollama-"):]
    return OllamaProvider(model=model)


__all__ = ["OllamaProvider", "build_provider"]
