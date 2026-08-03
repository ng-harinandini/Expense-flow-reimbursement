"""Enumerations for the AI platform.

These reuse T003's :class:`app.models.enums._WireEnum` base so that ``coerce`` accepts values,
member names and case/separator variants uniformly across the whole codebase — an AI enum behaves
exactly like a domain enum at the API edge.

Which of these become **native PostgreSQL enum types** is a deliberate split:

* Lifecycle states with a small, slow-changing set and a guard requirement (document status,
  ingestion status) are PG enums — the database rejects a bad value.
* Open vocabularies that later phases must extend *without a migration* (source type, chunk
  strategy, provider kind, telemetry operation) are ``VARCHAR`` columns validated in Python. This
  mirrors the reasoning already applied to ``audit_logs.action`` in T003.
"""

from __future__ import annotations

from app.models.enums import _WireEnum, _pg_enum


class KnowledgeSourceType(_WireEnum):
    """What kind of knowledge a document carries.

    Drives the ingestion *profile* — which parser, chunking strategy, metadata requirements and
    retention rules apply (see :mod:`app.ai.ingestion.profiles`). Stored as ``VARCHAR``: new source
    types are a routine future addition and must not need a migration.
    """

    POLICY = "POLICY"
    EMPLOYEE_HANDBOOK = "EMPLOYEE_HANDBOOK"
    FINANCE_POLICY = "FINANCE_POLICY"
    TRAVEL_POLICY = "TRAVEL_POLICY"
    MEDICAL_POLICY = "MEDICAL_POLICY"
    COUNTRY_POLICY = "COUNTRY_POLICY"
    VENDOR_CONTRACT = "VENDOR_CONTRACT"
    VENDOR_MANUAL = "VENDOR_MANUAL"
    HISTORICAL_CLAIM = "HISTORICAL_CLAIM"
    HISTORICAL_DECISION = "HISTORICAL_DECISION"
    REVIEWER_NOTE = "REVIEWER_NOTE"
    FRAUD_INVESTIGATION = "FRAUD_INVESTIGATION"
    TAX_RULE = "TAX_RULE"
    GOVERNMENT_GUIDELINE = "GOVERNMENT_GUIDELINE"
    RECEIPT = "RECEIPT"
    INVOICE = "INVOICE"
    TRAINING_DOCUMENT = "TRAINING_DOCUMENT"
    FAQ = "FAQ"
    OTHER = "OTHER"


class DocumentStatus(_WireEnum):
    """Ingestion lifecycle of a knowledge document.

    ``SUPERSEDED`` is why this is a lifecycle rather than a flag: a new version of a policy never
    deletes the old one, because a 2025 claim must still be judged against 2025 policy. Superseded
    documents stay queryable behind an effective-date filter.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class IngestionStatus(_WireEnum):
    """Outcome of one ingestion run."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


class IngestionStage(_WireEnum):
    """Pipeline stage, recorded on a run so a failure names where it happened."""

    FETCH = "FETCH"
    PARSE = "PARSE"
    NORMALIZE = "NORMALIZE"
    CLASSIFY = "CLASSIFY"
    PII_SCAN = "PII_SCAN"
    DEDUPLICATE = "DEDUPLICATE"
    PERSIST_DOCUMENT = "PERSIST_DOCUMENT"
    CHUNK = "CHUNK"
    EMBED = "EMBED"
    INDEX = "INDEX"
    FINALIZE = "FINALIZE"
    #: Not run by ``ingest_document`` — a later, separate step over an already-indexed document.
    #: ``VARCHAR``, so adding it needs no migration; recorded on ``knowledge_ingestion_runs`` only
    #: if a future caller chooses to track extraction through the same run-tracker convention.
    EXTRACT_RULES = "EXTRACT_RULES"


class ChunkStrategy(_WireEnum):
    """Chunking strategies required by Task 3. ``VARCHAR``: strategies are pluggable."""

    RECURSIVE = "RECURSIVE"
    SEMANTIC = "SEMANTIC"
    HEADING = "HEADING"
    TOKEN = "TOKEN"
    PARENT_CHILD = "PARENT_CHILD"
    SLIDING_WINDOW = "SLIDING_WINDOW"
    HYBRID = "HYBRID"


