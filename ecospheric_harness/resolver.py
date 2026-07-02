"""Intent resolver for the Ecospheric Agent Harness.

Maps a user intent + optional current artifact to a single ``ResolvedCall``
or a ``ResolutionError``.  Disambiguates multiple catalog entries by
``data_type`` matching, format fallback, and deterministic tool precedence.
"""

from __future__ import annotations

from typing import Any

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.intents import (
    IntentEntry,
    ResolvedCall,
    ResolutionError,
)

# Tool precedence for deterministic disambiguation (lower = preferred).
_TOOL_PRECEDENCE: dict[str, int] = {"edd": 0, "ese": 1}


class IntentResolver:
    """Resolves an intent string + params to a single tool call."""

    def __init__(self, catalog: list[IntentEntry]) -> None:
        self._catalog = catalog

    def resolve(
        self,
        intent: str,
        params: dict[str, Any],
        current_artifact: Artifact | None,
    ) -> ResolvedCall | ResolutionError:
        """Resolve *intent* to a concrete tool call.

        Parameters
        ----------
        intent:
            The intent string to resolve (e.g. ``"clip"``, ``"fetch"``).
        params:
            Parameters supplied by the agent for this call.
        current_artifact:
            The artifact currently in context, or ``None`` if no data has
            been produced yet.

        Returns
        -------
        ResolvedCall | ResolutionError
        """
        # --- AC48: single-asset fetch enforcement ---
        if intent == "fetch" and ("item" not in params or "asset" not in params):
            return ResolutionError(
                "Fetch requires both 'item' and 'asset' parameters. "
                "Use --list-assets or specify a single item and asset to download."
            )

        # 1. Find all catalog entries matching the intent.
        candidates: list[IntentEntry] = [
            e for e in self._catalog if e.intent == intent
        ]
        if not candidates:
            return ResolutionError(f"Unknown intent '{intent}'")

        # 2. Filter by artifact context.
        if current_artifact is not None:
            result = self._filter_with_artifact(
                candidates, intent, current_artifact
            )
        else:
            result = self._filter_without_artifact(candidates, intent)

        if isinstance(result, ResolutionError):
            return result

        candidates = result

        # 3. Inject source from catalog entry if available (e.g. "@osm").
        # The model emits search_osm but shouldn't need to know the exact
        # --source value (@osm vs osm). The catalog entry carries it.
        source_val = getattr(candidates[0], "source", None)
        if source_val is not None:
            params = dict(params)  # don't mutate caller's dict
            params["source"] = source_val

        # 4. Single candidate → direct resolution.
        if len(candidates) == 1:
            return ResolvedCall(
                tool=candidates[0].tool,
                command=candidates[0].command,
                params=params,
            )

        # 5. Multiple candidates → deterministic precedence sort.
        candidates.sort(key=lambda e: _TOOL_PRECEDENCE.get(e.tool.name, 99))
        return ResolvedCall(
            tool=candidates[0].tool,
            command=candidates[0].command,
            params=params,
        )

    # -- private helpers ---------------------------------------------------

    @staticmethod
    def _filter_with_artifact(
        candidates: list[IntentEntry],
        intent: str,
        artifact: Artifact,
    ) -> list[IntentEntry] | ResolutionError:
        """Filter candidates when an artifact is present."""
        # Primary: exact data_type match.
        compatible = [
            e for e in candidates if e.command.data_type == artifact.data_type
        ]
        # Fallback: "any" data_type + format-compatible.
        if not compatible:
            compatible = [
                e
                for e in candidates
                if e.command.data_type == "any"
                and (
                    artifact.format in e.command.input_formats
                    or not e.command.input_formats
                )
            ]
        if not compatible:
            return ResolutionError(
                f"No tool can '{intent}' on {artifact.data_type}"
            )
        return compatible

    @staticmethod
    def _filter_without_artifact(
        candidates: list[IntentEntry],
        intent: str,
    ) -> list[IntentEntry] | ResolutionError:
        """Filter candidates when no artifact is present."""
        compatible = [
            e
            for e in candidates
            if e.command.input_formats is None
            or len(e.command.input_formats) == 0
        ]
        if not compatible:
            return ResolutionError(
                f"Intent '{intent}' requires input data"
            )
        return compatible
