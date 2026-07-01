"""Schema validation for resolved tool calls in the Ecospheric Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from etp.describe import build_parameters_schema

from ecospheric_harness.intents import ResolvedCall

# jsonschema is a runtime dependency for JSON-Schema validation.
import jsonschema  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Outcome of validating a :class:`ResolvedCall` against its schema."""

    ok: bool
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

# Harness-internal key injected by the resolver; must not reach the schema.
_INPUT_TARGET_KEY = "_input_target"


class SchemaValidator:
    """Validates :class:`ResolvedCall.params` against the ETP parameter schema."""

    def validate(self, resolved: ResolvedCall) -> ValidationResult:
        """Return a :class:`ValidationResult` for *resolved*.

        Steps:
        1. Build the JSON Schema for the command via ``build_parameters_schema``.
        2. Strip the harness-internal ``_input_target`` key from *params*.
        3. Validate the cleaned params against the schema.
        """
        schema: dict[str, Any] = build_parameters_schema(resolved.command)

        # Strip harness-internal keys before validation.
        cleaned: dict[str, Any] = {
            k: v for k, v in resolved.params.items() if k != _INPUT_TARGET_KEY
        }

        validator = jsonschema.Draft7Validator(schema)

        errors: list[str] = sorted(
            _format_error(err) for err in validator.iter_errors(cleaned)
        )

        if errors:
            return ValidationResult(ok=False, errors=errors)
        return ValidationResult(ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_error(err: jsonschema.ValidationError) -> str:
    """Produce a concise, human-readable error string from a validation error."""
    # ``err.path`` is a deque of path segments; show them dotted.
    loc = ".".join(str(p) for p in err.absolute_path) if err.absolute_path else ""
    msg = str(err.message)
    if loc:
        return f"{loc}: {msg}"
    return msg
