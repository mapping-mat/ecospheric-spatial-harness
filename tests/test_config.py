from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from ecospheric_harness.config import HarnessConfig


# ── Defaults match spec ──────────────────────────────────────────────


def test_defaults_match_spec() -> None:
    cfg = HarnessConfig()
    assert cfg.model == "openrouter/z-ai/glm-5.2"
    assert cfg.tools == ["edd", "ese"]
    assert cfg.subprocess_timeout == 300
    assert cfg.disk_limit_gb == 2.0
    assert cfg.search_cap == 20
    assert cfg.max_turns == 20
    assert cfg.workdir == Path(cfg.workdir)  # is a Path


# ── from_env with defaults (no env vars) ─────────────────────────────


def test_from_env_defaults() -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("HARNESS_")
        and k not in ("OPENROUTER_API_KEY", "EDD_BIN", "ESE_BIN")
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = HarnessConfig.from_env()
    assert cfg.model == "openrouter/z-ai/glm-5.2"
    assert cfg.tools == ["edd", "ese"]
    assert cfg.subprocess_timeout == 300
    assert cfg.disk_limit_gb == 2.0
    assert cfg.search_cap == 20
    assert cfg.max_turns == 20


# ── from_env with all env vars set ───────────────────────────────────


def test_from_env_all_vars() -> None:
    env = {
        "OPENROUTER_API_KEY": "sk-test-key",
        "EDD_BIN": "/usr/local/bin/edd",
        "ESE_BIN": "/usr/local/bin/ese",
        "HARNESS_WORKDIR": "/tmp/custom_harness",
        "HARNESS_MAX_TURNS": "50",
        "HARNESS_SUBPROCESS_TIMEOUT": "600",
        "HARNESS_DISK_LIMIT_GB": "4.5",
        "HARNESS_SEARCH_CAP": "100",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = HarnessConfig.from_env()
    assert cfg.workdir == Path("/tmp/custom_harness")
    assert cfg.max_turns == 50
    assert cfg.subprocess_timeout == 600
    assert cfg.disk_limit_gb == 4.5
    assert cfg.search_cap == 100


# ── from_cli overrides ───────────────────────────────────────────────


def test_from_cli_overrides() -> None:
    cfg = HarnessConfig.from_cli(model="openai/gpt-4o", max_turns=99)
    assert cfg.model == "openai/gpt-4o"
    assert cfg.max_turns == 99
    # untouched fields keep defaults
    assert cfg.subprocess_timeout == 300
    assert cfg.tools == ["edd", "ese"]


def test_from_cli_none_values_ignored() -> None:
    cfg = HarnessConfig.from_cli(model=None, max_turns=10)
    assert cfg.model == "openrouter/z-ai/glm-5.2"  # default preserved
    assert cfg.max_turns == 10
