"""Embedding platform tests (T004-M5).

Three layers:

1. **A shared provider contract suite**, parametrized over every adapter whose environment is
   satisfied. Task 4's promise is that seven providers are interchangeable; that claim is only
   credible if one suite validates all of them, so the clauses in
   ``app.ai.interfaces.embeddings.EmbeddingProvider`` are asserted here rather than described.
   The deterministic provider always runs; ``bge_m3_onnx`` joins in when the weights are cached;
   the cloud adapters run against a stubbed transport.
2. **`EmbeddingService` behaviour** — batching, cache-aware ordering, retry policy, contract
   enforcement at the boundary, cost accounting.
3. **Version resolution**, the rules that decide what may be compared to what.

No network and no credentials are required. If the bge-m3 weights happen to be cached locally, the
suite additionally asserts real semantic behaviour; otherwise those tests skip with a clear reason.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

import pytest

from app.ai.core.config import BGE_M3_DIMENSIONS, AISettings
from app.ai.core.errors import (
    AIValidationError,
    EmbeddingVersionConflictError,
    ProviderError,
    ProviderNotConfiguredError,
)
from app.ai.core.ids import embedding_cache_key
from app.ai.core.types import EmbeddingResult, EmbeddingSpec, EmbeddingUsage, EmbeddingVector
from app.ai.embeddings import EmbeddingService
from app.ai.embeddings.math import (
    cosine_distance,
    cosine_similarity,
    dot,
    euclidean_distance,
    is_normalized,
    l2_norm,
    mean_vector,
    normalize,
    similarity_from_distance,
    top_k_by_similarity,
)
from app.ai.embeddings.versioning import (
    assert_comparable,
    build_spec,
    describe_spec,
    dimensions_for_metric,
    is_valid_spec_key,
    parse_spec_key,
    resolve_query_spec_key,
)
from app.ai.interfaces.embeddings import EmbeddingProvider, TokenCounter
from app.ai.providers.cache import MemoryCache
from app.ai.providers.embeddings import (
    BgeM3OnnxEmbeddingProvider,
    CohereEmbeddingProvider,
    DeterministicEmbeddingProvider,
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
    register_embedding_providers,
    resolve_embedding_provider,
)
from app.ai.registry.registry import embedding_registry
from app.ai.telemetry import build_recorder

SMALL_DIMS = 64


# ---------------------------------------------------------------------------
# vector maths
# ---------------------------------------------------------------------------


def test_normalize_produces_a_unit_vector() -> None:
    assert is_normalized(normalize([3.0, 4.0]))
    assert l2_norm(normalize([3.0, 4.0])) == pytest.approx(1.0)


def test_normalize_leaves_a_degenerate_vector_alone() -> None:
    """An empty chunk can legitimately embed to zero; failing the batch would be worse.

    Normalizing it would amplify floating-point noise into a meaningless direction.
    """
    assert normalize([0.0, 0.0, 0.0]) == (0.0, 0.0, 0.0)


def test_cosine_similarity_of_identical_vectors_is_one() -> None:
    vector = normalize([0.3, 0.5, 0.81])
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)


def test_cosine_similarity_is_clamped_to_the_valid_range() -> None:
    """Accumulated float error can push an identical pair past 1.0; a caller comparing against a
    threshold of exactly 1.0 would then behave inconsistently."""
    vector = [1e-8] * 32
    assert -1.0 <= cosine_similarity(vector, vector) <= 1.0


def test_cosine_similarity_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_of_opposite_vectors_is_minus_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_similarity_with_a_zero_vector_is_zero_not_an_error() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_mismatched_widths_are_rejected() -> None:
    for operation in (cosine_similarity, dot, euclidean_distance):
        with pytest.raises(ValueError, match="dimensional"):
            operation([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cosine_distance_matches_the_pgvector_operator_convention() -> None:
    """``<=>`` returns ``1 - similarity``; the Python path must agree with the SQL path."""
    left, right = normalize([1.0, 2.0, 3.0]), normalize([1.0, 2.1, 2.9])
    assert cosine_distance(left, right) == pytest.approx(1.0 - cosine_similarity(left, right))


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(0.0, 1.0), (0.5, 0.5), (1.0, 0.0), (1.5, 0.0), (2.0, 0.0), (-0.001, 1.0), (2.5, 0.0)],
)
def test_similarity_from_distance_is_bounded(distance: float, expected: float) -> None:
    """Every adapter must return ``[0, 1]`` scores whatever its engine natively produces, including
    values marginally outside the theoretical range from an approximate index."""
    assert similarity_from_distance(distance) == pytest.approx(expected)


def test_similarity_from_distance_never_rewards_a_further_vector() -> None:
    """Monotone non-increasing, over the whole cosine range including negative similarity.

    Load-bearing rather than a nicety: every adapter orders by the engine's ascending *distance* and
    converts to a score afterwards, so a conversion with a bump in it would return results whose
    scores contradict their own ordering. A previous implementation halved distances above 1.0 and
    scored 1.5 higher than 1.0.
    """
    scores = [similarity_from_distance(d / 100.0) for d in range(201)]
    assert all(
        earlier >= later for earlier, later in zip(scores[:-1], scores[1:], strict=True)
    ), "similarity must never increase as distance increases"


def test_mean_vector_is_the_centroid() -> None:
    assert mean_vector([[0.0, 0.0], [2.0, 4.0]]) == (1.0, 2.0)


def test_mean_vector_rejects_empty_or_ragged_input() -> None:
    with pytest.raises(ValueError):
        mean_vector([])
    with pytest.raises(ValueError, match="same dimensionality"):
        mean_vector([[1.0], [1.0, 2.0]])


def test_top_k_breaks_score_ties_deterministically() -> None:
    """The ``VectorStore`` contract requires stable paging; equal scores must not reorder."""
    query = [1.0, 0.0]
    candidates = [("b", [1.0, 0.0]), ("a", [1.0, 0.0]), ("c", [0.0, 1.0])]
    assert [i for i, _ in top_k_by_similarity(query, candidates, k=3)] == ["a", "b", "c"]
    assert top_k_by_similarity(query, candidates, k=3) == top_k_by_similarity(
        query, candidates, k=3
    )


def test_top_k_applies_a_threshold() -> None:
    results = top_k_by_similarity(
        [1.0, 0.0], [("near", [1.0, 0.0]), ("far", [0.0, 1.0])], k=5, threshold=0.5
    )
    assert [i for i, _ in results] == ["near"]


# ---------------------------------------------------------------------------
# the shared provider contract suite
# ---------------------------------------------------------------------------


def _canned_vector(text: str, dimensions: int) -> list[float]:
    """A deterministic, **unnormalized** vector derived from ``text``.

    Unnormalized on purpose, so the contract clause "the adapter normalizes what the vendor returns"
    is actually being tested. Derived from the text rather than the position so the fake behaves
    like a real embedder: the same input always yields the same vector, which is what the
    determinism and ordering clauses depend on.
    """
    seed = sum(ord(c) * (i + 1) for i, c in enumerate(text)) % 997
    return [math.sin(seed * 0.31 + j * 0.017) * 3.0 for j in range(dimensions)]


class _StubTransport:
    """Stands in for ``httpx.Client``, shaping its reply to the request it was given.

    Responding with a fixed number of vectors regardless of input would make the fake, not the
    adapter, the thing under test — and would trip the service's own count check.
    """

    def __init__(self, shape: str, *, dimensions: int = SMALL_DIMS,
                 status_code: int = 200) -> None:
        self.shape = shape
        self.dimensions = dimensions
        self.status_code = status_code
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, *, headers: dict, json: dict) -> Any:
        self.requests.append({"url": url, "headers": headers, "json": json})
        self._last = json
        return self

    @property
    def text(self) -> str:
        return "stub error body"

    def _texts(self) -> list[str]:
        payload = getattr(self, "_last", {})
        for key in ("input", "texts", "inputs"):
            if key in payload:
                value = payload[key]
                return list(value) if isinstance(value, list) else [value]
        return []

    def json(self) -> Any:
        texts = self._texts()
        vectors = [_canned_vector(t, self.dimensions) for t in texts]

        if self.shape == "openai":
            # Deliberately reversed: the adapter must sort by ``index``, not trust arrival order.
            data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
            return {"data": list(reversed(data)), "usage": {"total_tokens": len(texts) * 5}}
        if self.shape == "cohere":
            return {
                "embeddings": {"float": vectors},
                "meta": {"billed_units": {"input_tokens": len(texts) * 4}},
            }
        if self.shape == "voyage":
            return {
                "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
                "usage": {"total_tokens": len(texts) * 3},
            }
        raise AssertionError(self.shape)  # pragma: no cover

    def close(self) -> None:
        return None


def _stub_client(cls: type, shape: str, monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    """Patch ``cls._client`` to return a fresh stub; returns a list of each call's request log."""
    logs: list[list[dict]] = []

    def client(_self: Any) -> _StubTransport:
        stub = _StubTransport(shape)
        logs.append(stub.requests)
        return stub

    monkeypatch.setattr(cls, "_client", client)
    return logs


