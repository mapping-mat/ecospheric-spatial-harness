from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workdir(tmp_path: Path) -> Path:
    """Return a temporary working directory for harness tests."""
    d = tmp_path / "harness_workdir"
    d.mkdir()
    return d


@pytest.fixture()
def mock_tool_describe() -> dict[str, object]:
    """Return realistic EDD + ESE tool-descriptor JSON."""
    return {
        "tools": [
            {
                "name": "edd",
                "description": (
                    "Ecospheric Data Discoverer — search and retrieve"
                    " geospatial datasets."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text search query for datasets.",
                        },
                        "bbox": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": "[west, south, east, north] bounding box.",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "Maximum number of results.",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "ese",
                "description": (
                    "Ecospheric Spatial Engine — run geospatial"
                    " operations on datasets."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "description": "The spatial operation to perform.",
                        },
                        "inputs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Dataset paths or IDs.",
                        },
                        "params": {
                            "type": "object",
                            "description": "Operation-specific parameters.",
                        },
                    },
                    "required": ["operation", "inputs"],
                },
            },
        ]
    }
