"""Exact search over the same PostgreSQL rows, with no pgvector operator or index.

Filters and candidate selection run in SQL; the vectors come back as text and are scored in Python.
That is slower than :mod:`app.ai.vector_store.pgvector` and it is exact, which is what it is for:

1. **Ground truth for recall.** An HNSW index returns approximate results. The only way to know
   how approximate is to compare them against the exact answer over the same rows, and this store
   *is* that answer. Without it, "retrieval quality dropped" and "the index is under-tuned" are
   indistinguishable.
2. **A working store when the index is not.** pgvector added HNSW in 0.5.0; earlier ones have only
   IVFFlat, and an index can be absent while being rebuilt. This store never touches an index
   or an operator, so it keeps answering.
3. **Small corpora.** Below a few thousand chunks a tenant, an exact scan is competitive and has no
   recall question attached to it at all.

**What it does not do:** remove the pgvector dependency. That was its original purpose — the T004
baseline found no extension available on the target server — superseded when the owner chose
to install pgvector (decision D1). Migration ``0004`` declares ``knowledge_embeddings.embedding``
as ``vector(1024)``, so the extension is required for the table to exist, and no adapter reading
that table can be extension-free. What this adapter avoids is the *operator class and index*, which
is a real and useful property, but it is a narrower claim and the docstring says so rather
than letting the file name imply the old one.

Scoring in Python is bounded deliberately: the candidate set is capped, and a query whose filters
select more than the cap is refused rather than quietly ranked. Truncating the candidate set
would produce a result that looks like a complete answer and is not — the exact failure mode this
adapter exists to detect in the other one.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import AIValidationError, ProviderError
from app.ai.core.types import EmbeddingVector, MetadataFilter
from app.ai.embeddings.math import cosine_distance, dot, euclidean_distance
from app.ai.interfaces.vector_store import VectorMatch
from app.ai.vector_store import capabilities as caps
from app.ai.vector_store.postgres import PostgresVectorStoreBase
from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_NAME = "postgres_native"

# The most vectors this store will pull into Python for one query. 50k 1024-float vectors is roughly
# 400 MB of Python floats, so the ceiling is memory, not patience. A tenant whose filtered corpus
# exceeds it is a tenant that needs the indexed store.
DEFAULT_MAX_CANDIDATES = 20_000


class PostgresNativeVectorStore(PostgresVectorStoreBase):
    """Brute-force exact scoring over SQL-filtered candidates."""

    def __init__(
        self,
        *,
        dimensions: int,
        metric: DistanceMetric = DistanceMetric.COSINE,
        session: Optional[Session] = None,
        session_provider: Any = None,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        recorder: Any = None,
    ) -> None:
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.POSTGRES_NATIVE,
            metric=metric,
            session=session,
            session_provider=session_provider,
            recorder=recorder,
        )
        if max_candidates < 1:
            raise AIValidationError(
                f"max_candidates must be >= 1, received {max_candidates}."
            )
        self._max_candidates = max_candidates

    def _clone(self) -> "PostgresNativeVectorStore":
        return PostgresNativeVectorStore(
            dimensions=self.dimensions,
            metric=self.metric,
            session_provider=self._session_provider,
            max_candidates=self._max_candidates,
            recorder=self._recorder,
        )

    # --- search --------------------------------------------------------------

    def search(
        self,
        vector: EmbeddingVector,
        *,
        top_k: int = 10,
        tenant_id: str = "default",
        filters: Sequence[MetadataFilter] = (),
        score_threshold: float = 0.0,
        offset: int = 0,
    ) -> list[VectorMatch]:
        self._validate_query(
            vector, top_k=top_k, offset=offset,
            score_threshold=score_threshold, tenant_id=tenant_id,
        )
        normalized = self._normalize_filters(filters)
        session = self._require_session()

        with self._span(TelemetryOperation.VECTOR_SEARCH, topK=top_k) as span:
            self._guard_candidate_count(
                session, spec_key=vector.spec_key, tenant_id=tenant_id, filters=normalized
            )
            stmt = self._candidate_query(
                spec_key=vector.spec_key, tenant_id=tenant_id, filters=normalized
            )
            try:
                rows = session.execute(stmt).all()
            except SQLAlchemyError as exc:
                raise ProviderError(self.name, f"candidate selection failed: {exc}") from exc

            matches: list[VectorMatch] = []
            for chunk_row, embedding_row in rows:
                stored = embedding_row.embedding
                if not stored or len(stored) != self.dimensions:
                    # A row of the wrong width cannot be compared. Skipped and logged rather than
                    # raised: one damaged row must not take out every query that filters over it.
                    logger.warning(
                        "ai.vector_store.bad_row_width",
                        extra={
                            "store": self.name,
                            "chunkId": str(chunk_row.id),
                            "expected": self.dimensions,
                            "actual": len(stored or ()),
                        },
                    )
                    continue
                match = self._match(chunk_row, self._distance(vector.values, stored))
                if match.score >= score_threshold:
                    matches.append(match)

            # Same ordering rule as every other adapter, applied here in Python: descending score,
            # ties broken on the id so paging is stable.
            matches.sort(key=lambda m: (-m.score, str(m.chunk_id)))
            page = matches[offset: offset + top_k]
            if span is not None:
                span.set_counts(candidates_in=len(rows), candidates_out=len(page))
            return page

    def _guard_candidate_count(
        self, session: Session, *, spec_key: str, tenant_id: str, filters: Sequence[Any]
    ) -> None:
        """Refuse a query whose candidate set will not fit in memory.

        Counted first rather than truncated: a truncated exact search is no longer exact,
        and it would return a plausible page while silently ignoring most of the corpus. Refusing
        names the remedy — use the indexed store — instead of degrading into the thing this adapter
        exists to check.
        """
        # Counted over the candidate query as a subquery rather than by swapping its column list:
        # ``with_only_columns`` recomputes the FROM clause from the new columns, dropping the
        # join to ``knowledge_chunks`` and with it every filter that addresses a chunk column.
        inner = self._candidate_query(
            spec_key=spec_key, tenant_id=tenant_id, filters=filters
        ).subquery()
        try:
            total = int(session.execute(select(func.count()).select_from(inner)).scalar_one() or 0)
        except SQLAlchemyError as exc:
            raise ProviderError(self.name, f"candidate count failed: {exc}") from exc
        if total > self._max_candidates:
            raise AIValidationError(
                f"The '{self.name}' store scores candidates in Python and this query selects "
                f"{total} of them, above its limit of {self._max_candidates}. Narrow the filters, "
                "or use AI_VECTOR_STORE=pgvector, which ranks inside the database.",
                details={
                    "store": self.name, "candidates": total, "limit": self._max_candidates,
                },
            )

    def _distance(self, query: Sequence[float], candidate: Sequence[float]) -> float:
        """The configured metric's distance, in the convention the pgvector operators use.

        Matching the operator conventions exactly — including the *negated* inner product — is what
        makes the two PostgreSQL stores comparable: the same pair of vectors must produce the same
        number here as ``<=>``, ``<->`` or ``<#>`` produce in the database, or a recall comparison
        between them measures the difference in arithmetic rather than the difference in the index.
        """
        if self.metric is DistanceMetric.EUCLIDEAN:
            return euclidean_distance(query, candidate)
        if self.metric is DistanceMetric.INNER_PRODUCT:
            return -dot(query, candidate)
        return cosine_distance(query, candidate)

    # --- recall measurement --------------------------------------------------

    def recall_against(
        self,
        approximate: Sequence[VectorMatch],
        vector: EmbeddingVector,
        *,
        top_k: int,
        tenant_id: str = "default",
        filters: Sequence[MetadataFilter] = (),
    ) -> float:
        """Fraction of the exact top-``top_k`` that an approximate result set also found.

        The measurement this adapter exists for, as a method rather than a note in a runbook. HNSW
        recall varies with ``m``, ``ef_construction``, ``ef_search`` and data, so it is not a number
        anyone can derive — it has to be measured against the corpus in question, and it changes as
        that corpus grows.
        """
        exact = self.search(vector, top_k=top_k, tenant_id=tenant_id, filters=filters)
        if not exact:
            # Nothing to find: reporting 1.0 rather than 0.0, because an approximate store that also
            # found nothing agreed completely.
            return 1.0
        found = {m.chunk_id for m in approximate}
        return len([m for m in exact if m.chunk_id in found]) / len(exact)


__all__ = ["DEFAULT_MAX_CANDIDATES", "STORE_NAME", "PostgresNativeVectorStore"]
