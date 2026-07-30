"""``SchemaValidator`` — the concrete ``app.ai.interfaces.planner.Validator`` this platform
ships."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.ai.reasoning.structured_output import validate


class SchemaValidator:
    def validate(
        self, payload: Mapping[str, Any], schema: Mapping[str, Any]
    ) -> tuple[bool, Sequence[str]]:
        return validate(payload, schema)


__all__ = ["SchemaValidator"]
