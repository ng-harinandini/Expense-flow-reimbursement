"""The output schema every built-in read-only tool's result satisfies.

Validated by :func:`app.ai.reasoning.structured_output.validate` before a tool's output is
considered done — Task 16's "structured output is schema-validated before it leaves the platform."
"""

from __future__ import annotations

from typing import Any

CONTEXT_BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text", "tokenCount", "chunkCount", "truncated", "citations"],
    "properties": {
        "text": {"type": "string"},
        "tokenCount": {"type": "integer"},
        "chunkCount": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["documentId", "documentTitle", "documentVersion"],
                "properties": {
                    "documentId": {"type": "string"},
                    "documentTitle": {"type": "string"},
                    "documentVersion": {"type": "integer"},
                },
            },
        },
    },
}

__all__ = ["CONTEXT_BUNDLE_SCHEMA"]
