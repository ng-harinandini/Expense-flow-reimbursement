"""The ingestion pipeline: one idempotent, resumable, fully audited path from raw bytes to an
indexed, versioned, embedded document.

Composes every prior macro without duplicating any of their logic: :mod:`app.ai.parsing` resolves a
format parser and exposes five independent post-parse stages (this module is where they finally get
wired into a pipeline, per ``DocumentParser``'s own contract clause 4, which deliberately left that
composition to here); :mod:`app.ai.chunking` resolves a strategy by
:mod:`app.ai.ingestion.profiles`; :class:`~app.ai.embeddings.EmbeddingService` embeds the resulting
chunks; and :func:`~app.ai.vector_store.factory.resolve_vector_store` indexes them.

**Stage order actually executed** differs from :class:`~app.ai.core.enums.IngestionStage`'s
declared enum order in one place, deliberately: DEDUPLICATE runs immediately after FETCH, using the
raw-byte checksum alone, rather than after PARSE/NORMALIZE/CLASSIFY/PII_SCAN as the enum's member
order might suggest. A checksum match is exact-byte identity — nothing later in the pipeline can
change that answer — so checking it first means an exact re-upload short-circuits before any
parsing, chunking or embedding cost is spent, which is what "idempotent" should actually mean in
practice. The stage name recorded against the run is still ``DEDUPLICATE``, only its position moved
earlier.

**Two-phase commit around the run ledger.** The run row is created and committed by itself,
independent of everything that follows, so it stays visible even if the rest of the pipeline rolls
back. On failure, ``session.rollback()`` discards every document/chunk/embedding write from this
attempt, then a *fresh* transaction records the run as ``FAILED`` and commits that alone — otherwise
the very failure a caller most wants visible would itself never survive the rollback it describes.

**Where "the failing stage" is actually recorded.**
:meth:`~app.ai.repositories.knowledge_repository.KnowledgeIngestionRunRepository.finish` — an
M2-shipped method out of this macro's scope to change — unconditionally sets ``run.stage`` to
``FINALIZE`` for every outcome, including failure. So the stage that actually failed is prefixed
onto ``error_message`` instead (``"[PARSE] ProviderError: ..."``), which is what a caller or the
future ``/api/ai/knowledge/ingestion-runs`` endpoint (M13) should read to answer "which stage
broke".

**A FETCH failure writes no run row.** Every other stage failure does. FETCH is the one stage that
happens *before* a document's identity (source type, file name, checksum) is known at all — there is
nothing yet to hang an audit row on — so a source that cannot be read simply raises, and the caller
(who constructed the ``Source`` and therefore already knows what it tried to fetch) handles it like
any other I/O error.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.chunking.config import build_chunking_config
from app.ai.chunking.factory import resolve_chunker
from app.ai.core.config import AISettings, ai_settings
from app.ai.core.enums import (
    ChunkStrategy,
    DocumentStatus,
    IngestionStage,
    IngestionStatus,
    RedactionMode,
)
from app.ai.core.errors import PIIRejectionError
from app.ai.core.types import Chunk, ChunkMetadata, EmbeddedChunk, ParsedDocument
from app.ai.embeddings import EmbeddingService
from app.ai.ingestion.profiles import profile_for
from app.ai.ingestion.runs import RunTracker
from app.ai.ingestion.sources import Source
from app.ai.interfaces.vector_store import VectorStore
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.ai.parsing import resolve_parser
from app.ai.parsing.checksum import checksum_for, repeated_lines
from app.ai.parsing.classifier import classify
from app.ai.parsing.language import detect_document_language
from app.ai.parsing.normalizer import clean_sections, clean_text
from app.ai.parsing.pii import redact_text, scan
from app.ai.parsing.quality import score as quality_score
from app.ai.providers.embeddings import resolve_embedding_provider
from app.ai.repositories.knowledge_repository import (
    EmbeddingWrite,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEmbeddingRepository,
    KnowledgeIngestionRunRepository,
)
from app.ai.vector_store.factory import resolve_vector_store
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentMetadataInput:
    """Business metadata a parser cannot derive on its own — supplied by the caller.

    ``title`` is the versioning identity: two ingestions with the same title are treated as
    versions of the same logical document
    (see :meth:`KnowledgeDocumentRepository.latest_for_title`), regardless of how their bytes
    differ. Defaults to the source file's name, extension stripped.
    """

    title: str | None = None
    department: str | None = None
    country: str | None = None
    currency: str | None = None
    category: str | None = None
    policy_version: str | None = None
    owner: str | None = None
    effective_date: date | None = None
    expiry_date: date | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of one :func:`ingest_document` call."""

    status: IngestionStatus
    run_id: uuid.UUID
    document_id: uuid.UUID | None
    document_version: int | None
    chunks_created: int
    chunks_skipped: int
    embeddings_created: int
    duration_ms: int


