"""Immutable value types shared by every layer of the AI platform.

These are the vocabulary the ``interfaces`` protocols are written in, which is why they live in
``core`` and import nothing but the standard library and :mod:`app.ai.core.enums`. A parser, a
chunker, an embedding provider and a vector store can therefore be developed and tested in complete
isolation from one another and from the database.

Everything is a **frozen** dataclass. Retrieval passes the same objects through many stages
(filter → search → fuse → rerank → compress → build context) and a mutable payload there is how
stages start quietly corrupting each other's inputs. Where a stage needs to add information it
returns a new object via ``dataclasses.replace`` or a purpose-built ``with_*`` helper.

Two conventions worth knowing:

* **Vectors are tuples, not lists.** Hashability makes them safe cache keys and prevents in-place
  mutation of a vector that is already indexed.
* **Every retrieval result is traceable.** :class:`RetrievedChunk` carries per-stage scores rather
  than one blended number, and :class:`RetrievalResult` carries the embedding version and a
  configuration fingerprint, so any past answer can be re-derived or explained to an auditor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import (
    ChunkStrategy,
    DistanceMetric,
    DuplicateSignalKind,
    DuplicateVerdict,
    KnowledgeSourceType,
    PIIKind,
    RetrievalStrategy,
)

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Bytes plus provenance, as handed over by a knowledge source.

    ``content`` is the untouched original: the checksum that drives idempotency is computed over
    exactly these bytes, so any normalization happening later cannot change a document's identity.
    """

    content: bytes
    file_name: str
    mime_type: Optional[str] = None
    source_type: KnowledgeSourceType = KnowledgeSourceType.OTHER
    source_uri: Optional[str] = None
    tenant_id: str = "default"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class DocumentSection:
    """A heading-delimited region of a parsed document.

    ``heading_path`` is the full ancestor chain (``["Travel", "Meals", "Alcohol"]``), which is what
    lets a retrieved chunk be cited as a place in a document rather than an opaque offset.
    """

    text: str
    heading: Optional[str] = None
    level: int = 0
    page_number: Optional[int] = None
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PIIFinding:
    """One detected piece of personal data.

    Stores the span and a redacted sample only — never the raw value. Persisting the matched text
    would copy the very data the scan exists to control into a second table.
    """

    kind: PIIKind
    start: int
    end: int
    sample: str
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The normalized, analysed result of parsing one :class:`RawDocument`."""

    text: str
    checksum: str
    sections: tuple[DocumentSection, ...] = ()
    page_count: Optional[int] = None
    language: Optional[str] = None
    detected_type: Optional[KnowledgeSourceType] = None
    quality_score: float = 0.0
    pii_findings: tuple[PIIFinding, ...] = ()
    parser: str = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_pii(self) -> bool:
        return bool(self.pii_findings)

    @property
    def pii_kinds(self) -> tuple[str, ...]:
        return tuple(sorted({f.kind.value for f in self.pii_findings}))


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChunkMetadata:
    """The Task-6 metadata every chunk carries.

    The fields promoted to attributes here are precisely the ones that are *filtered on*, and they
    exist as real indexed columns in ``knowledge_chunks``. ``tags`` and ``extra`` are the extension
    point, stored as JSONB — so a new metadata key never needs a migration, while a filterable one
    stays indexable. (Plan D3.)
    """

    document_id: Optional[uuid.UUID] = None
    document_version: int = 1
    policy_version: Optional[str] = None
    department: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    section: Optional[str] = None
    page_number: Optional[int] = None
    owner: Optional[str] = None
    checksum: Optional[str] = None
    tenant_id: str = "default"
    source_type: Optional[KnowledgeSourceType] = None
    tags: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def is_effective_on(self, when: date) -> bool:
        """Whether this chunk's policy applies on ``when``.

        Superseded documents are never deleted, so effective-dating is how a 2025 claim keeps being
        judged against 2025 policy after a 2026 revision is indexed.
        """
        if self.effective_date and when < self.effective_date:
            return False
        if self.expiry_date and when > self.expiry_date:
            return False
        return True


@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrievable unit of text.

    ``parent_id`` supports parent–child chunking: children are embedded for precise matching while
    the parent supplies the wider context handed to a model. The parent's text is stored once and
    referenced, never duplicated into each child.
    """

    id: uuid.UUID
    text: str
    index: int
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    parent_id: Optional[uuid.UUID] = None
    token_count: int = 0
    heading_path: tuple[str, ...] = ()
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata)

    @property
    def is_child(self) -> bool:
        return self.parent_id is not None


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmbeddingSpec:
    """Identity of an embedding model, and the unit of comparability.

    ``version`` is the string written next to every stored vector. Comparing vectors across two
    different versions is meaningless, so the platform treats a model change as a *new version*
    that is indexed alongside the old rather than an overwrite — and refuses cross-version
    comparison outright (:class:`~app.ai.core.errors.EmbeddingVersionConflictError`).
    """

    provider: str
    model: str
    dimensions: int
    version: str
    metric: DistanceMetric = DistanceMetric.COSINE
    normalized: bool = True
    max_input_tokens: int = 512
    cost_per_million_tokens_usd: float = 0.0

    @property
    def key(self) -> str:
        """Stable identifier, e.g. ``bge_m3_onnx/BAAI/bge-m3@v1``."""
        return f"{self.provider}/{self.model}@{self.version}"


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """One vector plus the spec that produced it.

    The spec travels *with* the numbers rather than being remembered separately, because a bare
    list of floats whose provenance was lost elsewhere is the root of nearly every silent
    retrieval-quality regression.
    """

    values: tuple[float, ...]
    spec_key: str
    dimensions: int

    def __post_init__(self) -> None:
        if len(self.values) != self.dimensions:
            raise ValueError(
                f"Vector length {len(self.values)} does not match declared "
                f"dimensions {self.dimensions}."
            )


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """Cost and latency accounting for one embedding call (Task 4 / Task 14)."""

    texts: int = 0
    tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    provider: str = ""
    model: str = ""

    def merged_with(self, other: EmbeddingUsage) -> EmbeddingUsage:
        """Accumulate across batches; latency sums because batches run sequentially."""
        return EmbeddingUsage(
            texts=self.texts + other.texts,
            tokens=self.tokens + other.tokens,
            latency_ms=self.latency_ms + other.latency_ms,
            cost_usd=self.cost_usd + other.cost_usd,
            cache_hits=self.cache_hits + other.cache_hits,
            cache_misses=self.cache_misses + other.cache_misses,
            provider=other.provider or self.provider,
            model=other.model or self.model,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Vectors for a batch of texts, with the usage they incurred."""

    vectors: tuple[EmbeddingVector, ...]
    spec: EmbeddingSpec
    usage: EmbeddingUsage = field(default_factory=EmbeddingUsage)


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """A chunk paired with its vector — the unit a vector store indexes."""

    chunk: Chunk
    vector: EmbeddingVector

    @property
    def id(self) -> uuid.UUID:
        return self.chunk.id


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Citation:
    """Where a retrieved statement came from.

    Non-optional in every context the platform builds. An AI explanation a reviewer cannot trace
    back to a document, version, section and effective date is not usable as evidence in a
    reimbursement dispute, so citations are structural rather than best-effort.
    """

    document_id: uuid.UUID
    document_title: str
    document_version: int = 1
    source_type: Optional[KnowledgeSourceType] = None
    section: Optional[str] = None
    page_number: Optional[int] = None
    effective_date: Optional[date] = None
    source_uri: Optional[str] = None

    def to_label(self) -> str:
        """Short human-readable reference, e.g. ``Travel Policy v3, Meals, p.12``."""
        parts = [f"{self.document_title} v{self.document_version}"]
        if self.section:
            parts.append(self.section)
        if self.page_number is not None:
            parts.append(f"p.{self.page_number}")
        return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk that matched, with **per-stage** scores.

    Keeping the stage scores apart from the final score is what makes a ranking explainable: a
    reviewer or an engineer can see that a result placed third because lexical matched strongly but
    the cross-encoder disagreed, which a single blended float cannot express.
    """

    chunk: Chunk
    score: float
    citation: Optional[Citation] = None
    lexical_score: Optional[float] = None
    dense_score: Optional[float] = None
    fused_score: Optional[float] = None
    rerank_score: Optional[float] = None
    rank: Optional[int] = None

    @property
    def id(self) -> uuid.UUID:
        return self.chunk.id

    @property
    def text(self) -> str:
        return self.chunk.text

    def with_score(self, score: float, **stage_scores: Optional[float]) -> RetrievedChunk:
        """Return a copy carrying a new final score and any stage scores just computed."""
        return replace(self, score=score, **stage_scores)


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    """One store-agnostic predicate.

    Deliberately not raw SQL and not a vendor filter object: each adapter translates this into its
    own dialect, which is what allows the same query to run against pgvector, Qdrant or OpenSearch
    unchanged. ``op`` is validated by the filter compiler (:mod:`app.ai.retrieval.filters`).
    """

    field_name: str
    op: str = "eq"          # eq | ne | in | nin | gt | gte | lt | lte | contains | exists
    value: Any = None


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """Everything a retrieval call needs, as one immutable request object.

    Passing a request object rather than fifteen keyword arguments means a query can be logged,
    hashed for caching, replayed from an audit record, and extended without changing every
    signature between the API edge and the store.
    """

    text: str
    tenant_id: str = "default"
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    top_k: int = 8
    candidate_k: int = 50
    score_threshold: float = 0.0
    filters: tuple[MetadataFilter, ...] = ()
    effective_on: Optional[date] = None
    rerank: bool = True
    compress: bool = False
    context_budget_tokens: int = 4000
    lexical_weight: float = 0.4
    dense_weight: float = 0.6
    include_superseded: bool = False
    embedding_version: Optional[str] = None

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1.")
        if self.candidate_k < self.top_k:
            # Reranking cannot improve on a candidate set smaller than the requested output.
            raise ValueError("candidate_k must be >= top_k.")

    def with_filters(self, *extra: MetadataFilter) -> RetrievalQuery:
        return replace(self, filters=self.filters + tuple(extra))


@dataclass(frozen=True, slots=True)
class StageTiming:
    """Wall-clock cost of one retrieval stage (Task 14)."""

    stage: str
    duration_ms: int
    candidates_in: int = 0
    candidates_out: int = 0


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The outcome of one retrieval, with everything needed to reproduce it."""

    chunks: tuple[RetrievedChunk, ...]
    query: RetrievalQuery
    embedding_version: str
    config_fingerprint: str
    timings: tuple[StageTiming, ...] = ()
    total_candidates: int = 0
    truncated: bool = False
    cache_hit: bool = False

    @property
    def citations(self) -> tuple[Citation, ...]:
        """Unique citations in rank order (a document may back several chunks)."""
        seen: set[tuple[uuid.UUID, Optional[str]]] = set()
        out: list[Citation] = []
        for c in self.chunks:
            if c.citation is None:
                continue
            key = (c.citation.document_id, c.citation.section)
            if key not in seen:
                seen.add(key)
                out.append(c.citation)
        return tuple(out)

    @property
    def total_duration_ms(self) -> int:
        return sum(t.duration_ms for t in self.timings)

    def is_empty(self) -> bool:
        return not self.chunks


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Assembled, budget-bounded context ready to hand to a model or a reviewer.

    ``truncated`` is surfaced rather than hidden: a caller that silently received less evidence
    than it asked for would draw conclusions from a partial corpus without knowing it.
    """

    text: str
    citations: tuple[Citation, ...]
    token_count: int
    chunk_count: int
    truncated: bool = False
    embedding_version: str = ""
    config_fingerprint: str = ""
    retrieval: Optional[RetrievalResult] = None

    def is_empty(self) -> bool:
        return not self.text.strip()


# ---------------------------------------------------------------------------
# Duplicate detection (Task 10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DuplicateSignal:
    """One independently-scored, independently-explainable duplicate signal."""

    kind: DuplicateSignalKind
    score: float
    threshold: float
    weight: float = 1.0
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.score >= self.threshold


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """A candidate the subject may duplicate, and why."""

    target_kind: str                       # "claim" | "receipt" | "invoice"
    target_id: str
    signals: tuple[DuplicateSignal, ...]
    score: float
    verdict: DuplicateVerdict = DuplicateVerdict.NO_MATCH
    employee_code: Optional[str] = None

    @property
    def fired_signals(self) -> tuple[DuplicateSignalKind, ...]:
        return tuple(s.kind for s in self.signals if s.fired)

    def explain(self) -> str:
        """Reviewer-facing sentence naming the signals that fired and their scores."""
        if not self.fired_signals:
            return "No duplicate signals exceeded their thresholds."
        parts = [f"{s.kind.value} {s.score:.2f} (>= {s.threshold:.2f})"
                 for s in self.signals if s.fired]
        return f"{self.verdict.value}: " + "; ".join(parts)


@dataclass(frozen=True, slots=True)
class DuplicateReport:
    """Advisory result of a duplicate scan.

    Explicitly advisory. The deterministic duplicate block in ``ClaimService`` remains the only
    thing that can stop a submission; this report enriches a reviewer's view and never sets status.
    """

    subject_kind: str
    subject_id: str
    matches: tuple[DuplicateMatch, ...] = ()
    scanned: int = 0
    duration_ms: int = 0

    @property
    def verdict(self) -> DuplicateVerdict:
        """The strongest verdict among matches."""
        order = {
            DuplicateVerdict.NO_MATCH: 0,
            DuplicateVerdict.POSSIBLE: 1,
            DuplicateVerdict.LIKELY: 2,
            DuplicateVerdict.CONFIRMED: 3,
        }
        if not self.matches:
            return DuplicateVerdict.NO_MATCH
        return max((m.verdict for m in self.matches), key=lambda v: order[v])

    @property
    def is_advisory_only(self) -> bool:
        """Always ``True``. Present so the guarantee is assertable in a test, not just
        documented.
        """
        return True


# ---------------------------------------------------------------------------
# Structured model output (Task 16)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token accounting for one model invocation."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A model's reply, with the provenance needed to reproduce it.

    ``prompt_version`` is not optional in practice: without it a stored explanation cannot be
    regenerated once the prompt registry moves on.
    """

    text: str
    provider: str
    model: str
    prompt_version: Optional[str] = None
    structured: Optional[Mapping[str, Any]] = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    cost_usd: float = 0.0
    finish_reason: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class DecisionMemoryRecord:
    """A past decision, embedded so future reasoning can retrieve comparable cases (Task 9)."""

    kind: str
    subject_id: str
    summary: str
    claim_id: Optional[uuid.UUID] = None
    employee_code: Optional[str] = None
    outcome: Optional[str] = None
    occurred_at: Optional[datetime] = None
    tenant_id: str = "default"
    metadata: Mapping[str, Any] = field(default_factory=dict)


def as_sequence(vectors: Sequence[float]) -> tuple[float, ...]:
    """Coerce any float sequence into the hashable tuple form vectors are stored as."""
    return tuple(float(v) for v in vectors)


__all__ = [
    "Chunk",
    "ChunkMetadata",
    "Citation",
    "ContextBundle",
    "DecisionMemoryRecord",
    "DocumentSection",
    "DuplicateMatch",
    "DuplicateReport",
    "DuplicateSignal",
    "EmbeddedChunk",
    "EmbeddingResult",
    "EmbeddingSpec",
    "EmbeddingUsage",
    "EmbeddingVector",
    "LLMResponse",
    "MetadataFilter",
    "PIIFinding",
    "ParsedDocument",
    "RawDocument",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievedChunk",
    "StageTiming",
    "TokenUsage",
    "as_sequence",
]
