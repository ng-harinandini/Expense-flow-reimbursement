"""Reranking tests (T004-M7): the heuristic default, the service framework, and the three
provider adapters.

Built around a shared idea rather than a shared parametrized suite (unlike M5's embedding contract
suite or M6's vector-store contract suite): the ``Reranker`` protocol has exactly four clauses, and
every adapter here is tested against all four directly, because the interesting differences between
them are in *how* they fail (a bad HTTP response vs. a missing model file), not in what a correct
call returns.

The two cloud providers (Cohere, Bedrock) are exercised with a stubbed transport/client, following
the same pattern M5's ``providers/embeddings/cloud.py`` tests use — never a live account. The local
cross-encoder is exercised with an injected fake ONNX session and tokenizer, so its request-shaping
and score-pooling logic is real and tested without downloading real weights.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import Chunk, ChunkMetadata, RetrievedChunk
from app.ai.providers.rerank.bedrock import BedrockReranker
from app.ai.providers.rerank.cohere import CohereReranker
from app.ai.providers.rerank.cross_encoder_local import CrossEncoderLocalReranker
from app.ai.reranking.heuristic import HeuristicReranker
from app.ai.reranking.service import RerankService

TENANT = "default"


def candidate(text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            id=uuid.uuid4(), text=text, index=0,
            metadata=ChunkMetadata(tenant_id=TENANT),
        ),
        score=0.5,
    )


# ===========================================================================
# HeuristicReranker
# ===========================================================================


def test_heuristic_is_always_available() -> None:
    assert HeuristicReranker().is_available() is True


def test_heuristic_promotes_the_exact_phrase() -> None:
    """Done Check item 3: reranking reorders as specified."""
    exact = candidate("The meal allowance is thirty euros per day.")
    unrelated = candidate("Parking receipts require a manager's signature.")

    reranked = HeuristicReranker().rerank("meal allowance", [unrelated, exact])

    assert [c.text for c in reranked][0] == exact.text
    assert reranked[0].rerank_score is not None
    assert reranked[0].score == reranked[0].rerank_score


def test_heuristic_rewards_broader_term_coverage() -> None:
    both_terms = candidate("Travel and meal expenses are both reimbursable.")
    one_term = candidate("Travel expenses are reimbursable.")

    reranked = HeuristicReranker().rerank("travel meal expenses", [one_term, both_terms])

    assert reranked[0].text == both_terms.text


def test_heuristic_returns_every_candidate_ignoring_top_n() -> None:
    """Clause 1: never drops. This provider has no payload to save by truncating early — see its
    own docstring — so top_n is accepted and ignored; RerankService applies it uniformly instead."""
    candidates = [candidate("a"), candidate("b"), candidate("c")]
    reranked = HeuristicReranker().rerank("a b c", candidates, top_n=1)
    assert len(reranked) == 3


def test_heuristic_orders_descending_with_a_stable_tie_break() -> None:
    a, b = candidate("apple"), candidate("banana")
    reranked = HeuristicReranker().rerank("nothing matches", [b, a])
    assert reranked[0].score == reranked[1].score == 0.0
    assert [c.id for c in reranked] == sorted([c.id for c in reranked], key=str)


def test_heuristic_on_an_empty_query_scores_everything_zero() -> None:
    reranked = HeuristicReranker().rerank("", [candidate("anything")])
    assert reranked[0].score == 0.0


# ===========================================================================
# RerankService — the framework around one provider
# ===========================================================================


class _StubProvider:
    name = "stub"

    def __init__(self, *, available: bool = True, error: Exception | None = None,
                 wrong_count: bool = False) -> None:
        self._available = available
        self._error = error
        self._wrong_count = wrong_count
        self.calls: list[tuple[str, int]] = []

    def is_available(self) -> bool:
        return self._available

    def rerank(self, query, candidates, *, top_n=None):
        self.calls.append((query, len(candidates)))
        if self._error is not None:
            raise self._error
        if self._wrong_count:
            return list(candidates) + [candidate("invented")]
        reversed_candidates = list(reversed(candidates))
        if top_n is not None:
            reversed_candidates = reversed_candidates[:top_n]
        return reversed_candidates


def test_rerank_service_delegates_to_the_provider_and_truncates() -> None:
    provider = _StubProvider()
    service = RerankService(provider=provider)
    candidates = [candidate("a"), candidate("b"), candidate("c")]

    result = service.rerank("q", candidates, top_n=2)

    assert len(result) == 2
    assert provider.calls == [("q", 3)]


def test_rerank_service_returns_candidates_unchanged_when_unavailable() -> None:
    provider = _StubProvider(available=False)
    service = RerankService(provider=provider)
    candidates = [candidate("a"), candidate("b")]

    result = service.rerank("q", candidates)

    assert result == candidates
    assert provider.calls == []  # rerank() was never even attempted


def test_rerank_service_degrades_to_the_given_order_on_provider_failure() -> None:
    """Clause 4: an unavailable/failing provider must never fail the caller's retrieval."""
    provider = _StubProvider(error=ProviderError("stub", "boom"))
    service = RerankService(provider=provider)
    candidates = [candidate("a"), candidate("b")]

    result = service.rerank("q", candidates)

    assert result == candidates


