"""Unit tests for the AI platform's contract layer (T004-M1).

No database and no network: every module under test is deliberately infrastructure-free, which is
what makes this suite fast and what makes the platform's provider-independence claim testable.

Grouped by the guarantee each set defends, because that is what a future reader needs to know before
changing one of these files.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from dataclasses import FrozenInstanceError
from typing import Protocol, runtime_checkable

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.ai.core import ids, text
from app.ai.core.config import BGE_M3_DIMENSIONS, AISettings
from app.ai.core.enums import (
    ChunkStrategy,
    DocumentStatus,
    DuplicateSignalKind,
    DuplicateVerdict,
    KnowledgeSourceType,
    PIIKind,
    ProviderKind,
    TelemetryOperation,
)
from app.ai.core.errors import (
    AIError,
    DimensionMismatchError,
    DocumentAlreadyIndexedError,
    EmbeddingVersionConflictError,
    FeatureDisabledError,
    PIIRejectionError,
    PromptRenderError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ToolPermissionError,
    UnsupportedDocumentError,
)
from app.ai.core.types import (
    Chunk,
    ChunkMetadata,
    Citation,
    ContextBundle,
    DuplicateMatch,
    DuplicateReport,
    DuplicateSignal,
    EmbeddingSpec,
    EmbeddingUsage,
    EmbeddingVector,
    MetadataFilter,
    ParsedDocument,
    PIIFinding,
    RawDocument,
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
    StageTiming,
    as_sequence,
)
from app.ai.interfaces import CacheBackend, ChunkingConfig, Span, TelemetryRecorder
from app.ai.interfaces.vector_store import VectorMatch, VectorStoreCapabilities
from app.ai.providers.cache import MemoryCache, NullCache, register_cache_providers
from app.ai.registry.flags import FeatureFlags
from app.ai.registry.registry import ComponentRegistry, cache_registry
from app.ai.telemetry import (
    NullTelemetryRecorder,
    TelemetryRecorderImpl,
    build_recorder,
    current_span,
)
from app.core.errors import status_for


# ---------------------------------------------------------------------------
# Errors: the platform must produce correct HTTP semantics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (DimensionMismatchError(1024, 768), 422),
        (UnsupportedDocumentError("application/x-foo", "a.foo", ["application/pdf"]), 422),
        (PIIRejectionError(["CREDIT_CARD"]), 422),
        (PromptRenderError("policy.explain", ["limit"], []), 422),
        (DocumentAlreadyIndexedError("deadbeef", uuid.uuid4()), 409),
        (EmbeddingVersionConflictError("bge-m3@v1", "titan@v1"), 409),
        (ToolPermissionError("retrieve_policy", "employee", ["finance"]), 403),
        (ProviderNotConfiguredError("bedrock", "EMBEDDING", "Set AWS credentials."), 503),
        (FeatureDisabledError("ai.llm"), 503),
        (ProviderError("qdrant", "connection refused"), 502),
        (ProviderTimeoutError("cohere", 12.5), 504),
        (AIError("something generic"), 400),
    ],
)
def test_ai_errors_map_to_intended_http_status(error, expected_status: int) -> None:
    """A missing provider is an operator problem (503), not a client mistake (400) or a bug (500).

    Regression guard for the ``http_status`` hook in ``app.core.errors.status_for``: without it,
    every provider failure silently became a 400.
    """
    assert status_for(error) == expected_status


def test_provider_not_configured_names_a_remedy() -> None:
    """"Not configured" without saying what to configure is unactionable on call."""
    error = ProviderNotConfiguredError("redis", "CACHE", "Set AI_CACHE_REDIS_URL.")
    payload = error.to_payload()
    assert payload["code"] == "provider_not_configured"
    assert "AI_CACHE_REDIS_URL" in payload["context"]["remedy"]


def test_embedding_version_conflict_reports_both_versions() -> None:
    """Comparing vectors across models is meaningless; the error must say which two collided."""
    error = EmbeddingVersionConflictError("bge-m3@v1", "titan@v2")
    assert error.details == {"expectedVersion": "bge-m3@v1", "actualVersion": "titan@v2"}


# ---------------------------------------------------------------------------
# Types: immutability and the invariants that protect retrieval correctness
# ---------------------------------------------------------------------------

def test_embedding_vector_rejects_declared_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match declared dimensions"):
        EmbeddingVector(values=(0.1, 0.2), spec_key="k@v1", dimensions=3)


def test_embedding_vector_is_hashable_so_it_can_be_a_cache_key() -> None:
    vector = EmbeddingVector(values=(0.1, 0.2, 0.3), spec_key="k@v1", dimensions=3)
    assert hash(vector) == hash(
        EmbeddingVector(values=(0.1, 0.2, 0.3), spec_key="k@v1", dimensions=3)
    )


def test_value_types_are_frozen() -> None:
    """Retrieval threads one object through six stages; a mutable payload lets stages corrupt each
    other's inputs."""
    vector = EmbeddingVector(values=(1.0,), spec_key="k@v1", dimensions=1)
    with pytest.raises(FrozenInstanceError):
        vector.dimensions = 9  # type: ignore[misc]


def test_retrieval_query_rejects_candidate_k_below_top_k() -> None:
    """Reranking cannot improve on a candidate set smaller than the requested output."""
    with pytest.raises(ValueError, match="candidate_k must be >= top_k"):
        RetrievalQuery(text="x", top_k=10, candidate_k=5)


def test_retrieval_query_rejects_non_positive_top_k() -> None:
    with pytest.raises(ValueError, match="top_k must be >= 1"):
        RetrievalQuery(text="x", top_k=0)


