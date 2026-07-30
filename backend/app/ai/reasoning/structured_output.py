"""A minimal, dependency-free JSON-Schema-*like* validator.

Deliberately not the ``jsonschema`` package — this platform's convention (see
``app.ai.embeddings.math``'s "no numpy" docstring) is to avoid a dependency for a handful of
checks a call site actually needs. Supports exactly what this platform's own tool/reasoning output
shapes use: ``type`` (object/string/integer/number/boolean/array/null), ``required``,
``properties``, ``items``, and a plain list of ``enum`` values — enough for "structured output is
schema-validated before it leaves the platform" without pulling in a general-purpose schema engine.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    # bool is an int subclass in Python; excluded from "integer"/"number" so a bool value cannot
    # silently satisfy a numeric schema field.
    "integer": (int,),
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate(payload: Any, schema: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Return ``(is_valid, errors)``. Never raises on invalid input — invalidity is data (matches
    ``app.ai.interfaces.planner.Validator``'s own contract)."""
    errors: list[str] = []
    _validate(payload, schema, path="$", errors=errors)
    return not errors, errors


def _validate(payload: Any, schema: Mapping[str, Any], *, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(payload, expected_type):
        errors.append(f"{path}: expected type '{expected_type}', got '{type(payload).__name__}'")
        return  # further checks would be meaningless against the wrong shape

    enum = schema.get("enum")
    if enum is not None and payload not in enum:
        errors.append(f"{path}: value {payload!r} is not one of {list(enum)}")

    if expected_type == "object":
        _validate_object(payload, schema, path=path, errors=errors)
    elif expected_type == "array":
        _validate_array(payload, schema, path=path, errors=errors)


def _matches_type(payload: Any, expected_type: str) -> bool:
    checker = _TYPE_CHECKS.get(expected_type)
    if checker is None:
        return True  # an unknown type name is not this validator's job to reject
    if expected_type in ("integer", "number") and isinstance(payload, bool):
        return False
    return isinstance(payload, checker)


def _validate_object(
    payload: Any, schema: Mapping[str, Any], *, path: str, errors: list[str]
) -> None:
    if not isinstance(payload, dict):
        return  # the type check above already recorded this
    required: Sequence[str] = schema.get("required", ())
    for field in required:
        if field not in payload:
            errors.append(f"{path}: missing required field '{field}'")
    properties: Mapping[str, Any] = schema.get("properties", {})
    for field, field_schema in properties.items():
        if field in payload:
            _validate(payload[field], field_schema, path=f"{path}.{field}", errors=errors)


def _validate_array(
    payload: Any, schema: Mapping[str, Any], *, path: str, errors: list[str]
) -> None:
    if not isinstance(payload, list):
        return
    item_schema = schema.get("items")
    if item_schema is None:
        return
    for index, item in enumerate(payload):
        _validate(item, item_schema, path=f"{path}[{index}]", errors=errors)


__all__ = ["validate"]
