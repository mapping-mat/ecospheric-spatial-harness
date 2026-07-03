"""Schema validation for resolved tool calls in the Ecospheric Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from etp.describe import build_parameters_schema
from etp.describe import CommandDescriptor, ParameterDescriptor

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
        3. Coerce list values to strings for string-typed params (the model
           often emits bbox as a list, but the CLI expects a comma-joined string).
        4. Validate the cleaned params against the schema.
        """
        schema: dict[str, Any] = build_parameters_schema(resolved.command)

        # Strip harness-internal keys before validation.
        cleaned: dict[str, Any] = {
            k: v for k, v in resolved.params.items() if k != _INPUT_TARGET_KEY
        }

        # Coerce list values to comma-joined strings for string-typed params.
        # The model emits bbox as [-122.5, 39.7, -122.3, 39.8] but the ETP
        # schema declares type: "string" and the CLI expects "xmin,ymin,xmax,ymax".
        cleaned = _coerce_params(cleaned, resolved.command)

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


def _coerce_params(
    params: dict[str, Any],
    command: CommandDescriptor,
) -> dict[str, Any]:
    """Coerce param values to match their declared ETP type.

    Currently handles: list → comma-joined string for string-typed params.
    The model frequently emits bbox as [-122.5, 39.7, -122.3, 39.8] but
    the ETP schema declares type: "string" and serialize_params() handles
    comma-joining at execution time. This coercion ensures the validator
    doesn't reject valid list values that the serializer would handle.
    """
    param_map: dict[str, ParameterDescriptor] = {}
    for p in command.parameters:
        prop_name = p.name.lstrip("-").replace("-", "_")
        param_map[prop_name] = p

    coerced: dict[str, Any] = {}
    for key, value in params.items():
        norm_key = key.lstrip("-").replace("-", "_")
        desc = param_map.get(norm_key)
        if (
            desc is not None
            and desc.type == "string"
            and isinstance(value, list)
        ):
            coerced[norm_key] = ",".join(str(v) for v in value)
        else:
            coerced[norm_key] = value
    return coerced
