"""JSON — flattened to readable ``key: value`` lines rather than indexed as raw syntax.

A knowledge source can legitimately arrive as JSON (an exported decision log, a structured vendor
catalogue). Indexing the raw ``{"a":{"b":1}}`` text would make lexical search match on punctuation
noise and give a reader nothing citable. Flattening to dotted paths keeps the information searchable
and readable without asserting a schema the source was never guaranteed to follow.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from app.ai.core.errors import ProviderError
from app.ai.core.types import DocumentSection, RawDocument
from app.ai.parsing.base import DEFAULT_MAX_BYTES, BaseDocumentParser, single_section
from app.ai.parsing.text import decode_best_effort

NAME = "json"
MIME_TYPES = frozenset({"application/json"})
EXTENSIONS = frozenset({".json"})

# A single array/object nested this deep is far more likely to be malformed or degenerate (e.g. a
# self-referential structure fed through a permissive serializer) than a genuine knowledge document,
# so flattening stops rather than recursing indefinitely.
MAX_DEPTH = 20


class JsonParser(BaseDocumentParser):
    """Structured JSON, flattened to one ``path: value`` line per leaf."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None) -> None:
        super().__init__(
            name=NAME, mime_types=MIME_TYPES, extensions=EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )

    def is_available(self) -> bool:
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        raw = decode_best_effort(document.content)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(NAME, f"invalid JSON: {exc}") from exc

        lines = list(_flatten(data))
        text = "\n".join(lines)
        return text, single_section(text)


def _flatten(value: Any, *, prefix: str = "", depth: int = 0) -> Sequence[str]:
    if depth > MAX_DEPTH:
        return [f"{prefix}: <truncated at depth {MAX_DEPTH}>"]
    if isinstance(value, dict):
        out: list[str] = []
        for key in value:
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten(value[key], prefix=path, depth=depth + 1))
        return out
    if isinstance(value, list):
        out = []
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            out.extend(_flatten(item, prefix=path, depth=depth + 1))
        return out
    return [f"{prefix}: {_scalar(value)}"] if prefix else [_scalar(value)]


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


__all__ = ["EXTENSIONS", "MAX_DEPTH", "MIME_TYPES", "NAME", "JsonParser"]
