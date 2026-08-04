"""Embedding platform (Task 4): the framework, version resolution and vector maths.

Vendors live in :mod:`app.ai.providers.embeddings`; this package is what the rest of the platform
uses. Nothing here names a model.
"""

from app.ai.embeddings.service import EmbeddingService
from app.ai.embeddings.versioning import (
    assert_comparable,
    build_spec,
    describe_spec,
    is_valid_spec_key,
    parse_spec_key,
    resolve_query_spec_key,
)

__all__ = [
    "EmbeddingService",
    "assert_comparable",
    "build_spec",
    "describe_spec",
    "is_valid_spec_key",
    "parse_spec_key",
    "resolve_query_spec_key",
]
