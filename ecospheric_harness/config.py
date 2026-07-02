from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HarnessConfig:
    """Configuration for the Ecospheric Agent Harness."""

    model: str = "z-ai/glm-5.2"
    tools: list[str] = field(default_factory=lambda: ["edd", "ese"])
    subprocess_timeout: int = 300
    disk_limit_gb: float = 2.0
    search_cap: int = 20
    max_turns: int = 20
    workspace_root: Path = field(
        default_factory=lambda: Path.home() / ".esp" / "sessions"
    )
    session_id: str | None = None

    # Security
    subprocess_max_output_mb: int = 100
    rlimit_as_mb: int | None = None  # None = no RLIMIT_AS
    rlimit_nproc: int | None = None  # None = no RLIMIT_NPROC
    gdal_cachemax_mb: int = 256

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

        # New env var: HARNESS_WORKSPACE_ROOT
        if v := os.environ.get("HARNESS_WORKSPACE_ROOT"):
            cfg.workspace_root = Path(v)

        # Deprecated alias: HARNESS_WORKDIR
        if v := os.environ.get("HARNESS_WORKDIR"):
            warnings.warn(
                "HARNESS_WORKDIR is deprecated; use HARNESS_WORKSPACE_ROOT instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Only use the deprecated value if the new one wasn't set
            if not os.environ.get("HARNESS_WORKSPACE_ROOT"):
                cfg.workspace_root = Path(v)

        if v := os.environ.get("HARNESS_SESSION_ID"):
            cfg.session_id = v
        if v := os.environ.get("HARNESS_MAX_TURNS"):
            cfg.max_turns = int(v)
        if v := os.environ.get("HARNESS_SUBPROCESS_TIMEOUT"):
            cfg.subprocess_timeout = int(v)
        if v := os.environ.get("HARNESS_DISK_LIMIT_GB"):
            cfg.disk_limit_gb = float(v)
        if v := os.environ.get("HARNESS_SEARCH_CAP"):
            cfg.search_cap = int(v)

        # Security env vars
        if v := os.environ.get("HARNESS_MAX_OUTPUT_MB"):
            cfg.subprocess_max_output_mb = int(v)
        if v := os.environ.get("HARNESS_RLIMIT_AS_MB"):
            cfg.rlimit_as_mb = int(v)
        if v := os.environ.get("HARNESS_RLIMIT_NPROC"):
            cfg.rlimit_nproc = int(v)
        if v := os.environ.get("HARNESS_GDAL_CACHEMAX_MB"):
            cfg.gdal_cachemax_mb = int(v)

        return cfg

    @classmethod
    def from_cli(cls, **overrides: object) -> HarnessConfig:
        """Build a HarnessConfig, applying CLI-flag overrides on top of defaults."""
        # Filter out None values so defaults survive
        clean = {k: v for k, v in overrides.items() if v is not None}
        return cls(**clean)  # type: ignore[arg-type]