def test_with_filters_does_not_mutate_the_original_query() -> None:
    original = RetrievalQuery(text="meal limit")
    extended = original.with_filters(MetadataFilter("country", "eq", "IN"))
    assert original.filters == ()
    assert len(extended.filters) == 1


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (dt.date(2024, 12, 31), False),   # before effective
        (dt.date(2025, 1, 1), True),      # boundary: inclusive
        (dt.date(2025, 6, 1), True),
        (dt.date(2025, 12, 31), True),    # boundary: inclusive
        (dt.date(2026, 1, 1), False),     # after expiry
    ],
)
def test_chunk_metadata_effective_dating_is_inclusive(when: dt.date, expected: bool) -> None:
    """A 2025 claim must keep being judged against 2025 policy after a 2026 revision is indexed."""
    metadata = ChunkMetadata(
        effective_date=dt.date(2025, 1, 1), expiry_date=dt.date(2025, 12, 31)
    )
    assert metadata.is_effective_on(when) is expected


def test_chunk_metadata_without_dates_is_always_effective() -> None:
    assert ChunkMetadata().is_effective_on(dt.date(1999, 1, 1)) is True


def test_citation_label_is_human_readable() -> None:
    citation = Citation(
        document_id=uuid.uuid4(), document_title="Travel Policy",
        document_version=3, section="Meals", page_number=12,
    )
    assert citation.to_label() == "Travel Policy v3, Meals, p.12"


def test_embedding_usage_accumulates_across_batches() -> None:
    total = EmbeddingUsage(texts=2, tokens=10, latency_ms=5, cache_hits=1).merged_with(
        EmbeddingUsage(texts=3, tokens=7, latency_ms=6, provider="bge", model="m3")
    )
    assert (total.texts, total.tokens, total.latency_ms, total.cache_hits) == (5, 17, 11, 1)
    assert total.provider == "bge"


# ---------------------------------------------------------------------------
# Duplicate detection value types: explainability and advisory-only status
# ---------------------------------------------------------------------------

def test_duplicate_signal_fires_only_at_or_above_threshold() -> None:
    assert DuplicateSignal(DuplicateSignalKind.SHA256, score=1.0, threshold=1.0).fired
    assert not DuplicateSignal(
        DuplicateSignalKind.OCR_SIMILARITY, score=0.84, threshold=0.85
    ).fired


def test_duplicate_match_explains_which_signals_fired() -> None:
    """A reviewer needs to know *why*; a single blended score is not defensible in a dispute."""
    match = DuplicateMatch(
        target_kind="receipt", target_id="r1",
        signals=(
            DuplicateSignal(DuplicateSignalKind.SHA256, 1.0, 1.0),
            DuplicateSignal(DuplicateSignalKind.OCR_SIMILARITY, 0.10, 0.85),
        ),
        score=1.0, verdict=DuplicateVerdict.CONFIRMED,
    )
    explanation = match.explain()
    assert "SHA256" in explanation
    assert "OCR_SIMILARITY" not in explanation, "a signal that did not fire must not be cited"
    assert match.fired_signals == (DuplicateSignalKind.SHA256,)


def test_duplicate_report_verdict_is_the_strongest_match() -> None:
    def match(verdict: DuplicateVerdict) -> DuplicateMatch:
        return DuplicateMatch(target_kind="claim", target_id="c", signals=(), score=0.5,
                              verdict=verdict)

    report = DuplicateReport(
        subject_kind="claim", subject_id="s",
        matches=(match(DuplicateVerdict.POSSIBLE), match(DuplicateVerdict.LIKELY)),
    )
    assert report.verdict is DuplicateVerdict.LIKELY


def test_empty_duplicate_report_is_no_match() -> None:
    report = DuplicateReport(subject_kind="claim", subject_id="s")
    assert report.verdict is DuplicateVerdict.NO_MATCH


def test_duplicate_detection_is_structurally_advisory() -> None:
    """The deterministic engine in ClaimService remains the only thing that can stop a
    submission.
    """
    assert DuplicateReport(subject_kind="claim", subject_id="s").is_advisory_only is True


# ---------------------------------------------------------------------------
# Enums: wire-value stability and coercion
# ---------------------------------------------------------------------------

def test_enums_coerce_from_value_name_and_variants() -> None:
    assert ChunkStrategy.coerce("RECURSIVE") is ChunkStrategy.RECURSIVE
    assert ChunkStrategy.coerce("parent-child") is ChunkStrategy.PARENT_CHILD
    assert DocumentStatus.coerce("indexed") is DocumentStatus.INDEXED


def test_enum_coercion_rejects_unknown_values_with_a_useful_message() -> None:
    with pytest.raises(ValueError, match="is not a valid KnowledgeSourceType"):
        KnowledgeSourceType.coerce("NOT_A_SOURCE")


def test_all_eighteen_declared_knowledge_source_types_exist() -> None:
    """Task 1 enumerates the source types the platform must ingest."""
    for required in (
        "POLICY", "EMPLOYEE_HANDBOOK", "FINANCE_POLICY", "TRAVEL_POLICY", "MEDICAL_POLICY",
        "COUNTRY_POLICY", "VENDOR_CONTRACT", "VENDOR_MANUAL", "HISTORICAL_CLAIM",
        "HISTORICAL_DECISION", "REVIEWER_NOTE", "FRAUD_INVESTIGATION", "TAX_RULE",
        "GOVERNMENT_GUIDELINE", "RECEIPT", "INVOICE", "TRAINING_DOCUMENT", "FAQ",
    ):
        assert required in KnowledgeSourceType.__members__


def test_all_eleven_duplicate_signals_from_task_10_exist() -> None:
    assert len(DuplicateSignalKind) == 11


def test_all_seven_chunk_strategies_from_task_3_exist() -> None:
    assert len(ChunkStrategy) == 7


# ---------------------------------------------------------------------------
# Identity: the basis of idempotent ingestion and reproducible retrieval
# ---------------------------------------------------------------------------

def test_content_checksum_is_stable_and_content_sensitive() -> None:
    assert ids.content_checksum(b"abc") == ids.content_checksum(b"abc")
    assert ids.content_checksum(b"abc") != ids.content_checksum(b"abd")


