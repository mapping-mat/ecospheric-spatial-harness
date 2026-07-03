"""Single point of parameter-name normalization."""
from __future__ import annotations

from typing import Any


def normalize_param_key(key: str) -> str:
    """Strip leading dashes and convert hyphens to underscores.

    '--source' -> 'source', '--output-crs' -> 'output_crs', 'bbox' -> 'bbox'
    """
    return key.lstrip("-").replace("-", "_")


def normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Normalize all keys in params. Last-value-wins on key collision."""
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        normalized[normalize_param_key(key)] = value
    return normalized