def test_rerank_service_rejects_a_provider_that_drops_or_invents_results() -> None:
    """Clause 1, enforced at the framework level: a provider returning the wrong count is treated
    exactly like one that raised, because its ordering cannot be trusted either way."""
    provider = _StubProvider(wrong_count=True)
    service = RerankService(provider=provider)
    candidates = [candidate("a"), candidate("b")]

    result = service.rerank("q", candidates)

    assert result == candidates


def test_rerank_service_rejects_a_negative_top_n() -> None:
    """A negative top_n would otherwise mis-slice via Python's own negative-index semantics
    (``reranked[:-1]`` means "everything but the last"), not raise — caught explicitly instead."""
    service = RerankService(provider=_StubProvider())
    with pytest.raises(ValueError, match="top_n must be >= 0"):
        service.rerank("q", [candidate("a")], top_n=-1)


def test_rerank_service_on_no_candidates_is_a_no_op() -> None:
    provider = _StubProvider()
    service = RerankService(provider=provider)
    assert service.rerank("q", []) == []
    assert provider.calls == []


def test_rerank_service_describe_reports_the_provider_name() -> None:
    service = RerankService(provider=HeuristicReranker())
    described = service.describe()
    assert described["provider"] == "heuristic"
    assert described["available"] is True


def test_rerank_service_is_available_never_raises() -> None:
    class Explodes:
        name = "explodes"

        def is_available(self):
            raise RuntimeError("boom")

    service = RerankService(provider=Explodes())
    assert service.is_available() is False


# ===========================================================================
# CohereReranker
# ===========================================================================


class _StubResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload


class _StubHttpxClient:
    """Shapes its reply to the request, like M5's embedding-provider test doubles do."""

    def __init__(self, response: _StubResponse) -> None:
        self._response = response
        self.requests: list[dict[str, Any]] = []

    def post(self, url, *, headers, json):
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self._response

    def close(self) -> None:
        pass


@pytest.fixture
def stub_httpx(monkeypatch):
    """Patches ``httpx.Client`` inside ``app.ai.providers.rerank.cohere`` for one test."""
    created: list[_StubHttpxClient] = []

    def make(response: _StubResponse):
        def factory(*, timeout):  # noqa: ARG001
            client = _StubHttpxClient(response)
            created.append(client)
            return client

        import httpx

        monkeypatch.setattr(httpx, "Client", factory)
        return created

    return make


def test_cohere_reranker_needs_an_api_key() -> None:
    reranker = CohereReranker(api_key=None)
    assert reranker.is_available() is False
    with pytest.raises(ProviderNotConfiguredError, match="AI_COHERE_API_KEY"):
        reranker.rerank("q", [candidate("a")])


def test_cohere_reranker_maps_results_by_index_and_sorts_by_score(stub_httpx) -> None:
    candidates = [candidate("a"), candidate("b"), candidate("c")]
    # Cohere returns results out of input order, sorted by its own relevance_score descending.
    stub_httpx(_StubResponse(200, {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.4},
        ],
    }))
    reranker = CohereReranker(api_key="k")

    reranked = reranker.rerank("q", candidates, top_n=2)

    assert [c.text for c in reranked] == ["c", "a"]
    assert reranked[0].rerank_score == pytest.approx(0.9)


def test_cohere_reranker_sends_top_n_and_no_return_documents(stub_httpx) -> None:
    clients = stub_httpx(_StubResponse(200, {"results": [{"index": 0, "relevance_score": 1.0}]}))
    CohereReranker(api_key="k").rerank("q", [candidate("a")], top_n=5)
    body = clients[0].requests[0]["json"]
    assert body["top_n"] == 5
    assert body["return_documents"] is False


def test_cohere_reranker_raises_on_an_http_error(stub_httpx) -> None:
    stub_httpx(_StubResponse(429, "rate limited"))
    with pytest.raises(ProviderError, match="HTTP 429"):
        CohereReranker(api_key="k").rerank("q", [candidate("a")])


def test_cohere_reranker_raises_on_a_malformed_response(stub_httpx) -> None:
    stub_httpx(_StubResponse(200, {"unexpected": "shape"}))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        CohereReranker(api_key="k").rerank("q", [candidate("a")])


