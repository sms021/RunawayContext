"""Runtime configuration for RunawayContext.

HR-1: every opt-in network module is gated on an explicit `false → true` flag here.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def default_install_dir() -> Path:
    """Return the canonical install directory for this user.

    Returns:
        The path from ``RC_KS_DIR`` if set, else ``~/_knowledge``.

    Refuses:
        Nothing — pure path computation, no I/O.
    """
    override = os.environ.get("RC_KS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "_knowledge"


@dataclass
class Config:
    """Effective config for a single Client instance.

    Returns:
        Config object with resolved paths and feature flags.

    Refuses:
        None — this object never raises. It is read-only after load.
    """

    install_dir: Path = field(default_factory=default_install_dir)
    knowledge_db: Optional[Path] = None
    sessions_db: Optional[Path] = None
    metrics_db: Optional[Path] = None
    tier: str = "T1"  # T0..T5
    embeddings_enabled: bool = False
    embeddings_provider: str = "sentence-transformers-MiniLM-L6-v2"
    mcp_enabled: bool = False
    telemetry_enabled: bool = True
    otlp_enabled: bool = False
    otlp_endpoint: Optional[str] = None
    federation_enabled: bool = False
    federation_refresh_minutes: int = 60
    network_opt_in: dict = field(default_factory=dict)
    # Session capture — opt-out; LLM summarization opt-in (HR-1).
    session_capture_enabled: bool = True
    summarizer_provider: str = "off"   # off | claude-cli | anthropic-api | local-ollama
    summarizer_model: str = "claude-haiku-4-5"
    summarizer_daily_token_cap: int = 50_000
    summarizer_idle_threshold_sec: int = 1800   # 30 min — wait until session is "done"
    summarizer_char_cap: int = 30_000           # truncate transcripts past this
    summarizer_attempt_cap: int = 3             # max retries before permanent-fail marker
    summarizer_cooldown_sec: int = 300          # same conv can't summarize twice in 5 min
    summarizer_lock_timeout_sec: int = 300      # global summarizer lock wait
    summarizer_circuit_break_after: int = 5     # consecutive fails before halting
    summarizer_circuit_recovery_sec: int = 3600 # auto-recover after this many seconds

    @classmethod
    def load(cls, install_dir: Optional[Path] = None) -> "Config":
        """Load config from <install_dir>/config.json, with defaults.

        Returns:
            Config instance.
        """
        d = install_dir or default_install_dir()
        d = Path(d).expanduser()
        path = d / "config.json"
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        cfg = cls(install_dir=d)
        for k, v in data.items():
            if hasattr(cfg, k):
                if k in ("install_dir", "knowledge_db", "sessions_db", "metrics_db") and v:
                    setattr(cfg, k, Path(v))
                else:
                    setattr(cfg, k, v)
        cfg.knowledge_db = cfg.knowledge_db or (d / "knowledge.db")
        cfg.sessions_db = cfg.sessions_db or (d / "sessions.db")
        cfg.metrics_db = cfg.metrics_db or (d / "metrics.db")
        return cfg

    def save(self) -> None:
        """Persist config to <install_dir>/config.json. Creates dir if missing."""
        self.install_dir.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        for k in ("install_dir", "knowledge_db", "sessions_db", "metrics_db"):
            v = data.get(k)
            data[k] = str(v) if v is not None else None
        (self.install_dir / "config.json").write_text(json.dumps(data, indent=2))


SCHEMA_VERSION = (3, 0, 0)
DEFAULT_T1_TOKEN_CAP = 3000
DEFAULT_BRIEF_LINE_CAP = 150
DEFAULT_T1_LINE_CAP = 200  # Constitution
DEFAULT_T2_LINE_CAP = 50   # Living Memory (pointer-only)