class RetrievalStrategy(_WireEnum):
    """Which retrieval legs to run. ``HYBRID`` fuses lexical and dense."""

    LEXICAL = "LEXICAL"
    DENSE = "DENSE"
    HYBRID = "HYBRID"


class DistanceMetric(_WireEnum):
    """Vector distance metric. Cosine is the default because every provider in Task 4 returns
    L2-normalized vectors, for which cosine and inner product rank identically."""

    COSINE = "COSINE"
    EUCLIDEAN = "EUCLIDEAN"
    INNER_PRODUCT = "INNER_PRODUCT"


class ProviderKind(_WireEnum):
    """Category of a registered provider, used by the registry and the governance tables."""

    EMBEDDING = "EMBEDDING"
    LLM = "LLM"
    RERANK = "RERANK"
    VECTOR_STORE = "VECTOR_STORE"
    OCR = "OCR"
    CACHE = "CACHE"
    CHUNKING = "CHUNKING"


class PIIKind(_WireEnum):
    """Categories the deterministic PII scanner recognises."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CREDIT_CARD = "CREDIT_CARD"
    IBAN = "IBAN"
    NATIONAL_ID = "NATIONAL_ID"
    TAX_ID = "TAX_ID"
    IP_ADDRESS = "IP_ADDRESS"
    PASSPORT = "PASSPORT"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    PERSON_NAME = "PERSON_NAME"
    POSTAL_ADDRESS = "POSTAL_ADDRESS"


class RedactionMode(_WireEnum):
    """What to do with detected PII before text is indexed.

    ``TAG`` records findings without altering text (the default for internal policy documents);
    ``MASK`` replaces the span with a typed placeholder; ``REJECT`` refuses ingestion outright.
    """

    NONE = "NONE"
    TAG = "TAG"
    MASK = "MASK"
    REJECT = "REJECT"


class DuplicateSignalKind(_WireEnum):
    """The eleven duplicate-detection signals required by Task 10.

    Each is scored and reported independently so a verdict can state *which* signal fired — a
    single blended number would be unexplainable to a reviewer or an auditor.
    """

    SHA256 = "SHA256"
    IMAGE_HASH = "IMAGE_HASH"
    PERCEPTUAL_HASH = "PERCEPTUAL_HASH"
    OCR_SIMILARITY = "OCR_SIMILARITY"
    EMBEDDING_SIMILARITY = "EMBEDDING_SIMILARITY"
    VENDOR_ALIAS = "VENDOR_ALIAS"
    INVOICE_SIMILARITY = "INVOICE_SIMILARITY"
    HISTORICAL_CLAIM_SIMILARITY = "HISTORICAL_CLAIM_SIMILARITY"
    NEAR_DUPLICATE = "NEAR_DUPLICATE"
    MULTI_RECEIPT = "MULTI_RECEIPT"
    CROSS_EMPLOYEE = "CROSS_EMPLOYEE"


class DuplicateVerdict(_WireEnum):
    """Advisory outcome. Never sets a claim status — the deterministic engine decides."""

    NO_MATCH = "NO_MATCH"
    POSSIBLE = "POSSIBLE"
    LIKELY = "LIKELY"
    CONFIRMED = "CONFIRMED"


class DecisionMemoryKind(_WireEnum):
    """What kind of past event a decision-memory entry captures (Task 9)."""

    CLAIM = "CLAIM"
    REVIEW = "REVIEW"
    APPROVAL = "APPROVAL"
    REJECTION = "REJECTION"
    COMMENT = "COMMENT"
    FRAUD_FINDING = "FRAUD_FINDING"
    AI_EXPLANATION = "AI_EXPLANATION"


class TelemetryOperation(_WireEnum):
    """Timed operations (Task 14). ``VARCHAR``: new operations must not need a migration."""

    PARSE = "PARSE"
    CHUNK = "CHUNK"
    EMBED = "EMBED"
    VECTOR_UPSERT = "VECTOR_UPSERT"
    LEXICAL_SEARCH = "LEXICAL_SEARCH"
    VECTOR_SEARCH = "VECTOR_SEARCH"
    FUSION = "FUSION"
    RERANK = "RERANK"
    COMPRESS = "COMPRESS"
    CONTEXT_BUILD = "CONTEXT_BUILD"
    INFERENCE = "INFERENCE"
    DUPLICATE_SCAN = "DUPLICATE_SCAN"
    RETRIEVE = "RETRIEVE"


class PromptStatus(_WireEnum):
    """Prompt version lifecycle. Mirrors the ``policy_rules`` publish/retire pattern."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class LimitBasis(_WireEnum):
    """What a monetary limit is measured *per*.

    The dimension ``policy_rules`` has no column for: it stores a scalar ``expense_limit``, so
    "$40 per day" and "$40 per claim" are indistinguishable once written. A policy document states
    both, and conflating them changes which claims pass — so an extracted limit carries its basis
    here, on the proposal, where a reviewer can see it before publishing.

    ``FORMULA``, ``AGREEMENT`` and ``OTHER`` are the non-scalar cases: a rate multiplied by
    distance, a per-agreement amount, and anything else money cannot express. Those keep their
    wording in ``limit_expression`` and carry no amount at all.

    ``VARCHAR``: new bases are a routine future addition and must not need a migration.
    """

    PER_DAY = "PER_DAY"
    PER_TRIP = "PER_TRIP"
    PER_NIGHT = "PER_NIGHT"
    PER_EVENT = "PER_EVENT"
    PER_MONTH = "PER_MONTH"
    PER_YEAR = "PER_YEAR"
    PER_CLAIM = "PER_CLAIM"
    PER_PERSON_PER_EVENT = "PER_PERSON_PER_EVENT"
    PER_MILE = "PER_MILE"
    PER_TOOL_PER_YEAR = "PER_TOOL_PER_YEAR"
    PER_RECIPIENT_PER_YEAR = "PER_RECIPIENT_PER_YEAR"
    FORMULA = "FORMULA"
    AGREEMENT = "AGREEMENT"
    OTHER = "OTHER"


