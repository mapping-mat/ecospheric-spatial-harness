"""Ecospheric Agent Harness — intent-based geospatial pipeline orchestration.

Public API::

    from ecospheric_harness import Harness, HarnessConfig, Orchestrator, PipelineResult, StepRecord

    h = Harness(tools=["edd", "ese"])
    result = h.run("Download Sentinel-2 scene S2B_MSIL2A and clip to this region")
"""

from __future__ import annotations

from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.providers.base import (
    ModelProvider,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ProviderError,
)
from ecospheric_harness.providers.openrouter import OpenRouterProvider
from ecospheric_harness.providers.ollama import OllamaProvider
from ecospheric_harness.result import PipelineResult, StepRecord

# Harness is defined in __main__.py; import last to avoid circular deps
# (submodules import from each other, not from __init__).
# Harness lives in __main__.py alongside CLI; safe because __main__ guards with if __name__ == "__main__"
from ecospheric_harness.__main__ import Harness
from ecospheric_harness.session_manager import SessionManager

__all__ = [
    "Harness",
    "HarnessConfig",
    "ModelProvider",
    "ModelResponse",
    "OllamaProvider",
    "OpenRouterProvider",
    "Orchestrator",
    "PipelineResult",
    "ProviderError",
    "SessionManager",
    "StepRecord",
    "StreamChunk",
    "TokenUsage",
]
