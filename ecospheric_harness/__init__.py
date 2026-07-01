"""Ecospheric Agent Harness — intent-based geospatial pipeline orchestration.

Public API::

    from ecospheric_harness import Harness, HarnessConfig, Orchestrator, PipelineResult, StepRecord

    h = Harness(tools=["edd", "ese"])
    result = h.run("Download Sentinel-2 scene S2B_MSIL2A and clip to this region")
"""

from __future__ import annotations

from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.result import PipelineResult, StepRecord

# Harness is defined in __main__.py; import last to avoid circular deps
# (submodules import from each other, not from __init__).
# Harness lives in __main__.py alongside CLI; safe because __main__ guards with if __name__ == "__main__"
from ecospheric_harness.__main__ import Harness

__all__ = [
    "Harness",
    "HarnessConfig",
    "Orchestrator",
    "PipelineResult",
    "StepRecord",
]