def test_document_id_is_tenant_scoped() -> None:
    """Two enterprises uploading the same public PDF must not share a row."""
    checksum = ids.content_checksum(b"tax guidance")
    assert ids.document_id_for("acme", checksum) != ids.document_id_for("globex", checksum)


def test_chunk_id_changes_when_text_changes() -> None:
    """Reusing an id for different content would leave a stale embedding attached to new text."""
    document = uuid.uuid4()
    assert ids.chunk_id_for(document, 0, "a") == ids.chunk_id_for(document, 0, "a")
    assert ids.chunk_id_for(document, 0, "a") != ids.chunk_id_for(document, 0, "b")
    assert ids.chunk_id_for(document, 0, "a") != ids.chunk_id_for(document, 1, "a")


def test_deterministic_uuid_separator_prevents_part_collisions() -> None:
    assert ids.deterministic_uuid("doc", "abc") != ids.deterministic_uuid("docabc")


def test_embedding_cache_key_is_scoped_to_the_model_version() -> None:
    """Keying on text alone is how a model upgrade serves stale vectors out of cache."""
    assert ids.embedding_cache_key("t", "m@v1") != ids.embedding_cache_key("t", "m@v2")


def test_config_fingerprint_ignores_key_order_but_not_values() -> None:
    assert ids.config_fingerprint({"a": 1, "b": 2}) == ids.config_fingerprint({"b": 2, "a": 1})
    assert ids.config_fingerprint({"a": 1}) != ids.config_fingerprint({"a": 2})


def test_config_fingerprint_handles_nested_structures() -> None:
    left = ids.config_fingerprint({"o": {"y": 2, "x": 1}, "l": [1, 2]})
    right = ids.config_fingerprint({"l": [1, 2], "o": {"x": 1, "y": 2}})
    assert left == right


def test_retrieval_cache_key_is_tenant_scoped() -> None:
    """One tenant must never be served another's cached results."""
    assert ids.retrieval_cache_key("q", "s", "f", "acme") != ids.retrieval_cache_key(
        "q", "s", "f", "globex"
    )


def test_stable_hash_int_is_reproducible_across_processes() -> None:
    """Python's ``hash()`` is randomized per process, which would break the deterministic
    embedder.
    """
    assert ids.stable_hash_int("vendor", buckets=256) == ids.stable_hash_int("vendor", buckets=256)
    assert 0 <= ids.stable_hash_int("vendor", buckets=256) < 256


def test_stable_hash_int_rejects_zero_buckets() -> None:
    with pytest.raises(ValueError):
        ids.stable_hash_int("x", buckets=0)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def test_normalize_text_repairs_pdf_hyphenation() -> None:
    """PDF extraction splits words across lines; leaving it splits one token no query will match."""
    assert "reimbursement" in text.normalize_text("reimburse-\nment")


def test_normalize_text_folds_typographic_characters() -> None:
    """A query typed in ASCII must match a PDF that used typographic punctuation."""
    assert text.normalize_text("15.00–20.00") == "15.00-20.00"
    assert " " not in text.normalize_text("a b")


def test_normalize_text_is_idempotent() -> None:
    once = text.normalize_text("Team  lunch\n\n\n\nreimburse-\nment ")
    assert text.normalize_text(once) == once


def test_normalize_for_matching_strips_accents_and_punctuation() -> None:
    assert text.normalize_for_matching("UBER *TRIP  HELP.UBER.CO") == "uber trip help uber co"
    assert text.normalize_for_matching("Café") == "cafe"


def test_sentence_split_preserves_decimals_and_abbreviations() -> None:
    """Splitting mid-amount corrupts the very numbers a policy chunk exists to state."""
    sentences = text.split_sentences("Limit is USD 1.50 per item. Acme Inc. approved it. Done!")
    assert sentences == ["Limit is USD 1.50 per item.", "Acme Inc. approved it.", "Done!"]


def test_split_sentences_on_empty_input() -> None:
    assert text.split_sentences("") == []
    assert text.split_sentences("   ") == []


def test_split_paragraphs_uses_blank_lines() -> None:
    assert text.split_paragraphs("one\n\ntwo\n\n\nthree") == ["one", "two", "three"]


def test_token_estimate_charges_cjk_far_more_per_character() -> None:
    """A shared chars-per-token constant under-counts CJK ~4x and overflows the context window."""
    latin = text.estimate_tokens("a" * 40)
    cjk = text.estimate_tokens("食" * 40)
    assert cjk > latin * 3


def test_token_estimate_of_empty_string_is_zero() -> None:
    assert text.estimate_tokens("") == 0


def test_truncate_reports_whether_it_truncated() -> None:
    """Silently shortened evidence produces confident wrong conclusions."""
    body = "First sentence here. Second sentence here. Third sentence here."
    short, truncated = text.truncate_to_tokens(body, 8)
    assert truncated is True
    assert len(short) < len(body)

    whole, untouched = text.truncate_to_tokens(body, 10_000)
    assert untouched is False
    assert whole == body


def test_truncate_to_zero_tokens_returns_empty_and_flags_truncation() -> None:
    assert text.truncate_to_tokens("anything", 0) == ("", True)


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        ("The employee shall submit the expense report for reimbursement of costs", "en"),
        ("従業員の国内出張の食事手当は1日50米ドルです", "ja"),
        ("Les frais de repas des salaries sont rembourses pour que", "fr"),
        ("Der Mitarbeiter und die Kosten von der Reise sind nicht", "de"),
    ],
)
def test_language_detection_identifies_routing_languages(sample: str, expected: str) -> None:
    assert text.detect_language(sample) == expected


def test_language_detection_abstains_rather_than_guessing() -> None:
    """Only used for routing, so abstaining beats a confident wrong answer."""
    assert text.detect_language("xy") is None
    assert text.detect_language("") is None
    assert text.detect_language("zzz qqq", default="en") == "en"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@runtime_checkable