def test_cohere_reranker_refuses_a_batch_above_its_ceiling() -> None:
    too_many = [candidate(str(i)) for i in range(1001)]
    with pytest.raises(ProviderError, match="exceeds Cohere's ceiling"):
        CohereReranker(api_key="k").rerank("q", too_many)


# ===========================================================================
# BedrockReranker
# ===========================================================================


class _StubBedrockClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def rerank(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self._response


def test_bedrock_reranker_maps_results_by_index() -> None:
    candidates = [candidate("a"), candidate("b")]
    client = _StubBedrockClient({"results": [{"index": 1, "relevanceScore": 0.7}]})
    reranker = BedrockReranker(client=client)

    reranked = reranker.rerank("q", candidates, top_n=1)

    assert [c.text for c in reranked] == ["b"]
    assert client.calls[0]["rerankingConfiguration"]["bedrockRerankingConfiguration"][
        "numberOfResults"
    ] == 1


def test_bedrock_reranker_builds_the_documented_request_shape() -> None:
    client = _StubBedrockClient({"results": []})
    BedrockReranker(client=client, model_arn="amazon.rerank-v1:0").rerank(
        "the query", [candidate("passage one")]
    )
    request = client.calls[0]
    assert request["queries"] == [{"type": "TEXT", "textQuery": {"text": "the query"}}]
    assert request["sources"][0]["inlineDocumentSource"]["textDocument"]["text"] == "passage one"
    model_config = request["rerankingConfiguration"]["bedrockRerankingConfiguration"][
        "modelConfiguration"
    ]
    assert model_config["modelArn"] == "amazon.rerank-v1:0"


def test_bedrock_reranker_raises_on_a_malformed_response() -> None:
    client = _StubBedrockClient({"results": [{"index": 99, "relevanceScore": 1.0}]})
    with pytest.raises(ProviderError, match="malformed response"):
        BedrockReranker(client=client).rerank("q", [candidate("a")])


def test_bedrock_reranker_is_available_never_raises() -> None:
    reranker = BedrockReranker()
    # Whatever this environment has installed/configured, is_available must answer, not raise.
    assert reranker.is_available() in (True, False)


# ===========================================================================
# CrossEncoderLocalReranker
# ===========================================================================


class _FakeEncoding:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.attention_mask = [1] * len(ids)
        self.type_ids = [0] * len(ids)


class _FakeTokenizer:
    def encode_batch(self, pairs):
        # One deterministic "token" per character pair, just enough to prove pairs were built.
        return [_FakeEncoding([len(query), len(passage)]) for query, passage in pairs]


class _FakeOnnxSession:
    """Returns a higher single-logit score for a passage containing 'relevant'."""

    def __init__(self) -> None:
        self._inputs = [_Named("input_ids"), _Named("attention_mask")]

    def get_inputs(self):
        return self._inputs

    def run(self, output_names, feed):

        # The stub tokenizer encodes passage length as the second id; use it as a stand-in scoring
        # signal so different passages produce different, checkable logits.
        passage_lengths = feed["input_ids"][:, 1]
        logits = (passage_lengths % 7).astype("float32") - 3.0
        return [logits.reshape(-1, 1)]


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def test_cross_encoder_local_needs_the_runtime_or_reports_unavailable() -> None:
    reranker = CrossEncoderLocalReranker()
    # Whatever this environment has installed, is_available must answer without raising.
    assert reranker.is_available() in (True, False)


def test_cross_encoder_local_scores_pairs_and_bounds_to_zero_one() -> None:
    reranker = CrossEncoderLocalReranker(
        session=_FakeOnnxSession(), tokenizer=_FakeTokenizer(),
    )
    candidates = [candidate("short"), candidate("a somewhat longer passage of text")]

    reranked = reranker.rerank("query text", candidates)

    assert len(reranked) == 2
    assert all(0.0 <= c.score <= 1.0 for c in reranked)
    assert reranked == sorted(reranked, key=lambda c: -c.score)


def test_cross_encoder_local_feeds_only_declared_inputs() -> None:
    """Mirrors bge_m3_onnx.py's own guard: an undeclared input (e.g. token_type_ids on a graph that
    has none) must never be fed, or onnxruntime raises."""
    class _NoTypeIdsSession(_FakeOnnxSession):
        def run(self, output_names, feed):
            assert "token_type_ids" not in feed
            return super().run(output_names, feed)

    reranker = CrossEncoderLocalReranker(
        session=_NoTypeIdsSession(), tokenizer=_FakeTokenizer(),
    )
    reranker.rerank("q", [candidate("a")])