class ProposalStatus(_WireEnum):
    """Lifecycle of an extracted rule set awaiting human review.

    A closed set with a guard requirement — approving twice, or approving something already
    rejected, must be impossible — so this is a native PG enum rather than a ``VARCHAR``.

    ``SUPERSEDED`` is set when a newer extraction of the same document arrives: the older proposal
    stays readable as the record of what was proposed at the time, rather than being deleted.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


# --- Native PostgreSQL enum types --------------------------------------------
# Only the closed, slow-changing vocabularies get a database type; see the module docstring.

DOCUMENT_STATUS_ENUM_NAME = "ai_document_status"
INGESTION_STATUS_ENUM_NAME = "ai_ingestion_status"
PROMPT_STATUS_ENUM_NAME = "ai_prompt_status"
DUPLICATE_VERDICT_ENUM_NAME = "ai_duplicate_verdict"
PROPOSAL_STATUS_ENUM_NAME = "ai_proposal_status"

document_status_enum = _pg_enum(DocumentStatus, DOCUMENT_STATUS_ENUM_NAME)
ingestion_status_enum = _pg_enum(IngestionStatus, INGESTION_STATUS_ENUM_NAME)
prompt_status_enum = _pg_enum(PromptStatus, PROMPT_STATUS_ENUM_NAME)
duplicate_verdict_enum = _pg_enum(DuplicateVerdict, DUPLICATE_VERDICT_ENUM_NAME)
proposal_status_enum = _pg_enum(ProposalStatus, PROPOSAL_STATUS_ENUM_NAME)


__all__ = [
    "ChunkStrategy",
    "DOCUMENT_STATUS_ENUM_NAME",
    "DUPLICATE_VERDICT_ENUM_NAME",
    "DecisionMemoryKind",
    "DistanceMetric",
    "DocumentStatus",
    "DuplicateSignalKind",
    "DuplicateVerdict",
    "INGESTION_STATUS_ENUM_NAME",
    "IngestionStage",
    "IngestionStatus",
    "KnowledgeSourceType",
    "LimitBasis",
    "PIIKind",
    "PROMPT_STATUS_ENUM_NAME",
    "PROPOSAL_STATUS_ENUM_NAME",
    "PromptStatus",
    "ProposalStatus",
    "ProviderKind",
    "RedactionMode",
    "RetrievalStrategy",
    "TelemetryOperation",
    "document_status_enum",
    "duplicate_verdict_enum",
    "ingestion_status_enum",
    "prompt_status_enum",
    "proposal_status_enum",
]