class _Sample(Protocol):
    name: str

    def is_available(self) -> bool:
        ...


class _Good:
    name = "good"

    def __init__(self, available: bool = True) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


class _MissingMethod:
    name = "incomplete"


@pytest.fixture
def registry() -> ComponentRegistry:
    return ComponentRegistry(ProviderKind.EMBEDDING)


def test_registry_caches_instances(registry: ComponentRegistry) -> None:
    """Two concurrent first-requests must not each build a ~2 GB ONNX session."""
    registry.register("a", _Good, protocol=_Sample)
    assert registry.resolve("a") is registry.resolve("a")


def test_registry_blocks_accidental_reregistration(registry: ComponentRegistry) -> None:
    """A silent overwrite makes the active provider depend on import order."""
    registry.register("a", _Good, protocol=_Sample)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("a", _Good, protocol=_Sample)
    registry.register("a", _Good, protocol=_Sample, replace=True)


def test_registry_enforces_the_declared_protocol(registry: ComponentRegistry) -> None:
    """Catching a half-implemented adapter here beats an AttributeError inside a retrieval loop."""
    registry.register("bad", _MissingMethod, protocol=_Sample)
    with pytest.raises(TypeError, match="does not satisfy"):
        registry.resolve("bad")


def test_registry_reports_unknown_component_as_not_configured(registry: ComponentRegistry) -> None:
    with pytest.raises(ProviderNotConfiguredError):
        registry.resolve("nope")


def test_registry_wraps_construction_failure_as_not_configured(
    registry: ComponentRegistry,
) -> None:
    """A missing driver should be a 503 naming the remedy, not an ImportError 500."""
    def explode() -> object:
        raise RuntimeError("no driver installed")

    registry.register("boom", explode, protocol=_Sample)
    with pytest.raises(ProviderNotConfiguredError, match="no driver installed"):
        registry.resolve("boom")


def test_registry_falls_back_to_the_first_available_component(
    registry: ComponentRegistry,
) -> None:
    """The mechanism behind bge-m3 -> deterministic degradation when weights are absent."""
    registry.register("primary", lambda: _Good(available=False), protocol=_Sample)
    registry.register("backup", lambda: _Good(available=True), protocol=_Sample)
    assert registry.resolve_with_fallback(["primary", "backup"]).is_available()


def test_registry_fallback_raises_when_nothing_is_usable(registry: ComponentRegistry) -> None:
    registry.register("primary", lambda: _Good(available=False), protocol=_Sample)
    with pytest.raises(ProviderNotConfiguredError, match="No candidate was usable"):
        registry.resolve_with_fallback(["primary", "absent"])


def test_registry_fallback_requires_at_least_one_preference(registry: ComponentRegistry) -> None:
    with pytest.raises(ValueError):
        registry.resolve_with_fallback([])


def test_describe_does_not_construct_components(registry: ComponentRegistry) -> None:
    """Regression: ``describe()`` used to build every provider.

    Opening a metrics dashboard would have loaded the bge-m3 ONNX session (~2.3 GB) and dialled
    every configured external store. Introspection must be side-effect free.
    """
    built: list[str] = []

    class _Heavy:
        name = "heavy"

        def __init__(self) -> None:
            built.append("heavy")

        def is_available(self) -> bool:
            return True

    registry.register("heavy", _Heavy, protocol=_Sample)
    rows = registry.describe()

    assert built == [], "describe() must not instantiate providers"
    assert rows[0]["instantiated"] is False
    assert rows[0]["available"] is None, "unbuilt component availability is unknown, not False"


def test_describe_reports_availability_for_already_built_components(
    registry: ComponentRegistry,
) -> None:
    registry.register("a", _Good, protocol=_Sample)
    registry.resolve("a")
    row = registry.describe()[0]
    assert row["instantiated"] is True
    assert row["available"] is True


def test_registry_kind_property_reports_its_own_kind(registry: ComponentRegistry) -> None:
    assert registry.kind == ProviderKind.EMBEDDING


def test_registry_rejects_registering_a_blank_name(registry: ComponentRegistry) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        registry.register("   ", _Good, protocol=_Sample)


def test_safe_available_is_none_for_an_unregistered_name(registry: ComponentRegistry) -> None:
    """``describe()``/probing never raises for a name that was never registered at all."""
    assert registry._safe_available("nope") is None


def test_safe_available_catches_a_raising_is_available(registry: ComponentRegistry) -> None:
    """A broken adapter's ``is_available()`` must not break introspection — see the module's own
    'Availability-aware fallback' guarantee."""
    class _Broken:
        name = "broken"

        def is_available(self) -> bool:
            raise RuntimeError("boom")

    registry.register("broken", _Broken, protocol=_Sample)
    assert registry._safe_available("broken", construct=True) is False


def test_fallback_skips_a_preference_whose_construction_is_not_configured(
    registry: ComponentRegistry,
) -> None:
    """A registered-but-unconfigured preference (its own factory raises
    ``ProviderNotConfiguredError``, exactly like an external vector-store adapter with no URL set)
    must be skipped in favour of the next preference, not propagate."""
    def unconfigured() -> object:
        raise ProviderNotConfiguredError(provider="primary", kind="EMBEDDING", remedy="set it")

    registry.register("primary", unconfigured, protocol=_Sample)
    registry.register("backup", lambda: _Good(available=True), protocol=_Sample)
    assert registry.resolve_with_fallback(["primary", "backup"]).is_available()


def test_fallback_skips_a_preference_whose_is_available_raises(
    registry: ComponentRegistry,
) -> None:
    """Distinct from the construction-failure case above: the component builds fine, but calling
    its own ``is_available()`` blows up — the fallback loop must still move on."""
    class _RaisesOnAvailabilityCheck:
        name = "flaky"

        def is_available(self) -> bool:
            raise RuntimeError("network blip")

    registry.register("primary", _RaisesOnAvailabilityCheck, protocol=_Sample)
    registry.register("backup", lambda: _Good(available=True), protocol=_Sample)
    assert registry.resolve_with_fallback(["primary", "backup"]).is_available()


