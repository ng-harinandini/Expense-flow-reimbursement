"""AI platform configuration.

A **separate** ``BaseSettings`` with an ``AI_`` env prefix rather than more fields on
:class:`app.core.config.Settings`. Three reasons:

* ``app.core`` must not acquire knowledge of the AI package (the layering rule in
  ``app/ai/__init__.py``, enforced by the architecture test).
* The AI platform has ~60 knobs. Merging them would triple the size of the core settings object for
  every consumer that does not use AI.
* It reads from the same ``.env``, so operators still configure one file.

**Every default is chosen so the platform boots and self-tests with no cloud credentials.** The
embedding provider defaults to ``bge_m3_onnx`` (local, CPU, no API key) and falls back to the
deterministic provider when the ONNX weights are absent — the same graceful-degradation convention
``textract_service`` already follows. Nothing here fails at import time; a missing capability
surfaces as :class:`~app.ai.core.errors.ProviderNotConfiguredError` (503) at the point of use.
"""

from __future__ import annotations

from functools import cached_property
from typing import Literal, Optional

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.core.enums import ChunkStrategy, DistanceMetric, RedactionMode, RetrievalStrategy

# bge-m3's native dimensionality. Named rather than inlined because changing the model means
# changing this *and* re-indexing, and a bare 1024 in a migration would not make that obvious.
BGE_M3_DIMENSIONS = 1024
BGE_M3_MAX_TOKENS = 8192