def _openai_provider(monkeypatch: pytest.MonkeyPatch) -> OpenAIEmbeddingProvider:
    _stub_client(OpenAIEmbeddingProvider, "openai", monkeypatch)
    return OpenAIEmbeddingProvider(
        api_key="test-key", model="text-embedding-3-large",
        dimensions=SMALL_DIMS, version="v1",
    )


def _cohere_provider(monkeypatch: pytest.MonkeyPatch) -> CohereEmbeddingProvider:
    _stub_client(CohereEmbeddingProvider, "cohere", monkeypatch)
    return CohereEmbeddingProvider(
        api_key="test-key", model="embed-v4.0", dimensions=SMALL_DIMS, version="v1"
    )


def _voyage_provider(monkeypatch: pytest.MonkeyPatch) -> VoyageEmbeddingProvider:
    _stub_client(VoyageEmbeddingProvider, "voyage", monkeypatch)
    return VoyageEmbeddingProvider(
        api_key="test-key", model="voyage-3-large", dimensions=SMALL_DIMS, version="v1"
    )


def _bge_available() -> bool:
    """Whether the bge-m3 ONNX weights are already cached, without downloading 2.3 GB."""
    return BgeM3OnnxEmbeddingProvider(allow_download=False).is_available()


# One ONNX session for the whole module. Each ``InferenceSession`` costs ~4s to build and holds
# ~2.3 GB of weights, so constructing one per test made this module take two minutes on its own.
# The provider is stateless after loading, so sharing it is safe.
_BGE_CACHE: dict[str, Any] = {}