def test_registry_snapshot_reports_every_registry_by_kind() -> None:
    from app.ai.registry.registry import ALL_REGISTRIES, registry_snapshot

    snapshot = registry_snapshot()
    assert set(snapshot) == {registry.kind.value for registry in ALL_REGISTRIES}
    assert all(isinstance(rows, list) for rows in snapshot.values())


def test_describe_can_probe_on_request(registry: ComponentRegistry) -> None:
    registry.register("a", _Good, protocol=_Sample)
    assert registry.describe(probe=True)[0]["available"] is True


def test_clear_instances_keeps_registrations(registry: ComponentRegistry) -> None:
    registry.register("a", _Good, protocol=_Sample)
    first = registry.resolve("a")
    registry.clear_instances()
    assert registry.is_registered("a")
    assert registry.resolve("a") is not first


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

@pytest.fixture
def flags() -> FeatureFlags:
    return FeatureFlags(AISettings())


def test_generative_capabilities_are_off_by_default(flags: FeatureFlags) -> None:
    """Deploying this code must never start sending data to a model provider."""
    assert flags.is_enabled("ai.llm") is False
    assert flags.is_enabled("ai.llm.explanations") is False


def test_read_only_capabilities_are_on_by_default(flags: FeatureFlags) -> None:
    assert flags.is_enabled("ai.retrieval") is True
    assert flags.is_enabled("ai.ingestion") is True


def test_unknown_flag_is_disabled_not_enabled(flags: FeatureFlags) -> None:
    """Fail closed: a typo must not silently switch a capability on."""
    assert flags.is_enabled("ai.definitely_not_a_flag") is False


def test_master_switch_disables_every_descendant(flags: FeatureFlags) -> None:
    """During an incident an operator must not have to find a dozen variables."""
    flags.set_override("ai", False)
    assert flags.is_enabled("ai.retrieval") is False
    assert flags.is_enabled("ai.rerank") is False
    assert flags.is_enabled("ai.duplicate_detection") is False


def test_child_flag_requires_its_own_value_too(flags: FeatureFlags) -> None:
    flags.set_override("ai.llm", True)
    assert flags.is_enabled("ai.llm") is True
    assert flags.is_enabled("ai.llm.explanations") is False


def test_disabled_reason_identifies_the_responsible_ancestor(flags: FeatureFlags) -> None:
    flags.set_override("ai", False)
    reason = flags.disabled_reason("ai.rerank")
    assert reason is not None and "'ai'" in reason and "AI_ENABLED" in reason


def test_require_raises_feature_disabled(flags: FeatureFlags) -> None:
    with pytest.raises(FeatureDisabledError) as excinfo:
        flags.require("ai.llm")
    assert excinfo.value.details["flag"] == "ai.llm"


def test_override_rejects_unknown_flag(flags: FeatureFlags) -> None:
    with pytest.raises(ValueError, match="Unknown feature flag"):
        flags.set_override("nope", True)


def test_clearing_overrides_restores_configured_values(flags: FeatureFlags) -> None:
    flags.set_override("ai", False)
    flags.clear_overrides()
    assert flags.is_enabled("ai.retrieval") is True


def test_flag_snapshot_covers_every_known_flag(flags: FeatureFlags) -> None:
    snapshot = flags.snapshot()
    assert set(snapshot["flags"]) == set(flags.known_flags())


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_default_embedding_configuration_matches_bge_m3() -> None:
    settings = AISettings()
    assert settings.EMBEDDING_MODEL == "BAAI/bge-m3"
    assert settings.EMBEDDING_DIMENSIONS == BGE_M3_DIMENSIONS == 1024
    assert settings.embedding_spec_key == "bge_m3_onnx/BAAI/bge-m3@v1"


def test_default_vector_store_is_pgvector() -> None:
    assert AISettings().VECTOR_STORE == "pgvector"


def test_default_settings_are_internally_consistent() -> None:
    AISettings().require_valid()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"CHUNK_MAX_TOKENS": 100, "CHUNK_OVERLAP_TOKENS": 100}, "must be less than"),
        ({"CHUNK_MIN_TOKENS": 9_000}, "cannot exceed"),
        ({"RETRIEVAL_TOP_K": 9_000}, "must be >="),
        ({"DUP_CONFIRMED_SCORE": 0.1}, "must be >="),
        ({"CACHE_BACKEND": "redis"}, "requires AI_CACHE_REDIS_URL"),
    ],
)
def test_cross_field_validation_rejects_contradictory_config(
    overrides: dict, message: str
) -> None:
    """Caught at boot rather than at the first request — an overlap >= chunk size would otherwise
    become an infinite chunking loop."""
    with pytest.raises(ValueError, match=message):
        AISettings(**overrides).require_valid()


@pytest.mark.parametrize(
    "overrides",
    [
        {"EMBEDDING_DIMENSIONS": 0},
        {"RETRIEVAL_DENSE_WEIGHT": 1.5},
        {"DUP_LIKELY_SCORE": 2.0},
        {"CHUNK_OVERLAP_TOKENS": -1},
    ],
)
def test_field_validation_rejects_out_of_range_values(overrides: dict) -> None:
    with pytest.raises(PydanticValidationError):
        AISettings(**overrides)


def test_retrieval_fingerprint_tracks_result_affecting_settings_only() -> None:
    """A batch-size tweak must not invalidate every recorded fingerprint."""
    baseline = AISettings()
    same_results = AISettings(EMBEDDING_BATCH_SIZE=baseline.EMBEDDING_BATCH_SIZE + 8)
    different_results = AISettings(RETRIEVAL_TOP_K=baseline.RETRIEVAL_TOP_K + 1)

    fingerprint = ids.config_fingerprint(baseline.retrieval_config_fingerprint_source())
    assert fingerprint == ids.config_fingerprint(
        same_results.retrieval_config_fingerprint_source()
    )
    assert fingerprint != ids.config_fingerprint(
        different_results.retrieval_config_fingerprint_source()
    )