class AISettings(BaseSettings):
    """Settings for the AI Knowledge Platform. All overridable via ``AI_*`` env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AI_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- master switches (Task 13 feature flags) ------------------------------
    # Retrieval and ingestion are on by default because they are read-only and cannot influence a
    # decision. Anything that calls a *generative* model defaults OFF: an LLM must be switched on
    # deliberately, never by merely deploying this code.
    ENABLED: bool = True
    INGESTION_ENABLED: bool = True
    RETRIEVAL_ENABLED: bool = True
    RERANK_ENABLED: bool = True
    DUPLICATE_DETECTION_ENABLED: bool = True
    DECISION_MEMORY_ENABLED: bool = True
    LLM_ENABLED: bool = False
    LLM_EXPLANATIONS_ENABLED: bool = False

    # --- tenancy -------------------------------------------------------------
    DEFAULT_TENANT_ID: str = "default"

    # --- embeddings (Task 4) -------------------------------------------------
    EMBEDDING_PROVIDER: str = "bge_m3_onnx"
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIMENSIONS: int = BGE_M3_DIMENSIONS
    # Bumped by hand when the model or its pooling changes. Written next to every stored vector;
    # vectors of different versions are never compared (EmbeddingVersionConflictError).
    EMBEDDING_VERSION: str = "v1"
    EMBEDDING_MAX_INPUT_TOKENS: int = BGE_M3_MAX_TOKENS
    EMBEDDING_BATCH_SIZE: int = 16
    EMBEDDING_TIMEOUT_SECONDS: float = 60.0
    EMBEDDING_MAX_RETRIES: int = 2
    EMBEDDING_NORMALIZE: bool = True
    EMBEDDING_COST_PER_MILLION_TOKENS_USD: float = 0.0   # local model: no marginal cost
    # When the configured provider is unusable (no weights, no credentials), fall back to the
    # deterministic provider instead of failing. Keeps CI green offline; logged loudly at startup.
    EMBEDDING_FALLBACK_TO_DETERMINISTIC: bool = True
    # Local ONNX model cache. None => HuggingFace default (~/.cache/huggingface).
    EMBEDDING_MODEL_CACHE_DIR: Optional[str] = None
    EMBEDDING_ONNX_THREADS: int = 0                      # 0 => let onnxruntime decide

    # --- vector store (Task 5) ----------------------------------------------
    VECTOR_STORE: Literal[
        "pgvector", "postgres_native", "memory",
        "qdrant", "opensearch", "pinecone", "milvus", "weaviate",
    ] = "pgvector"
    VECTOR_DISTANCE_METRIC: DistanceMetric = DistanceMetric.COSINE
    # HNSW build/search parameters. ef_search is the recall/latency dial at query time.
    VECTOR_HNSW_M: int = 16
    VECTOR_HNSW_EF_CONSTRUCTION: int = 64
    VECTOR_HNSW_EF_SEARCH: int = 100
    VECTOR_UPSERT_BATCH_SIZE: int = 128

    # External store connection details (all optional; absent => ProviderNotConfiguredError).
    QDRANT_URL: Optional[str] = None
    QDRANT_API_KEY: Optional[str] = None
    OPENSEARCH_URL: Optional[str] = None
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_INDEX: Optional[str] = None
    MILVUS_URI: Optional[str] = None
    WEAVIATE_URL: Optional[str] = None
    VECTOR_COLLECTION: str = "expenseflow_knowledge"

    # --- chunking (Task 3) --------------------------------------------------
    CHUNK_STRATEGY: ChunkStrategy = ChunkStrategy.RECURSIVE
    CHUNK_MAX_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 64
    CHUNK_MIN_TOKENS: int = 32
    # Parent-child: parents give a model surrounding context, children are what gets embedded.
    CHUNK_PARENT_MAX_TOKENS: int = 2048
    CHUNK_SEMANTIC_THRESHOLD: float = 0.82
    CHUNK_SLIDING_STRIDE_TOKENS: int = 256

    # --- retrieval (Task 7) -------------------------------------------------
    RETRIEVAL_STRATEGY: RetrievalStrategy = RetrievalStrategy.HYBRID
    RETRIEVAL_TOP_K: int = 8
    RETRIEVAL_CANDIDATE_K: int = 50
    RETRIEVAL_SCORE_THRESHOLD: float = 0.0
    # Dense is weighted higher than lexical: bge-m3 is multilingual and handles paraphrase, which is
    # most policy queries. Lexical still matters for exact codes and amounts, hence non-zero.
    RETRIEVAL_LEXICAL_WEIGHT: float = 0.4
    RETRIEVAL_DENSE_WEIGHT: float = 0.6
    # Reciprocal Rank Fusion damping. 60 is the value from the original RRF paper and is not
    # sensitive; exposed because tuning it is the cheapest fusion experiment available.
    RETRIEVAL_RRF_K: int = 60
    RETRIEVAL_CONTEXT_BUDGET_TOKENS: int = 4000
    RETRIEVAL_COMPRESSION_ENABLED: bool = False
    RETRIEVAL_INCLUDE_SUPERSEDED: bool = False

    # --- reranking ----------------------------------------------------------
    RERANK_PROVIDER: str = "heuristic"      # heuristic | cohere | bedrock | cross_encoder_local
    RERANK_MODEL: Optional[str] = None
    RERANK_TOP_N: int = 8
    RERANK_TIMEOUT_SECONDS: float = 15.0
    COHERE_API_KEY: Optional[str] = None

    # --- LLM (advisory only — never a decision maker) -----------------------
    LLM_PROVIDER: str = "none"              # none | bedrock | gemini | openai | anthropic
    LLM_MODEL: Optional[str] = None
    LLM_TIMEOUT_SECONDS: float = 45.0
    LLM_MAX_OUTPUT_TOKENS: int = 1024
    LLM_TEMPERATURE: float = 0.0            # advisory text must be as reproducible as possible
    BEDROCK_REGION: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None

    # --- caching (Task 15) --------------------------------------------------
    CACHE_BACKEND: Literal["memory", "redis", "none"] = "memory"
    CACHE_REDIS_URL: Optional[str] = None
    EMBEDDING_CACHE_ENABLED: bool = True
    # 7 days: a vector stays valid until its model version changes.
    EMBEDDING_CACHE_TTL_SECONDS: int = 604800
    RETRIEVAL_CACHE_ENABLED: bool = True
    RETRIEVAL_CACHE_TTL_SECONDS: int = 300        # short: the corpus changes under it
    CACHE_MAX_ENTRIES: int = 10_000

    # --- parsing / PII (Task 2) ---------------------------------------------
    OCR_PROVIDER: str = "textract"          # reuses T001's textract_service, fallback included
    PII_DETECTION_ENABLED: bool = True
    PII_REDACTION_MODE: RedactionMode = RedactionMode.TAG
    PII_REJECT_KINDS: str = ""              # comma-separated PIIKind values to refuse outright
    MAX_DOCUMENT_BYTES: int = 25 * 1024 * 1024   # also the API upload cap (retires debt #11)
    MIN_QUALITY_SCORE: float = 0.0          # below this, ingestion warns but still indexes

    # --- duplicate detection thresholds (Task 10) ---------------------------
    # Thresholds are configuration, never constants in code, because tuning them is an operational
    # activity: finance will want them tighter or looser without a deploy.
    DUP_SHA256_THRESHOLD: float = 1.0
    DUP_PERCEPTUAL_HASH_THRESHOLD: float = 0.90
    DUP_OCR_SIMILARITY_THRESHOLD: float = 0.85
    DUP_EMBEDDING_SIMILARITY_THRESHOLD: float = 0.93
    DUP_VENDOR_ALIAS_THRESHOLD: float = 0.85
    DUP_INVOICE_SIMILARITY_THRESHOLD: float = 0.90
    DUP_NEAR_DUPLICATE_THRESHOLD: float = 0.80
    DUP_LIKELY_SCORE: float = 0.75
    DUP_CONFIRMED_SCORE: float = 0.95
    DUP_AMOUNT_TOLERANCE: float = 0.01
    DUP_DATE_WINDOW_DAYS: int = 7
    DUP_MAX_CANDIDATES: int = 200
    DUP_CROSS_EMPLOYEE_ENABLED: bool = True

    # --- telemetry (Task 14) ------------------------------------------------
    TELEMETRY_ENABLED: bool = True
    # Persist a row per operation to ai_inference_logs. Off => spans are logged but not stored.
    TELEMETRY_PERSIST: bool = True
    TELEMETRY_SLOW_OPERATION_MS: int = 2000

    # --- validators ---------------------------------------------------------

    @field_validator("EMBEDDING_DIMENSIONS")
    @classmethod
    def _positive_dimensions(cls, v: int) -> int:
        if v < 1:
            raise ValueError("AI_EMBEDDING_DIMENSIONS must be >= 1.")
        return v

    @field_validator("RETRIEVAL_LEXICAL_WEIGHT", "RETRIEVAL_DENSE_WEIGHT")
    @classmethod
    def _weight_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Retrieval weights must be between 0.0 and 1.0.")
        return v

    @field_validator(
        "DUP_SHA256_THRESHOLD", "DUP_PERCEPTUAL_HASH_THRESHOLD",
        "DUP_OCR_SIMILARITY_THRESHOLD", "DUP_EMBEDDING_SIMILARITY_THRESHOLD",
        "DUP_VENDOR_ALIAS_THRESHOLD", "DUP_INVOICE_SIMILARITY_THRESHOLD",
        "DUP_NEAR_DUPLICATE_THRESHOLD", "DUP_LIKELY_SCORE", "DUP_CONFIRMED_SCORE",
        "CHUNK_SEMANTIC_THRESHOLD",
    )
    @classmethod
    def _similarity_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Similarity thresholds must be between 0.0 and 1.0.")
        return v

    @field_validator("CHUNK_OVERLAP_TOKENS")
    @classmethod
    def _overlap_sane(cls, v: int) -> int:
        if v < 0:
            raise ValueError("AI_CHUNK_OVERLAP_TOKENS must be >= 0.")
        return v

    # --- derived ------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def embedding_spec_key(self) -> str:
        """The version string stored beside every vector, e.g. ``bge_m3_onnx/BAAI/bge-m3@v1``."""
        return f"{self.EMBEDDING_PROVIDER}/{self.EMBEDDING_MODEL}@{self.EMBEDDING_VERSION}"

    @cached_property
    def pii_reject_kinds(self) -> frozenset[str]:
        return frozenset(
            part.strip().upper() for part in self.PII_REJECT_KINDS.split(",") if part.strip()
        )

    @property
    def chunk_overlap_is_valid(self) -> bool:
        """Overlap must be smaller than the chunk size or chunking cannot advance."""
        return self.CHUNK_OVERLAP_TOKENS < self.CHUNK_MAX_TOKENS

    def require_valid(self) -> None:
        """Cross-field checks that a single-field validator cannot express.

        Called once at application startup so a contradictory configuration fails loudly at boot
        rather than at the first request — and never silently produces an infinite chunk loop.
        """
        if not self.chunk_overlap_is_valid:
            raise ValueError(
                f"AI_CHUNK_OVERLAP_TOKENS ({self.CHUNK_OVERLAP_TOKENS}) must be less than "
                f"AI_CHUNK_MAX_TOKENS ({self.CHUNK_MAX_TOKENS}); otherwise chunking cannot advance."
            )
        if self.CHUNK_MIN_TOKENS > self.CHUNK_MAX_TOKENS:
            raise ValueError("AI_CHUNK_MIN_TOKENS cannot exceed AI_CHUNK_MAX_TOKENS.")
        if self.RETRIEVAL_CANDIDATE_K < self.RETRIEVAL_TOP_K:
            raise ValueError("AI_RETRIEVAL_CANDIDATE_K must be >= AI_RETRIEVAL_TOP_K.")
        if self.DUP_CONFIRMED_SCORE < self.DUP_LIKELY_SCORE:
            raise ValueError("AI_DUP_CONFIRMED_SCORE must be >= AI_DUP_LIKELY_SCORE.")
        if self.CACHE_BACKEND == "redis" and not self.CACHE_REDIS_URL:
            raise ValueError("AI_CACHE_BACKEND=redis requires AI_CACHE_REDIS_URL.")

    def retrieval_config_fingerprint_source(self) -> dict[str, object]:
        """The settings that affect a retrieval outcome, for the reproducibility fingerprint.

        Only knobs that change *results* belong here — timeouts and batch sizes do not, or an
        unrelated performance tweak would invalidate every recorded fingerprint.
        """
        return {
            "strategy": self.RETRIEVAL_STRATEGY.value,
            "topK": self.RETRIEVAL_TOP_K,
            "candidateK": self.RETRIEVAL_CANDIDATE_K,
            "threshold": self.RETRIEVAL_SCORE_THRESHOLD,
            "lexicalWeight": self.RETRIEVAL_LEXICAL_WEIGHT,
            "denseWeight": self.RETRIEVAL_DENSE_WEIGHT,
            "rrfK": self.RETRIEVAL_RRF_K,
            "rerank": self.RERANK_ENABLED,
            "rerankProvider": self.RERANK_PROVIDER if self.RERANK_ENABLED else None,
            "compression": self.RETRIEVAL_COMPRESSION_ENABLED,
            "includeSuperseded": self.RETRIEVAL_INCLUDE_SUPERSEDED,
            "embeddingSpec": self.embedding_spec_key,
            "vectorStore": self.VECTOR_STORE,
            "metric": self.VECTOR_DISTANCE_METRIC.value,
        }


ai_settings = AISettings()

__all__ = ["AISettings", "BGE_M3_DIMENSIONS", "BGE_M3_MAX_TOKENS", "ai_settings"]