def _default_title(file_name: str) -> str:
    stem = Path(file_name).stem
    return stem or file_name


def _chunk_to_row(chunk: Chunk, *, tenant_id: str) -> KnowledgeChunk:
    metadata = chunk.metadata
    return KnowledgeChunk(
        id=chunk.id,
        tenant_id=tenant_id,
        document_id=metadata.document_id,
        parent_chunk_id=chunk.parent_id,
        chunk_index=chunk.index,
        content=chunk.text,
        content_tokens=chunk.token_count,
        strategy=chunk.strategy.value,
        checksum_sha256=metadata.checksum,
        section=metadata.section,
        page_number=metadata.page_number,
        heading_path=list(chunk.heading_path),
        policy_version=metadata.policy_version,
        department=metadata.department,
        country=metadata.country,
        currency=metadata.currency,
        category=metadata.category,
        language=metadata.language,
        source_type=metadata.source_type.value if metadata.source_type else None,
        owner=metadata.owner,
        effective_date=metadata.effective_date,
        expiry_date=metadata.expiry_date,
        tags=list(metadata.tags),
        chunk_metadata=dict(metadata.extra),
    )


def ingest_document(
    source: Source,
    *,
    session: Session,
    metadata: DocumentMetadataInput | None = None,
    chunk_strategy: ChunkStrategy | None = None,
    chunk_overrides: Mapping[str, Any] | None = None,
    embedding_service: EmbeddingService | None = None,
    vector_store: VectorStore | None = None,
    settings: AISettings | None = None,
    actor_sub: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    manage_transaction: bool = True,
) -> IngestionResult:
    """Run one document through fetch, dedup, parse, normalize, classify, PII-scan, persist,
    chunk, embed and index — atomically, and fully audited in ``knowledge_ingestion_runs``.

    Every dependency below the document/run repositories is resolved to its configured default
    when not supplied, so a normal caller passes only ``source`` and ``session``; a test passes a
    stub ``embedding_service``/``vector_store`` to stay fast and offline.

    ``manage_transaction`` (default ``True``) controls the two-phase commit described in the module
    docstring. Set it to ``False`` when this call is a side effect *nested inside* a larger,
    already-open transaction the caller owns (this is exactly what
    :func:`app.ai.memory.indexer.record_decision` does) — every ``session.commit()``/
    ``session.rollback()`` below is skipped, so this function's own success or failure becomes part
    of the caller's transaction instead of ending it early. Concretely: without this, a decision
    memory write nested inside ``ClaimService.submit_claim()`` would call ``session.commit()``
    partway through that method, permanently committing the claim (and everything staged before it)
    even if a later step in ``submit_claim()`` subsequently failed and the caller rolled back —
    silently breaking that method's own "one whole transaction" promise. With
    ``manage_transaction=False``, this function's failures simply propagate as exceptions and rely
    on the *caller's* rollback to undo everything, including this call's own work.
    """
    s = settings or ai_settings
    meta = metadata or DocumentMetadataInput()

    fetch_started = time.monotonic()
    document = source.fetch()
    fetch_ms = int((time.monotonic() - fetch_started) * 1000)

    run_repo = KnowledgeIngestionRunRepository(session)
    document_repo = KnowledgeDocumentRepository(session)
    chunk_repo = KnowledgeChunkRepository(session)
    embedding_repo = KnowledgeEmbeddingRepository(session)

    tenant_id = document.tenant_id
    overall_started = time.monotonic()

    run = run_repo.start(
        tenant_id=tenant_id, source_type=document.source_type.value,
        source_uri=document.source_uri, file_name=document.file_name,
        actor_sub=actor_sub, request_id=request_id, correlation_id=correlation_id,
    )
    if manage_transaction:
        session.commit()
    else:
        session.flush()

    tracker = RunTracker(run_repo, run)
    tracker.stage_timings[IngestionStage.FETCH.value] = fetch_ms

    try:
        with tracker.stage(IngestionStage.DEDUPLICATE):
            checksum = checksum_for(document)
            existing = document_repo.find_by_checksum(checksum, tenant_id=tenant_id)

        if existing is not None:
            duration_ms = int((time.monotonic() - overall_started) * 1000)
            finished = run_repo.finish(
                run, status=IngestionStatus.SKIPPED_DUPLICATE, document_id=existing.id,
                duration_ms=duration_ms, stage_timings=tracker.stage_timings,
            )
            if manage_transaction:
                session.commit()
            return IngestionResult(
                status=IngestionStatus.SKIPPED_DUPLICATE, run_id=finished.id,
                document_id=existing.id, document_version=existing.version,
                chunks_created=0, chunks_skipped=0, embeddings_created=0, duration_ms=duration_ms,
            )

        with tracker.stage(IngestionStage.PARSE):
            parser = resolve_parser(document)
            raw_text, raw_sections = parser.parse(document)

        with tracker.stage(IngestionStage.NORMALIZE):
            boilerplate = repeated_lines(raw_sections)
            cleaned_sections = clean_sections(raw_sections)
            cleaned_text = (
                "\n\n".join(section.text for section in cleaned_sections)
                if cleaned_sections else clean_text(raw_text, boilerplate=boilerplate)
            )

        with tracker.stage(IngestionStage.CLASSIFY):
            detected_type = classify(cleaned_text)
            language = detect_document_language(cleaned_sections)

        with tracker.stage(IngestionStage.PII_SCAN):
            findings = scan(cleaned_text) if s.PII_DETECTION_ENABLED else ()
            found_kinds = {f.kind.value for f in findings}
            forced_reject = found_kinds & s.pii_reject_kinds
            if forced_reject:
                raise PIIRejectionError(kinds=sorted(forced_reject))
            if findings and s.PII_REDACTION_MODE == RedactionMode.REJECT:
                raise PIIRejectionError(kinds=sorted(found_kinds))
            if findings and s.PII_REDACTION_MODE == RedactionMode.MASK:
                # `findings`' spans are offsets into the flat `cleaned_text`, not into any one
                # section's own text, so a section-based chunker (HEADING/HYBRID) would still see
                # the raw PII if only `cleaned_text` were redacted here. Redact sections first, each
                # against its own fresh scan, then re-derive `cleaned_text` from them — mirroring
                # exactly how NORMALIZE derives `cleaned_text` from `cleaned_sections` in the first
                # place.
                if cleaned_sections:
                    cleaned_sections = tuple(
                        replace(section, text=redact_text(section.text, scan(section.text)))
                        for section in cleaned_sections
                    )
                    cleaned_text = "\n\n".join(section.text for section in cleaned_sections)
                else:
                    cleaned_text = redact_text(cleaned_text, findings)

        with tracker.stage(IngestionStage.PERSIST_DOCUMENT):
            page_numbers = [
                sec.page_number for sec in cleaned_sections if sec.page_number is not None
            ]
            score = quality_score(cleaned_text, section_count=max(1, len(cleaned_sections)))
            if score < s.MIN_QUALITY_SCORE:
                logger.warning(
                    "ai.ingestion.low_quality",
                    extra={
                        "fileName": document.file_name, "score": score,
                        "minimum": s.MIN_QUALITY_SCORE,
                    },
                )
            parsed = ParsedDocument(
                text=cleaned_text, checksum=checksum, sections=cleaned_sections,
                page_count=max(page_numbers) if page_numbers else None,
                language=language, detected_type=detected_type, quality_score=score,
                pii_findings=findings, parser=parser.name,
            )

            profile = profile_for(document.source_type)
            title = meta.title or _default_title(document.file_name)
            if detected_type is not None and detected_type != document.source_type:
                logger.warning(
                    "ai.ingestion.source_type_mismatch",
                    extra={
                        "fileName": document.file_name, "declared": document.source_type.value,
                        "detected": detected_type.value,
                    },
                )

            previous = document_repo.latest_for_title(title, tenant_id=tenant_id)
            version = document_repo.next_version_for_title(title, tenant_id=tenant_id)
            document_row = document_repo.add(KnowledgeDocument(
                tenant_id=tenant_id, source_type=document.source_type.value, title=title,
                file_name=document.file_name, mime_type=document.mime_type,
                source_uri=document.source_uri, checksum_sha256=checksum,
                size_bytes=document.size_bytes, version=version,
                status=DocumentStatus.PROCESSING, language=parsed.language,
                page_count=parsed.page_count,
                quality_score=Decimal(str(round(parsed.quality_score, 2))),
                pii_kinds=list(parsed.pii_kinds), parser=parsed.parser,
                department=meta.department, country=meta.country, currency=meta.currency,
                category=meta.category or profile.default_category,
                policy_version=meta.policy_version, owner=meta.owner,
                effective_date=meta.effective_date, expiry_date=meta.expiry_date,
                doc_metadata={"detectedSourceType": detected_type.value} if detected_type else None,
                created_by_sub=actor_sub,
            ))
            if previous is not None:
                document_repo.supersede(previous=previous, replacement=document_row)

        chunk_metadata_template = ChunkMetadata(
            document_id=document_row.id, tenant_id=tenant_id,
            department=meta.department, country=meta.country, currency=meta.currency,
            category=document_row.category, policy_version=meta.policy_version, owner=meta.owner,
            effective_date=meta.effective_date, expiry_date=meta.expiry_date,
            language=parsed.language, source_type=document.source_type,
        )

        with tracker.stage(IngestionStage.CHUNK):
            strategy = chunk_strategy or profile.chunk_strategy
            config = build_chunking_config(
                s, strategy=strategy, **(chunk_overrides or profile.chunk_overrides)
            )
            chunker = resolve_chunker(strategy)
            chunks = chunker.chunk(parsed, config=config, metadata=chunk_metadata_template)
            chunk_repo.add_many(_chunk_to_row(c, tenant_id=tenant_id) for c in chunks)

        with tracker.stage(IngestionStage.EMBED):
            service = embedding_service or EmbeddingService(resolve_embedding_provider())
            embed_result = service.embed_documents([c.text for c in chunks])
            writes = [
                EmbeddingWrite(
                    chunk_id=c.id, document_id=document_row.id, vector=vector.values,
                    provider=service.provider_name, model_name=service.spec.model,
                    version=service.spec.version, spec_key=vector.spec_key,
                    dimensions=vector.dimensions, input_tokens=c.token_count,
                    created_by_sub=actor_sub,
                )
                for c, vector in zip(chunks, embed_result.vectors, strict=True)
            ]
            if writes:
                embedding_repo.upsert_many(writes, tenant_id=tenant_id)

        with tracker.stage(IngestionStage.INDEX):
            store = vector_store or resolve_vector_store(session=session)
            embedded = [
                EmbeddedChunk(chunk=c, vector=vector)
                for c, vector in zip(chunks, embed_result.vectors, strict=True)
            ]
            if embedded:
                store.upsert(embedded, tenant_id=tenant_id)
            document_repo.mark_indexed(document_row)

        duration_ms = int((time.monotonic() - overall_started) * 1000)
        # Not wrapped in `tracker.stage(IngestionStage.FINALIZE)`: that stage's only "work" is this
        # `finish()` call itself, which is what writes `stage_timings` to the row — a timing entry
        # for FINALIZE could only be added to the dict *after* this call already flushed it, so it
        # would never actually persist. `finish()` unconditionally sets `run.stage` to FINALIZE
        # itself; nothing here needs to additionally time it.
        finished = run_repo.finish(
            run, status=IngestionStatus.COMPLETED, document_id=document_row.id,
            chunks_created=len(chunks), chunks_skipped=0, embeddings_created=len(writes),
            embedding_spec_key=service.spec_key, duration_ms=duration_ms,
            stage_timings=tracker.stage_timings,
        )
        if manage_transaction:
            session.commit()
        return IngestionResult(
            status=IngestionStatus.COMPLETED, run_id=finished.id, document_id=document_row.id,
            document_version=document_row.version, chunks_created=len(chunks), chunks_skipped=0,
            embeddings_created=len(writes), duration_ms=duration_ms,
        )
    except Exception as exc:
        if not manage_transaction:
            # Nothing to do here: the caller owns the transaction (see this function's own
            # docstring) and is expected to have wrapped this call in its own savepoint/rollback
            # boundary — attempting our own commit/rollback would either be a no-op racing the
            # caller's or, worse, prematurely end a transaction the caller is still using.
            raise
        session.rollback()
        duration_ms = int((time.monotonic() - overall_started) * 1000)
        run_repo.finish(
            run, status=IngestionStatus.FAILED,
            error_message=f"[{tracker.current_stage.value}] {type(exc).__name__}: {exc}",
            duration_ms=duration_ms, stage_timings=tracker.stage_timings,
        )
        session.commit()
        raise


__all__ = ["DocumentMetadataInput", "IngestionResult", "ingest_document"]