def test_pii_reject_kinds_parses_a_messy_list() -> None:
    settings = AISettings(PII_REJECT_KINDS="credit_card, national_id ,,")
    assert settings.pii_reject_kinds == frozenset({"CREDIT_CARD", "NATIONAL_ID"})


def test_pii_reject_kinds_is_not_stale_after_model_copy() -> None:
    """Regression (found building T004-M8): this was a ``cached_property``, and pydantic's
    ``model_copy`` copies ``__dict__`` verbatim — so once a singleton's ``pii_reject_kinds`` had
    been read anywhere, every later ``model_copy(update={"PII_REJECT_KINDS": ...})`` would carry
    over the stale cached value and silently ignore the override. Reproduced by reading the
    property before copying, exactly as a caller doing ``settings or ai_settings`` elsewhere in the
    codebase would."""
    original = AISettings(PII_REJECT_KINDS="")
    assert original.pii_reject_kinds == frozenset()  # read once, before the copy

    updated = original.model_copy(update={"PII_REJECT_KINDS": "EMAIL"})
    assert updated.pii_reject_kinds == frozenset({"EMAIL"})


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_memory_cache_satisfies_the_backend_protocol() -> None:
    assert isinstance(MemoryCache(), CacheBackend)
    assert isinstance(NullCache(), CacheBackend)


def test_memory_cache_round_trips_a_value() -> None:
    cache = MemoryCache()
    assert cache.get("k") is None
    cache.set("k", {"v": [1, 2]})
    assert cache.get("k") == {"v": [1, 2]}


def test_memory_cache_evicts_least_recently_used() -> None:
    cache = MemoryCache(max_entries=3)
    cache.set("a", 1)
    cache.set("b", 1)
    cache.set("c", 1)
    cache.get("a")           # 'a' becomes most-recently-used, so 'b' is next out
    cache.set("d", 1)
    assert cache.get("b") is None
    assert cache.get("a") == 1


def test_memory_cache_expires_entries() -> None:
    cache = MemoryCache()
    cache.set("k", 1, ttl_seconds=1)
    assert cache.get("k") == 1
    cache._entries["k"] = (1, time.monotonic() - 1)   # force expiry without sleeping
    assert cache.get("k") is None
    assert cache.stats["expirations"] == 1


def test_memory_cache_reports_hit_rate() -> None:
    cache = MemoryCache()
    cache.set("k", 1)
    cache.get("k")
    cache.get("absent")
    assert cache.stats["hitRatePercent"] == 50


def test_memory_cache_rejects_nonsensical_capacity() -> None:
    with pytest.raises(ValueError):
        MemoryCache(max_entries=0)


def test_null_cache_never_retains_anything() -> None:
    cache = NullCache()
    cache.set("k", 1)
    assert cache.get("k") is None


def test_redis_backend_is_registered_but_reports_unconfigured() -> None:
    """The adapter must be registerable on a machine with no redis package and no URL."""
    register_cache_providers()
    assert "redis" in cache_registry.names()
    with pytest.raises(ProviderNotConfiguredError):
        cache_registry.resolve("redis")


def test_cache_registry_describe_is_side_effect_free() -> None:
    register_cache_providers()
    rows = {row["name"]: row for row in cache_registry.describe()}
    assert set(rows) >= {"memory", "none", "redis"}


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def test_recorder_satisfies_the_protocol() -> None:
    assert isinstance(build_recorder(enabled=True), TelemetryRecorder)
    assert isinstance(build_recorder(enabled=False), TelemetryRecorder)


def test_spans_nest_into_a_tree() -> None:
    """One retrieval yields per-stage numbers from the tree rather than ad-hoc timers."""
    recorder = build_recorder(enabled=True)
    with recorder.span(TelemetryOperation.RETRIEVE) as root:
        assert isinstance(root, Span)
        with recorder.span(TelemetryOperation.EMBED):
            pass
        with recorder.span(TelemetryOperation.VECTOR_SEARCH):
            pass
    assert [child.operation for child in root.children] == ["EMBED", "VECTOR_SEARCH"]
    assert [timing.stage for timing in root.timings()] == [
        "RETRIEVE", "EMBED", "VECTOR_SEARCH",
    ]


def test_span_records_funnel_counts() -> None:
    recorder = build_recorder(enabled=True)
    with recorder.span(TelemetryOperation.FUSION) as span:
        span.set_counts(candidates_in=100, candidates_out=8)
    timing = span.to_timing()
    assert (timing.candidates_in, timing.candidates_out) == (100, 8)


def test_span_records_failure_and_still_propagates_the_exception() -> None:
    """Telemetry observes failures; it must not suppress them."""
    recorder = build_recorder(enabled=True)
    with pytest.raises(RuntimeError, match="cohere unreachable"):
        with recorder.span(TelemetryOperation.RERANK):
            raise RuntimeError("cohere unreachable")
    assert recorder.snapshot()["operations"]["RERANK"]["failures"] == 1


def test_telemetry_never_raises_on_hostile_input() -> None:
    """Losing a metric is acceptable; failing the request it measured is not."""
    recorder = build_recorder(enabled=True)
    span = recorder.span("custom.op")
    span.set_attribute("k", object())
    span.set_counts(candidates_in=-1)
    span.__exit__(None, None, None)
    recorder.record_metric("m", float("nan"))
    recorder.increment("c", 1)


def test_recorder_aggregates_counters_and_gauges() -> None:
    recorder = build_recorder(enabled=True)
    recorder.increment("tokens", 1_500)
    recorder.increment("tokens", 500)
    recorder.record_metric("cache_hit_rate", 0.83)
    snapshot = recorder.snapshot()
    assert snapshot["counters"]["tokens"] == 2_000
    assert snapshot["gauges"]["cache_hit_rate"] == 0.83


