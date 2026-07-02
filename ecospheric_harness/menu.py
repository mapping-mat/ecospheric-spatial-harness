"""Intent menu narrowing for the Ecospheric Agent Harness.

Filters the full intent catalog down to options compatible with the
current artifact (or no-input commands when no artifact exists),
deduplicates by intent name using resolver-backed param selection,
and caps the output at 15 options.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ecospheric_harness.artifact import normalize_format
from ecospheric_harness.intents import IntentEntry, IntentOption, ResolvedCall
from ecospheric_harness.registry import PARAM_DENYLIST

if TYPE_CHECKING:
    from ecospheric_harness.artifact import Artifact
    from ecospheric_harness.resolver import IntentResolver

_EXCLUDED_CATEGORIES: frozenset[str] = frozenset({
    "diagnostic",
    "info",
    "pipe",
})

_MAX_OPTIONS: int = 15


def available_intents(
    catalog: list[IntentEntry],
    artifact: Artifact | None,
    resolver: IntentResolver,
) -> list[IntentOption]:
    """Return intent options compatible with *artifact*.

    Parameters
    ----------
    catalog:
        Full intent catalog built by the registry.
    artifact:
        Current artifact in context, or ``None`` if no data produced yet.
    resolver:
        Resolver used to determine which catalog entry would actually
        be selected for a given intent + artifact combination.

    Returns
    -------
    list[IntentOption]
        Deduplicated, compatibility-filtered menu options capped at 15.
    """
    options: list[IntentOption] = []
    seen: set[str] = set()

    for entry in catalog:
        # Step 2: skip diagnostic / info / pipe categories
        if entry.command.category in _EXCLUDED_CATEGORIES:
            continue

        # Step 3–4: compatibility filtering
        if artifact is None:
            # Only show no-input commands
            if entry.command.input_formats:
                continue
        else:
            # Skip no-input commands when artifact is in context
            if not entry.command.input_formats:
                continue
            type_match = (
                entry.command.data_type == artifact.data_type
                or entry.command.data_type == "any"
            )
            norm_fmt = normalize_format(artifact.format)
            format_match = any(
                normalize_format(f) == norm_fmt
                for f in entry.command.input_formats
            )
            if not (type_match and format_match):
                continue

        # Step 5: dedup by intent name
        if entry.intent in seen:
            continue

        # Step 6: when artifact exists, resolve to find the actual entry
        # Skip resolution for fetch with no params (always fails; params come from user).
        if artifact is not None and not (entry.intent == "fetch" and not entry.required_params):
            try:
                resolved = resolver.resolve(entry.intent, {}, artifact)
            except Exception:
                resolved = None
            if isinstance(resolved, ResolvedCall):
                for e in catalog:
                    if e.intent == entry.intent and e.command is resolved.command:
                        entry = e
                        break

        seen.add(entry.intent)

        # Step 7: required params filtered by denylist
        required_params = [
            p for p in entry.required_params if p not in PARAM_DENYLIST
        ]

        # Build param descriptors for the model (non-denylisted)
        param_descriptors = []
        for p in entry.command.parameters:
            if p.name in PARAM_DENYLIST:
                continue
            param_descriptors.append({
                "name": p.name,
                "type": p.type,
                "description": p.description,
                "required": p.required,
            })

        options.append(IntentOption(
            intent=entry.intent,
            description=entry.description,
            required_params=required_params,
            tool=entry.tool.name,
            command=entry.command.name,
            data_type=entry.command.data_type,
            params=param_descriptors,
        ))

    # Step 8: cap at 15
    return options[:_MAX_OPTIONS]