@pytest.fixture(scope="module")
def bge_provider() -> BgeM3OnnxEmbeddingProvider:
    if not _bge_available():
        pytest.skip("bge-m3 ONNX weights are not cached locally (~2.3 GB download)")
    if "provider" not in _BGE_CACHE:
        _BGE_CACHE["provider"] = BgeM3OnnxEmbeddingProvider(
            version="v1", allow_download=False
        )
    return _BGE_CACHE["provider"]


CONTRACT_PROVIDERS = ["deterministic", "openai", "cohere", "voyage", "bge_m3_onnx"]


@pytest.fixture(params=CONTRACT_PROVIDERS)
def contract_provider(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """One provider per parametrization, skipping any whose environment is unsatisfied."""
    name = request.param
    if name == "deterministic":
        return DeterministicEmbeddingProvider(dimensions=SMALL_DIMS, version="v1")
    if name == "openai":
        return _openai_provider(monkeypatch)
    if name == "cohere":
        return _cohere_provider(monkeypatch)
    if name == "voyage":
        return _voyage_provider(monkeypatch)
    if name == "bge_m3_onnx":
        if not _bge_available():
            pytest.skip("bge-m3 ONNX weights are not cached locally (~2.3 GB download)")
        if "provider" not in _BGE_CACHE:
            _BGE_CACHE["provider"] = BgeM3OnnxEmbeddingProvider(
                version="v1", allow_download=False
            )
        return _BGE_CACHE["provider"]
    raise AssertionError(name)  # pragma: no cover


# --- contract clauses -------------------------------------------------------


def test_contract_provider_satisfies_the_protocol(contract_provider) -> None:
    assert isinstance(contract_provider, EmbeddingProvider)


def test_contract_spec_is_self_consistent(contract_provider) -> None:
    spec = contract_provider.spec
    assert isinstance(spec, EmbeddingSpec)
    assert spec.dimensions > 0
    assert spec.key == f"{spec.provider}/{spec.model}@{spec.version}"


def test_contract_is_available_never_raises(contract_provider) -> None:
    """The registry calls this to decide on fallback; an exception would defeat that path."""
    assert isinstance(contract_provider.is_available(), bool)


def test_contract_returns_one_vector_per_input_in_order(contract_provider) -> None:
    """Clause 1. Mis-ordering pairs vectors with the wrong chunks and never raises."""
    texts = ["the daily meal allowance is 50 USD", "lodging is capped at 200 USD"]
    result = contract_provider.embed_documents(texts)
    assert isinstance(result, EmbeddingResult)
    assert len(result.vectors) == len(texts)

    # Each vector must match the one produced for that text on its own.
    for text, vector in zip(texts, result.vectors, strict=True):
        alone = contract_provider.embed_documents([text]).vectors[0]
        assert cosine_similarity(vector.values, alone.values) == pytest.approx(1.0, abs=1e-5)


def test_contract_vectors_have_the_declared_width(contract_provider) -> None:
    """Clause 2."""
    spec = contract_provider.spec
    for vector in contract_provider.embed_documents(["policy text"]).vectors:
        assert vector.dimensions == spec.dimensions
        assert len(vector.values) == spec.dimensions


def test_contract_is_deterministic_within_a_process(contract_provider) -> None:
    """Clause 3. Without this, a cache would serve vectors that differ from a fresh call."""
    first = contract_provider.embed_documents(["a stable input"]).vectors[0]
    second = contract_provider.embed_documents(["a stable input"]).vectors[0]
    assert first.values == second.values


def test_contract_honours_its_normalization_claim(contract_provider) -> None:
    """Clause 4. A provider claiming normalized vectors but not returning them makes cosine and
    inner-product ranking diverge silently."""
    spec = contract_provider.spec
    if not spec.normalized:
        pytest.skip("provider does not claim normalized vectors")
    for vector in contract_provider.embed_documents(["normalize me"]).vectors:
        assert is_normalized(vector.values), f"norm was {l2_norm(vector.values)}"


def test_contract_query_vector_is_comparable_to_document_vectors(contract_provider) -> None:
    """Clause 5. Asymmetric models use a different prefix for queries, but the two must still
    live in one space — otherwise no query ever matches anything."""
    spec = contract_provider.spec
    query = contract_provider.embed_query("what is the meal allowance?")
    assert query.dimensions == spec.dimensions
    assert query.spec_key == spec.key

    document = contract_provider.embed_documents(["the meal allowance is 50 USD"]).vectors[0]
    similarity = cosine_similarity(query.values, document.values)
    assert -1.0 <= similarity <= 1.0


def test_contract_stamps_its_spec_key_on_every_vector(contract_provider) -> None:
    spec = contract_provider.spec
    for vector in contract_provider.embed_documents(["x", "y"]).vectors:
        assert vector.spec_key == spec.key


def test_contract_empty_input_returns_empty_without_io(contract_provider) -> None:
    """Clause 6. A provider that calls out for an empty batch wastes a request and may be
    billed for it."""
    result = contract_provider.embed_documents([])
    assert result.vectors == ()
    assert result.usage.texts == 0


def test_contract_reports_usage(contract_provider) -> None:
    usage = contract_provider.embed_documents(["some text to account for"]).usage
    assert isinstance(usage, EmbeddingUsage)
    assert usage.texts == 1
    assert usage.latency_ms >= 0
    assert usage.cost_usd >= 0.0


# --- vendor-specific details the shared suite cannot express ----------------


def test_openai_sorts_response_by_index_not_arrival_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API does not guarantee response order; ``index`` is authoritative.

    The stub returns its ``data`` reversed, so an adapter that trusted arrival order would pair each
    vector with the wrong text — and nothing would raise.
    """
    provider = _openai_provider(monkeypatch)
    vectors = provider.embed_documents(["first text", "second text"]).vectors
    assert cosine_similarity(
        vectors[0].values, normalize(_canned_vector("first text", SMALL_DIMS))
    ) == pytest.approx(1.0)
    assert cosine_similarity(
        vectors[1].values, normalize(_canned_vector("second text", SMALL_DIMS))
    ) == pytest.approx(1.0)


def test_cohere_uses_a_different_input_type_for_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Getting this wrong measurably reduces recall — Cohere trains the two roles apart."""
    logs = _stub_client(CohereEmbeddingProvider, "cohere", monkeypatch)
    provider = CohereEmbeddingProvider(
        api_key="k", model="embed-v4.0", dimensions=SMALL_DIMS, version="v1"
    )

    provider.embed_documents(["a passage"])
    assert logs[0][0]["json"]["input_type"] == "search_document"
    provider.embed_query("a question")
    assert logs[1][0]["json"]["input_type"] == "search_query"


def test_voyage_uses_a_different_input_type_for_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs = _stub_client(VoyageEmbeddingProvider, "voyage", monkeypatch)
    provider = VoyageEmbeddingProvider(
        api_key="k", model="voyage-3-large", dimensions=SMALL_DIMS, version="v1"
    )
    provider.embed_documents(["a passage"])
    assert logs[0][0]["json"]["input_type"] == "document"
    provider.embed_query("a question")
    assert logs[1][0]["json"]["input_type"] == "query"


def test_http_provider_reports_an_error_status_without_echoing_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error body can contain the submitted receipt text; it must be truncated hard."""
    def client(_self: Any) -> _StubTransport:
        return _StubTransport("openai", status_code=429)

    monkeypatch.setattr(OpenAIEmbeddingProvider, "_client", client)
    provider = OpenAIEmbeddingProvider(
        api_key="k", model="text-embedding-3-large", dimensions=SMALL_DIMS, version="v1"
    )
    with pytest.raises(ProviderError, match="HTTP 429"):
        provider.embed_documents(["x"])


def test_http_provider_rejects_a_batch_over_its_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _cohere_provider(monkeypatch)
    with pytest.raises(ProviderError, match="exceeds this provider's ceiling"):
        provider.embed_documents(["x"] * (provider.max_batch + 1))


def test_provider_without_credentials_reports_unavailable_and_names_the_remedy() -> None:
    provider = OpenAIEmbeddingProvider(
        api_key=None, model="text-embedding-3-large", dimensions=SMALL_DIMS, version="v1"
    )
    assert provider.is_available() is False
    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        provider.embed_documents(["x"])
    assert "API key" in excinfo.value.details["remedy"]


# --- the deterministic provider's own guarantees ---------------------------


def test_deterministic_provider_declares_it_is_not_semantic() -> None:
    """The platform must never let a plumbing stand-in be mistaken for a real model."""
    assert DeterministicEmbeddingProvider(dimensions=SMALL_DIMS).is_semantic is False
    assert BgeM3OnnxEmbeddingProvider().is_semantic is True


def test_deterministic_provider_is_reproducible_across_instances() -> None:
    """BLAKE2b, not ``hash()``: a per-process seed would corrupt an index across a redeploy."""
    first = DeterministicEmbeddingProvider(dimensions=SMALL_DIMS)
    second = DeterministicEmbeddingProvider(dimensions=SMALL_DIMS)
    assert (
        first.embed_query("meal allowance").values
        == second.embed_query("meal allowance").values
    )


def test_deterministic_provider_separates_unrelated_texts() -> None:
    """Signed hashing, not counts. With counts every vector sits in the positive orthant and any two
    texts look similar, which would make retrieval appear to work while ranking at random."""
    provider = DeterministicEmbeddingProvider(dimensions=512)
    left = provider.embed_query("the meal allowance for domestic travel is 50 USD")
    right = provider.embed_query("quarterly revenue grew twelve percent in Asia Pacific")
    assert cosine_similarity(left.values, right.values) < 0.5


def test_deterministic_provider_handles_blank_text() -> None:
    vector = DeterministicEmbeddingProvider(dimensions=SMALL_DIMS).embed_query("   ")
    assert len(vector.values) == SMALL_DIMS


def test_deterministic_provider_rejects_a_useless_width() -> None:
    with pytest.raises(ValueError):
        DeterministicEmbeddingProvider(dimensions=4)


# ---------------------------------------------------------------------------
# EmbeddingService
# ---------------------------------------------------------------------------


class _CountingProvider:
    """Records how many texts it was asked to embed, and in how many calls."""

    name = "counting"
    is_semantic = True

    def __init__(self, *, dimensions: int = SMALL_DIMS, fail_times: int = 0) -> None:
        self._inner = DeterministicEmbeddingProvider(dimensions=dimensions, version="v1")
        self.calls: list[int] = []
        self.total_texts = 0
        self._fail_times = fail_times

    @property
    def spec(self) -> EmbeddingSpec:
        return self._inner.spec

    def is_available(self) -> bool:
        return True

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ProviderError(self.name, "transient upstream failure")
        self.calls.append(len(texts))
        self.total_texts += len(texts)
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> EmbeddingVector:
        return self._inner.embed_query(text)


@pytest.fixture
def service_settings() -> AISettings:
    return AISettings(EMBEDDING_DIMENSIONS=SMALL_DIMS, EMBEDDING_BATCH_SIZE=2,
                      EMBEDDING_MAX_RETRIES=2)


def test_service_returns_vectors_in_the_callers_order(service_settings: AISettings) -> None:
    provider = _CountingProvider()
    service = EmbeddingService(provider, settings=service_settings)
    texts = [f"chunk number {i}" for i in range(7)]

    vectors = service.embed_documents(texts).vectors
    assert len(vectors) == 7
    for text, vector in zip(texts, vectors, strict=True):
        expected = provider.embed_query(text)
        assert vector.values == expected.values


def test_service_batches_according_to_configuration(service_settings: AISettings) -> None:
    provider = _CountingProvider()
    service = EmbeddingService(provider, settings=service_settings)
    service.embed_documents([f"t{i}" for i in range(5)])
    assert provider.calls == [2, 2, 1], "batch size 2 over 5 inputs"


def test_service_serves_repeats_from_cache(service_settings: AISettings) -> None:
    """Re-embedding unchanged policy text is the largest avoidable cost in the platform."""
    provider = _CountingProvider()
    cache = MemoryCache()
    service = EmbeddingService(provider, cache=cache, settings=service_settings)

    first = service.embed_documents(["repeat me", "and me"])
    assert first.usage.cache_misses == 2
    assert provider.total_texts == 2

    second = service.embed_documents(["repeat me", "and me"])
    assert second.usage.cache_hits == 2
    assert provider.total_texts == 2, "the provider must not be called again"
    assert second.vectors[0].values == first.vectors[0].values


def test_service_preserves_order_when_only_some_inputs_are_cached(
    service_settings: AISettings,
) -> None:
    """The subtle one: recombining cached and fresh vectors. Getting it wrong pairs vectors with the
    wrong chunks and never raises."""
    provider = _CountingProvider()
    cache = MemoryCache()
    service = EmbeddingService(provider, cache=cache, settings=service_settings)

    texts = [f"text {i}" for i in range(6)]
    service.embed_documents([texts[1], texts[3], texts[4]])   # warm a subset

    result = service.embed_documents(texts)
    assert result.usage.cache_hits == 3
    assert result.usage.cache_misses == 3
    for text, vector in zip(texts, result.vectors, strict=True):
        assert vector.values == provider.embed_query(text).values


def test_service_cache_key_includes_the_model_version(service_settings: AISettings) -> None:
    """Keying on text alone is how a model upgrade serves the previous model's vectors out of
    the cache."""
    cache = MemoryCache()
    service_v1 = EmbeddingService(
        DeterministicEmbeddingProvider(dimensions=SMALL_DIMS, version="v1"),
        cache=cache, settings=service_settings,
    )
    service_v2 = EmbeddingService(
        DeterministicEmbeddingProvider(dimensions=SMALL_DIMS, version="v2"),
        cache=cache, settings=service_settings,
    )
    service_v1.embed_documents(["shared text"])
    result = service_v2.embed_documents(["shared text"])
    assert result.usage.cache_misses == 1, "v2 must not read v1's cached vector"


def test_service_ignores_a_cached_entry_of_the_wrong_width(
    service_settings: AISettings,
) -> None:
    """A stale entry from a previous model width must be treated as a miss, not trusted."""
    cache = MemoryCache()
    service = EmbeddingService(_CountingProvider(), cache=cache, settings=service_settings)
    cache.set(embedding_cache_key("poisoned", service.spec_key), [0.1, 0.2, 0.3])
    result = service.embed_documents(["poisoned"])
    assert result.usage.cache_misses == 1
    assert result.vectors[0].dimensions == SMALL_DIMS


def test_service_ignores_a_corrupt_cache_entry(service_settings: AISettings) -> None:
    cache = MemoryCache()
    service = EmbeddingService(_CountingProvider(), cache=cache, settings=service_settings)
    cache.set(embedding_cache_key("bad", service.spec_key), "not a vector")
    assert service.embed_documents(["bad"]).usage.cache_misses == 1


def test_service_survives_a_broken_cache_backend(service_settings: AISettings) -> None:
    """A cache that fails the request it exists to accelerate is worse than no cache."""

    class _BrokenCache:
        name = "broken"

        def is_available(self) -> bool:
            return True

        def get(self, key: str) -> None:
            raise RuntimeError("cache down")

        def set(self, key: str, value: Any, *, ttl_seconds: Optional[int] = None) -> None:
            raise RuntimeError("cache down")

    service = EmbeddingService(_CountingProvider(), cache=_BrokenCache(),
                               settings=service_settings)
    assert len(service.embed_documents(["still works"]).vectors) == 1


def test_service_retries_a_transient_failure(service_settings: AISettings) -> None:
    provider = _CountingProvider(fail_times=1)
    service = EmbeddingService(provider, settings=service_settings)
    assert len(service.embed_documents(["eventually succeeds"]).vectors) == 1


def test_service_gives_up_after_the_configured_attempts(
    service_settings: AISettings,
) -> None:
    provider = _CountingProvider(fail_times=99)
    service = EmbeddingService(provider, settings=service_settings)
    with pytest.raises(ProviderError):
        service.embed_documents(["never succeeds"])


def test_service_does_not_retry_a_configuration_error() -> None:
    """Credentials will not appear between attempts; retrying only makes a clear 503 slow."""
    attempts = {"count": 0}

    class _Unconfigured:
        name = "unconfigured"
        spec = EmbeddingSpec(provider="unconfigured", model="m", dimensions=SMALL_DIMS,
                             version="v1")

        def is_available(self) -> bool:
            return False

        def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
            attempts["count"] += 1
            raise ProviderNotConfiguredError("unconfigured", "EMBEDDING", "Set a key.")

        def embed_query(self, text: str) -> EmbeddingVector:
            raise ProviderNotConfiguredError("unconfigured", "EMBEDDING", "Set a key.")

    service = EmbeddingService(
        _Unconfigured(), settings=AISettings(EMBEDDING_MAX_RETRIES=3)
    )
    with pytest.raises(ProviderNotConfiguredError):
        service.embed_documents(["x"])
    assert attempts["count"] == 1


def test_service_rejects_a_provider_returning_the_wrong_width() -> None:
    """Enforcing the contract at the boundary keeps a bad vector out of the index, where it would
    degrade every future comparison silently."""

    class _WrongWidth:
        name = "wrong_width"
        spec = EmbeddingSpec(provider="wrong_width", model="m", dimensions=SMALL_DIMS,
                             version="v1")
        is_semantic = True

        def is_available(self) -> bool:
            return True

        def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple(
                    EmbeddingVector(values=(1.0, 0.0), spec_key=self.spec.key, dimensions=2)
                    for _ in texts
                ),
                spec=self.spec,
            )

        def embed_query(self, text: str) -> EmbeddingVector:
            return EmbeddingVector(values=(1.0, 0.0), spec_key=self.spec.key, dimensions=2)

    service = EmbeddingService(_WrongWidth(), settings=AISettings(
        EMBEDDING_DIMENSIONS=SMALL_DIMS, EMBEDDING_MAX_RETRIES=0))
    with pytest.raises(ProviderError, match="dimensions"):
        service.embed_documents(["x"])


def test_service_rejects_a_provider_returning_a_wrong_count() -> None:
    """A dropped vector would shift every later pairing by one, silently."""

    class _DropsOne:
        name = "drops_one"
        spec = EmbeddingSpec(provider="drops_one", model="m", dimensions=SMALL_DIMS,
                             version="v1")
        is_semantic = True

        def is_available(self) -> bool:
            return True

        def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
            inner = DeterministicEmbeddingProvider(dimensions=SMALL_DIMS, version="v1")
            # Re-stamp with *this* provider's spec key: otherwise the service's spec-key check
            # fires first and this test would pass for the wrong reason.
            vectors = tuple(
                EmbeddingVector(
                    values=v.values, spec_key=self.spec.key, dimensions=v.dimensions
                )
                for v in inner.embed_documents(list(texts)[:-1]).vectors
            )
            return EmbeddingResult(vectors=vectors, spec=self.spec)

        def embed_query(self, text: str) -> EmbeddingVector:
            raise NotImplementedError

    service = EmbeddingService(_DropsOne(), settings=AISettings(
        EMBEDDING_DIMENSIONS=SMALL_DIMS, EMBEDDING_MAX_RETRIES=0))
    with pytest.raises(ProviderError, match="vectors for"):
        service.embed_documents(["a", "b"])


def test_service_rejects_a_provider_stamping_the_wrong_spec_key() -> None:
    """The check that fired during development of the test above.

    A vector stamped with another provider's key would later be compared against vectors it is not
    comparable to — the exact corruption ``spec_key`` exists to prevent.
    """

    class _WrongStamp:
        name = "wrong_stamp"
        spec = EmbeddingSpec(provider="wrong_stamp", model="m", dimensions=SMALL_DIMS,
                             version="v1")
        is_semantic = True

        def is_available(self) -> bool:
            return True

        def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
            inner = DeterministicEmbeddingProvider(dimensions=SMALL_DIMS, version="v1")
            return EmbeddingResult(
                vectors=inner.embed_documents(list(texts)).vectors, spec=self.spec
            )

        def embed_query(self, text: str) -> EmbeddingVector:
            raise NotImplementedError

    service = EmbeddingService(_WrongStamp(), settings=AISettings(
        EMBEDDING_DIMENSIONS=SMALL_DIMS, EMBEDDING_MAX_RETRIES=0))
    with pytest.raises(ProviderError, match="stamped spec key"):
        service.embed_documents(["a"])


def test_service_rejects_an_unnormalized_vector_from_a_normalizing_provider() -> None:
    class _Liar:
        name = "liar"
        spec = EmbeddingSpec(provider="liar", model="m", dimensions=2, version="v1",
                             normalized=True)
        is_semantic = True

        def is_available(self) -> bool:
            return True

        def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple(
                    EmbeddingVector(values=(3.0, 4.0), spec_key=self.spec.key, dimensions=2)
                    for _ in texts
                ),
                spec=self.spec,
            )

        def embed_query(self, text: str) -> EmbeddingVector:
            raise NotImplementedError

    service = EmbeddingService(_Liar(), settings=AISettings(
        EMBEDDING_DIMENSIONS=2, EMBEDDING_MAX_RETRIES=0))
    with pytest.raises(ProviderError, match="non-unit"):
        service.embed_documents(["x"])


def test_service_rejects_empty_and_none_input(service_settings: AISettings) -> None:
    service = EmbeddingService(_CountingProvider(), settings=service_settings)
    assert service.embed_documents([]).vectors == ()
    with pytest.raises(AIValidationError):
        service.embed_documents(["ok", None])  # type: ignore[list-item]
    with pytest.raises(AIValidationError):
        service.embed_query("   ")


def test_service_records_telemetry(service_settings: AISettings) -> None:
    recorder = build_recorder(enabled=True)
    service = EmbeddingService(
        _CountingProvider(), cache=MemoryCache(), recorder=recorder,
        settings=service_settings,
    )
    service.embed_documents(["one", "two"])
    service.embed_documents(["one", "two"])   # now cached

    snapshot = recorder.snapshot()
    assert snapshot["operations"]["EMBED"]["count"] >= 2
    assert snapshot["counters"]["embedding.texts"] >= 2
    assert snapshot["counters"]["embedding.cache_hits"] >= 2


def test_service_describe_surfaces_the_semantic_flag(service_settings: AISettings) -> None:
    """API consumers must be able to tell plumbing vectors from real ones."""
    real = EmbeddingService(_CountingProvider(), settings=service_settings)
    assert real.describe()["isSemantic"] is True

    stand_in = EmbeddingService(
        DeterministicEmbeddingProvider(dimensions=SMALL_DIMS), settings=service_settings
    )
    assert stand_in.describe()["isSemantic"] is False
    assert stand_in.is_semantic is False


def test_service_query_embeddings_are_cached(service_settings: AISettings) -> None:
    provider = _CountingProvider()
    cache = MemoryCache()
    service = EmbeddingService(provider, cache=cache, settings=service_settings)
    first = service.embed_query("what is the meal limit?")
    second = service.embed_query("what is the meal limit?")
    assert first.values == second.values
    assert cache.stats["hits"] >= 1


# ---------------------------------------------------------------------------
# version resolution
# ---------------------------------------------------------------------------


def test_build_spec_reflects_configuration() -> None:
    spec = build_spec(AISettings())
    assert spec.dimensions == BGE_M3_DIMENSIONS
    assert spec.key == "bge_m3_onnx/BAAI/bge-m3@v1"


@pytest.mark.parametrize(
    "spec_key",
    ["bge_m3_onnx/BAAI/bge-m3@v1", "openai/text-embedding-3-large@v2", "p/m@v1"],
)
def test_valid_spec_keys_parse(spec_key: str) -> None:
    provider, model, version = parse_spec_key(spec_key)
    assert f"{provider}/{model}@{version}" == spec_key
    assert is_valid_spec_key(spec_key)


@pytest.mark.parametrize("spec_key", ["no-slash@v1", "p/m", "p/m@v1@v2", "", "   "])
def test_malformed_spec_keys_are_rejected(spec_key: str) -> None:
    """A malformed key means a vector was written without resolvable provenance."""
    assert not is_valid_spec_key(spec_key)
    with pytest.raises(ValueError, match="not a valid embedding spec key"):
        parse_spec_key(spec_key)


def test_model_names_containing_slashes_are_supported() -> None:
    """``BAAI/bge-m3`` contains a slash; a naive split would mangle it."""
    provider, model, version = parse_spec_key("bge_m3_onnx/BAAI/bge-m3@v1")
    assert (provider, model, version) == ("bge_m3_onnx", "BAAI/bge-m3", "v1")


def test_assert_comparable_allows_identical_versions() -> None:
    assert_comparable("p/m@v1", "p/m@v1")


def test_assert_comparable_rejects_a_version_mismatch() -> None:
    """Cosine similarity across two models is valid arithmetic and meaningless semantics — the most
    dangerous failure mode in retrieval, because nothing errors."""
    with pytest.raises(EmbeddingVersionConflictError) as excinfo:
        assert_comparable("p/m@v1", "p/m@v2")
    assert excinfo.value.details == {"expectedVersion": "p/m@v1", "actualVersion": "p/m@v2"}


def test_query_version_defaults_to_the_active_one() -> None:
    assert resolve_query_spec_key(None, active_spec_key="p/m@v2",
                                  indexed_spec_keys=("p/m@v2",)) == "p/m@v2"


def test_query_version_can_be_pinned_to_reproduce_an_old_answer() -> None:
    assert resolve_query_spec_key("p/m@v1", active_spec_key="p/m@v2",
                                  indexed_spec_keys=("p/m@v1", "p/m@v2")) == "p/m@v1"


def test_query_version_falls_back_to_the_only_indexed_version() -> None:
    """Right after a model upgrade the config names v2 while the index still holds only v1. Failing
    would take retrieval down for the whole re-index; answering from v1 keeps it available."""
    assert resolve_query_spec_key(None, active_spec_key="p/m@v2",
                                  indexed_spec_keys=("p/m@v1",)) == "p/m@v1"


def test_query_version_is_ambiguous_when_several_are_indexed_and_none_is_active() -> None:
    """The platform must not choose, because every choice silently changes the results."""
    with pytest.raises(EmbeddingVersionConflictError):
        resolve_query_spec_key(
            None, active_spec_key="p/m@v3", indexed_spec_keys=("p/m@v1", "p/m@v2")
        )


def test_query_version_rejects_a_malformed_request() -> None:
    with pytest.raises(ValueError):
        resolve_query_spec_key("garbage", active_spec_key="p/m@v1")


def test_normalized_inner_product_is_downgraded_to_cosine() -> None:
    """Unit vectors rank identically under both, and the index is built for cosine."""
    from app.ai.core.enums import DistanceMetric

    spec = EmbeddingSpec(provider="p", model="m", dimensions=8, version="v1",
                         metric=DistanceMetric.INNER_PRODUCT, normalized=True)
    assert dimensions_for_metric(spec) is DistanceMetric.COSINE

    unnormalized = EmbeddingSpec(provider="p", model="m", dimensions=8, version="v1",
                                 metric=DistanceMetric.INNER_PRODUCT, normalized=False)
    assert dimensions_for_metric(unnormalized) is DistanceMetric.INNER_PRODUCT


def test_describe_spec_is_serialisable() -> None:
    payload = describe_spec(build_spec(AISettings()))
    assert payload["specKey"] == "bge_m3_onnx/BAAI/bge-m3@v1"
    assert payload["dimensions"] == BGE_M3_DIMENSIONS


# ---------------------------------------------------------------------------
# registry wiring
# ---------------------------------------------------------------------------


def test_all_seven_task_four_providers_are_registered() -> None:
    register_embedding_providers()
    assert set(embedding_registry.names()) == {
        "bge_m3_onnx", "deterministic", "bedrock_titan",
        "openai", "cohere", "voyage", "tei_http",
    }


def test_registering_providers_constructs_nothing() -> None:
    """Must be safe on a machine with no credentials, no boto3/httpx and no model weights."""
    embedding_registry.clear_instances()
    register_embedding_providers()
    assert all(row["instantiated"] is False for row in embedding_registry.describe())


def test_resolution_falls_back_to_deterministic_when_the_default_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mechanism that keeps CI green without the 2.3 GB weights."""
    embedding_registry.clear_instances()
    register_embedding_providers()
    monkeypatch.setattr(BgeM3OnnxEmbeddingProvider, "is_available", lambda self: False)

    provider = resolve_embedding_provider()
    assert provider.name == "deterministic"
    assert provider.is_semantic is False


def test_resolution_raises_when_fallback_is_disabled_and_nothing_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_registry.clear_instances()
    register_embedding_providers()
    monkeypatch.setattr(BgeM3OnnxEmbeddingProvider, "is_available", lambda self: False)
    monkeypatch.setattr(
        "app.ai.core.config.ai_settings.EMBEDDING_FALLBACK_TO_DETERMINISTIC", False
    )
    with pytest.raises(ProviderNotConfiguredError):
        resolve_embedding_provider()


# ---------------------------------------------------------------------------
# bge-m3, when its weights are available locally
# ---------------------------------------------------------------------------


requires_bge = pytest.mark.skipif(
    not _bge_available(),
    reason="bge-m3 ONNX weights are not cached locally (~2.3 GB download)",
)


@requires_bge
def test_bge_m3_produces_1024_unit_dimensions(bge_provider) -> None:
    provider = bge_provider
    vector = provider.embed_query("What is the meal allowance?")
    assert vector.dimensions == BGE_M3_DIMENSIONS
    assert is_normalized(vector.values)


@requires_bge
def test_bge_m3_ranks_a_paraphrase_above_an_unrelated_sentence(bge_provider) -> None:
    """The claim the deterministic provider cannot make: actual semantic similarity."""
    provider = bge_provider
    vectors = provider.embed_documents([
        "The daily meal allowance for L3 employees travelling domestically is 50 USD.",
        "Per diem food reimbursement for grade L3 on domestic trips is fifty dollars.",
        "Quarterly revenue grew twelve percent in the Asia Pacific region.",
    ]).vectors
    paraphrase = cosine_similarity(vectors[0].values, vectors[1].values)
    unrelated = cosine_similarity(vectors[0].values, vectors[2].values)
    assert paraphrase > unrelated + 0.15, f"{paraphrase=:.4f} {unrelated=:.4f}"


@requires_bge
def test_bge_m3_matches_across_languages(bge_provider) -> None:
    """Multilingual is the reason this model was chosen over an English-only one."""
    provider = bge_provider
    vectors = provider.embed_documents([
        "The daily meal allowance for domestic travel is 50 USD.",
        "従業員の国内出張の食事手当は1日50米ドルです。",
        "The office coffee machine is serviced every second Tuesday.",
    ]).vectors
    cross_lingual = cosine_similarity(vectors[0].values, vectors[1].values)
    unrelated = cosine_similarity(vectors[0].values, vectors[2].values)
    assert cross_lingual > unrelated, f"{cross_lingual=:.4f} {unrelated=:.4f}"


@requires_bge
def test_bge_m3_exposes_an_exact_token_counter(bge_provider) -> None:
    """Exact counts let chunk budgets be precise; the heuristic deliberately errs high."""
    counter = bge_provider.as_token_counter()
    assert isinstance(counter, TokenCounter)
    assert counter.count("hello world") > 0
    longer = counter.count("a much longer sentence with considerably more words in it")
    assert longer > counter.count("short")


def test_bge_m3_construction_is_free_without_weights() -> None:
    """Constructing must never load 2.3 GB — the registry lists it on machines lacking the
    model."""
    provider = BgeM3OnnxEmbeddingProvider(allow_download=False)
    assert provider.spec.dimensions == BGE_M3_DIMENSIONS
    assert isinstance(provider.is_available(), bool)