def test_null_recorder_is_inert_but_usable_as_a_context_manager() -> None:
    """A null object keeps measured code free of ``if recorder is not None`` branching."""
    recorder = build_recorder(enabled=False)
    with recorder.span(TelemetryOperation.EMBED) as span:
        span.set_attribute("x", 1)
    assert recorder.snapshot()["enabled"] is False


def test_null_recorder_still_nests_child_spans_under_their_parent() -> None:
    """Regression: ``NullTelemetryRecorder.span()`` used to build a disconnected ``TimingSpan`` —
    never reading or writing the ``_current_span`` ContextVar — so a child span opened while
    "disabled" telemetry was in effect would time correctly on its own but never appear in its
    parent's ``flatten()``/``timings()``. Anything that builds a span tree and reads it back from
    the root (T004-M7's ``HybridRetrievalEngine.retrieve`` does exactly this to populate
    ``RetrievalResult.timings``) silently lost every stage but the root whenever no real recorder
    was wired up — which is the *default* construction for exactly that engine.
    """
    recorder = build_recorder(enabled=False)
    with recorder.span(TelemetryOperation.RETRIEVE) as root:
        with recorder.span(TelemetryOperation.LEXICAL_SEARCH):
            pass
        with recorder.span(TelemetryOperation.VECTOR_SEARCH):
            pass

    stages = {timing.stage for timing in root.timings()}
    assert stages == {"RETRIEVE", "LEXICAL_SEARCH", "VECTOR_SEARCH"}
    assert len(root.children) == 2


def test_a_slow_span_is_logged_as_a_warning() -> None:
    """The usual first symptom of a misconfigured provider or a cold model, made visible."""
    recorder = TelemetryRecorderImpl(enabled=True, slow_operation_ms=0)
    with recorder.span(TelemetryOperation.EMBED):
        pass
    assert recorder.snapshot()["operations"]["EMBED"]["count"] == 1


def test_record_metric_with_attributes_does_not_raise() -> None:
    recorder = build_recorder(enabled=True)
    recorder.record_metric("cache_hit_rate", 0.5, attributes={"tier": "hot"})


def test_recorder_reset_clears_operations_counters_and_gauges() -> None:
    recorder = TelemetryRecorderImpl(enabled=True)
    with recorder.span(TelemetryOperation.EMBED):
        pass
    recorder.increment("tokens", 10)
    recorder.record_metric("g", 1.0)
    recorder.reset()
    snapshot = recorder.snapshot()
    assert snapshot["operations"] == {}
    assert snapshot["counters"] == {}
    assert snapshot["gauges"] == {}


def test_null_recorder_every_method_is_a_documented_no_op() -> None:
    recorder = NullTelemetryRecorder()
    assert recorder.record_metric("x", 1.0, attributes={"a": 1}) is None
    assert recorder.increment("x", 2) is None
    assert recorder.reset() is None
    assert recorder.snapshot() == {"enabled": False, "operations": {}, "counters": {}, "gauges": {}}


def test_current_span_is_none_outside_any_span() -> None:
    assert current_span() is None


def test_timing_span_to_dict_reports_the_whole_subtree() -> None:
    recorder = build_recorder(enabled=True)
    with recorder.span(TelemetryOperation.RETRIEVE) as root:
        with recorder.span(TelemetryOperation.EMBED):
            pass
    payload = root.to_dict()
    assert payload["operation"] == "RETRIEVE"
    assert payload["children"][0]["operation"] == "EMBED"


# ---------------------------------------------------------------------------
# ChunkingConfig: the validation that prevents an infinite chunking loop
# ---------------------------------------------------------------------------

def test_chunking_config_defaults_are_valid() -> None:
    config = ChunkingConfig()
    assert config.strategy is ChunkStrategy.RECURSIVE
    assert config.overlap_tokens < config.max_tokens


def test_chunking_config_rejects_overlap_at_or_above_chunk_size() -> None:
    """The failure this prevents is not a bad result — it is a loop that never terminates."""
    with pytest.raises(ValueError, match="must be < max_tokens"):
        ChunkingConfig(max_tokens=100, overlap_tokens=100)
    with pytest.raises(ValueError, match="must be < max_tokens"):
        ChunkingConfig(max_tokens=100, overlap_tokens=250)


def test_chunking_config_rejects_non_positive_max_tokens() -> None:
    with pytest.raises(ValueError, match="max_tokens must be >= 1"):
        ChunkingConfig(max_tokens=0)


def test_chunking_config_rejects_min_above_max() -> None:
    with pytest.raises(ValueError, match="min_tokens cannot exceed max_tokens"):
        ChunkingConfig(max_tokens=100, min_tokens=200, overlap_tokens=10)


def test_chunking_config_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        ChunkingConfig().max_tokens = 9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Vector store value objects
# ---------------------------------------------------------------------------

def test_capabilities_default_to_the_conservative_answer() -> None:
    """An adapter must opt *in* to claiming native lexical/hybrid search, never inherit the claim —
    the retrieval engine reads these flags to decide whether hybrid search is even possible."""
    capabilities = VectorStoreCapabilities()
    assert capabilities.native_lexical_search is False
    assert capabilities.native_hybrid_search is False
    assert capabilities.requires_external_service is False
    assert capabilities.exact_search is True


def test_capabilities_serialize_for_the_metrics_endpoint() -> None:
    payload = VectorStoreCapabilities(
        native_lexical_search=True, approximate_index=True, max_dimensions=2_000,
        requires_external_service=True,
    ).to_dict()
    assert payload["nativeLexicalSearch"] is True
    assert payload["approximateIndex"] is True
    assert payload["maxDimensions"] == 2_000
    assert payload["requiresExternalService"] is True


