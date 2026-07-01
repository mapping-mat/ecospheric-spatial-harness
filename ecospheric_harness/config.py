from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HarnessConfig:
    """Configuration for the Ecospheric Agent Harness."""

    model: str = "openrouter/z-ai/glm-5.2"
    tools: list[str] = field(default_factory=lambda: ["edd", "ese"])
    subprocess_timeout: int = 300
    disk_limit_gb: float = 2.0
    search_cap: int = 20
    max_turns: int = 20
    workdir: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "harness"
    )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> HarnessConfig:
        """Build a HarnessConfig from environment variables.

        OPENROUTER_API_KEY is *read* (so callers can verify it exists) but
        never stored on the dataclass — secrets don't belong in config
        objects.
        """
        _ = os.environ.get("OPENROUTER_API_KEY")  # validate presence, discard

        cfg = cls()

        if v := os.environ.get("HARNESS_WORKDIR"):
            cfg.workdir = Path(v)
        if v := os.environ.get("HARNESS_MAX_TURNS"):
            cfg.max_turns = int(v)
        if v := os.environ.get("HARNESS_SUBPROCESS_TIMEOUT"):
            cfg.subprocess_timeout = int(v)
        if v := os.environ.get("HARNESS_DISK_LIMIT_GB"):
            cfg.disk_limit_gb = float(v)
        if v := os.environ.get("HARNESS_SEARCH_CAP"):
            cfg.search_cap = int(v)

        return cfg

    @classmethod
    def from_cli(cls, **overrides: object) -> HarnessConfig:
        """Build a HarnessConfig, applying CLI-flag overrides on top of defaults."""
        # Filter out None values so defaults survive
        clean = {k: v for k, v in overrides.items() if v is not None}
        return cls(**clean)  # type: ignore[arg-type]
