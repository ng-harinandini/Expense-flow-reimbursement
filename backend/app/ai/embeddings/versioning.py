"""Embedding version resolution and comparability rules (Task 4).

The single idea this module exists to enforce: **vectors are only comparable within one
``spec_key``.** A spec key is ``provider/model@version``, and it is written beside every stored
vector. Cosine similarity between vectors from two different models is arithmetically valid and
semantically meaningless, which makes it the most dangerous failure mode in a retrieval system —
nothing errors, results simply become noise.

So the platform:

* stamps every vector with its spec key (``knowledge_embeddings.spec_key``),
* refuses a query whose spec key differs from the index's
  (:class:`~app.ai.core.errors.EmbeddingVersionConflictError`),
* treats a model change as a **new version indexed alongside the old**, never an overwrite, so a
  re-index is reversible and can run incrementally while the old version still serves queries.

``version`` is bumped by hand (``AI_EMBEDDING_VERSION``) rather than derived from the model name,
because the thing that invalidates a vector is not only the model id: changed pooling, a changed
instruction prefix, or a changed normalization all produce incomparable vectors from the *same*
model. A human deciding "this changed" is more reliable than any automatic fingerprint.
"""

from __future__ import annotations

import re
from typing import Optional

from app.ai.core.config import AISettings, ai_settings
from app.ai.core.enums import DistanceMetric
from app.ai.core.errors import EmbeddingVersionConflictError
from app.ai.core.types import EmbeddingSpec

# provider/model@version. The model may contain slashes ("BAAI/bge-m3") but never an "@" — so
# exactly one "@" is allowed in the whole key. A greedy ``.+`` for the model would accept
# "p/m@v1@v2" by reading the model as "m@v1", which silently invents a provenance that was never
# written.
_SPEC_KEY_PATTERN = re.compile(r"^(?P<provider>[^/@]+)/(?P<model>[^@]+)@(?P<version>[^@]+)$")


def build_spec(settings: Optional[AISettings] = None) -> EmbeddingSpec:
    """The :class:`EmbeddingSpec` the platform is currently configured for."""
    config = settings or ai_settings
    return EmbeddingSpec(
        provider=config.EMBEDDING_PROVIDER,
        model=config.EMBEDDING_MODEL,
        dimensions=config.EMBEDDING_DIMENSIONS,
        version=config.EMBEDDING_VERSION,
        metric=config.VECTOR_DISTANCE_METRIC,
        normalized=config.EMBEDDING_NORMALIZE,
        max_input_tokens=config.EMBEDDING_MAX_INPUT_TOKENS,
        cost_per_million_tokens_usd=config.EMBEDDING_COST_PER_MILLION_TOKENS_USD,
    )


def parse_spec_key(spec_key: str) -> tuple[str, str, str]:
    """Split ``provider/model@version`` into its three parts.

    Needed when reading vectors back: the row carries the spec key, and reporting or a rollback
    needs the parts. Raises rather than guessing, because a malformed key means something wrote a
    vector without a resolvable provenance.
    """
    match = _SPEC_KEY_PATTERN.match(spec_key.strip())
    if match is None:
        raise ValueError(
            f"'{spec_key}' is not a valid embedding spec key. "
            "Expected 'provider/model@version', e.g. 'bge_m3_onnx/BAAI/bge-m3@v1'."
        )
    return match.group("provider"), match.group("model"), match.group("version")


def is_valid_spec_key(spec_key: str) -> bool:
    return bool(_SPEC_KEY_PATTERN.match((spec_key or "").strip()))


def assert_comparable(index_spec_key: str, query_spec_key: str) -> None:
    """Guard a similarity comparison.

    Called before every vector search. Deliberately an exact string comparison — not "same model,
    different version is probably fine". A version bump exists precisely to say the vectors changed,
    so honouring it loosely would defeat its only purpose.
    """
    if index_spec_key != query_spec_key:
        raise EmbeddingVersionConflictError(index_spec_key, query_spec_key)


def resolve_query_spec_key(
    requested: Optional[str], *, active_spec_key: str, indexed_spec_keys: tuple[str, ...] = ()
) -> str:
    """Decide which embedding version a query should run against.

    Precedence:

    1. An explicitly requested version wins — that is how a caller reproduces an old answer, or
       queries the previous version while a re-index is still in flight.
    2. Otherwise the active configured version, **if the index actually holds vectors for it**.
    3. Otherwise, if the index holds exactly one version, use that and let the caller see it in the
       response metadata.

    Case 3 is the one that matters operationally: right after a model upgrade the configuration
    names v2 while the index still only has v1. Failing there would take retrieval down for the
    duration of the re-index; answering from v1 and reporting which version was used keeps the
    system available and honest.
    """
    if requested:
        if not is_valid_spec_key(requested):
            raise ValueError(
                f"'{requested}' is not a valid embedding spec key "
                "('provider/model@version')."
            )
        return requested

    if not indexed_spec_keys or active_spec_key in indexed_spec_keys:
        return active_spec_key

    if len(indexed_spec_keys) == 1:
        return indexed_spec_keys[0]

    # Several versions present and none is the configured one: the platform must not pick for the
    # caller, because every choice silently changes the results.
    raise EmbeddingVersionConflictError(
        ", ".join(sorted(indexed_spec_keys)), active_spec_key
    )


def dimensions_for_metric(spec: EmbeddingSpec) -> DistanceMetric:
    """The metric a spec should actually be searched with.

    Unit-length vectors rank identically under cosine and inner product, so a normalized spec that
    asks for inner product is silently downgraded to cosine — the vector store's index is built for
    cosine, and honouring the request would mean either a second index or a wrong ordering.
    """
    if spec.normalized and spec.metric == DistanceMetric.INNER_PRODUCT:
        return DistanceMetric.COSINE
    return spec.metric


def describe_spec(spec: EmbeddingSpec) -> dict[str, object]:
    """Serialisable description, for API responses and the governance registry."""
    return {
        "provider": spec.provider,
        "model": spec.model,
        "version": spec.version,
        "specKey": spec.key,
        "dimensions": spec.dimensions,
        "metric": spec.metric.value,
        "normalized": spec.normalized,
        "maxInputTokens": spec.max_input_tokens,
        "costPerMillionTokensUsd": spec.cost_per_million_tokens_usd,
    }


__all__ = [
    "assert_comparable",
    "build_spec",
    "describe_spec",
    "dimensions_for_metric",
    "is_valid_spec_key",
    "parse_spec_key",
    "resolve_query_spec_key",
]