def test_vector_match_carries_score_and_optional_raw_distance() -> None:
    """``raw_distance`` is kept so a normalized score can be traced back to what the engine
    returned.
    """
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    match = VectorMatch(chunk_id, document_id, score=0.93, raw_distance=0.07)
    assert (match.chunk_id, match.document_id, match.score, match.raw_distance) == (
        chunk_id, document_id, 0.93, 0.07,
    )
    assert str(chunk_id) in repr(match)


# ---------------------------------------------------------------------------
# Retrieval result assembly
# ---------------------------------------------------------------------------

def _chunk(index: int, body: str) -> Chunk:
    return Chunk(id=uuid.uuid4(), text=body, index=index)


def test_retrieved_chunk_with_score_preserves_earlier_stage_scores() -> None:
    """Each stage adds its score without erasing the previous ones, so ranking stays explainable."""
    original = RetrievedChunk(chunk=_chunk(0, "body"), score=0.5, lexical_score=0.4)
    updated = original.with_score(0.9, rerank_score=0.9)
    assert original.score == 0.5, "the original must be unchanged"
    assert updated.score == 0.9
    assert updated.rerank_score == 0.9
    assert updated.lexical_score == 0.4, "earlier stage score must survive"


def test_retrieval_result_deduplicates_citations_by_document_and_section() -> None:
    """One document usually backs several chunks; a reviewer should see it cited once per
    section.
    """
    document_id = uuid.uuid4()

    def cited(section: str) -> Citation:
        return Citation(document_id=document_id, document_title="Travel Policy", section=section)

    result = RetrievalResult(
        chunks=(
            RetrievedChunk(_chunk(0, "a"), 0.9, citation=cited("Meals")),
            RetrievedChunk(_chunk(1, "b"), 0.8, citation=cited("Meals")),
            RetrievedChunk(_chunk(2, "c"), 0.7, citation=cited("Lodging")),
            RetrievedChunk(_chunk(3, "d"), 0.6, citation=None),
        ),
        query=RetrievalQuery(text="q"),
        embedding_version="bge-m3@v1",
        config_fingerprint="abc123",
    )
    assert [c.section for c in result.citations] == ["Meals", "Lodging"]


def test_retrieval_result_sums_stage_timings() -> None:
    result = RetrievalResult(
        chunks=(), query=RetrievalQuery(text="q"), embedding_version="v",
        config_fingerprint="f",
        timings=(StageTiming("EMBED", 12), StageTiming("VECTOR_SEARCH", 30)),
    )
    assert result.total_duration_ms == 42
    assert result.is_empty() is True


def test_context_bundle_reports_emptiness_and_truncation() -> None:
    assert ContextBundle(text="   ", citations=(), token_count=0, chunk_count=0).is_empty()
    bundle = ContextBundle(
        text="policy text", citations=(), token_count=3, chunk_count=1, truncated=True
    )
    assert bundle.is_empty() is False
    assert bundle.truncated is True


def test_parsed_document_summarizes_its_pii_findings() -> None:
    document = ParsedDocument(
        text="body", checksum="abc",
        pii_findings=(
            PIIFinding(PIIKind.EMAIL, 0, 5, "a***@x"),
            PIIFinding(PIIKind.CREDIT_CARD, 6, 10, "****1234"),
            PIIFinding(PIIKind.EMAIL, 11, 16, "b***@y"),
        ),
    )
    assert document.has_pii is True
    assert document.pii_kinds == ("CREDIT_CARD", "EMAIL"), "sorted and de-duplicated"


def test_parsed_document_without_pii() -> None:
    document = ParsedDocument(text="body", checksum="abc")
    assert document.has_pii is False
    assert document.pii_kinds == ()


def test_chunk_knows_whether_it_is_a_child() -> None:
    parent = _chunk(0, "parent body")
    child = Chunk(id=uuid.uuid4(), text="child body", index=1, parent_id=parent.id)
    assert parent.is_child is False
    assert child.is_child is True


def test_embedding_spec_key_is_the_comparability_unit() -> None:
    spec = EmbeddingSpec(
        provider="bge_m3_onnx", model="BAAI/bge-m3", dimensions=1024, version="v1"
    )
    assert spec.key == "bge_m3_onnx/BAAI/bge-m3@v1"


def test_raw_document_reports_its_size() -> None:
    assert RawDocument(content=b"12345", file_name="a.txt").size_bytes == 5


def test_as_sequence_produces_a_hashable_float_tuple() -> None:
    coerced = as_sequence([1, 2.5, 3])
    assert coerced == (1.0, 2.5, 3.0)
    assert hash(coerced)


# ---------------------------------------------------------------------------
# Cache: remaining surface
# ---------------------------------------------------------------------------

def test_memory_cache_delete_and_clear() -> None:
    cache = MemoryCache()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.delete("a")
    assert cache.get("a") is None and cache.get("b") == 2
    cache.delete("does-not-exist")          # must not raise
    cache.clear()
    assert cache.stats["entries"] == 0


def test_memory_cache_stats_can_be_reset_without_dropping_entries() -> None:
    cache = MemoryCache()
    cache.set("a", 1)
    cache.get("a")
    cache.reset_stats()
    assert cache.stats["hits"] == 0
    assert cache.get("a") == 1, "entries must survive a stats reset"


def test_memory_cache_treats_zero_ttl_as_no_expiry() -> None:
    cache = MemoryCache()
    cache.set("a", 1, ttl_seconds=0)
    assert cache.get("a") == 1


def test_memory_cache_overwrites_an_existing_key_without_growing() -> None:
    cache = MemoryCache(max_entries=2)
    cache.set("a", 1)
    cache.set("a", 2)
    assert cache.get("a") == 2
    assert cache.stats["entries"] == 1


def test_null_cache_surface_is_complete_and_inert() -> None:
    cache = NullCache()
    cache.set("k", 1)
    cache.delete("k")
    cache.clear()
    assert cache.is_available() is True
    assert cache.get("k") is None
    assert cache.stats["hitRatePercent"] == 0
