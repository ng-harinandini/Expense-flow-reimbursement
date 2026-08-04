"""pgvector adapter — the platform default.

Similarity is a filtered, indexed ``ORDER BY embedding <=> :query`` inside PostgreSQL. That single
sentence is the reason this is the default: the vectors live in the same database and the same
transaction as the chunks, documents, claims and audit log, so an ingestion either indexes
a document completely or not at all, and no retrieval reads a vector whose chunk was rolled back.
No second datastore to keep consistent, back up, secure, or explain to an auditor.

Approximate, and it says so. The HNSW index created by migration ``0004`` makes the result set
approximate, which the capability matrix declares (``exact_search=False``) so a recall regression is
attributable, not mysterious. ``postgres_native`` reads the same rows exactly and is the ground
truth to measure against.

Three query-shape decisions worth stating, because each one is a way this goes silently wrong:

* **The query vector is a bound parameter cast to ``vector(n)``**, never interpolated. A bare bound
  array reaches PostgreSQL untyped and ``<=>`` cannot resolve an operator for it; string
  interpolation would work and would also put caller-derived floats into SQL text.
* **``ORDER BY`` is the distance expression, with the chunk id as a tie-break.** Ties are not
  hypothetical — duplicated boilerplate across policy versions produces identical vectors —
  and without the second key, ``LIMIT``/``OFFSET`` paging can repeat or skip a row.
* **The score threshold is pushed into ``WHERE`` as a distance bound**, not applied to the returned
  rows. Filtering after ``LIMIT`` would return fewer than ``top_k`` results while reporting nothing
  about why.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import Select, asc, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import AIValidationError, ProviderError
from app.ai.core.types import EmbeddingVector, MetadataFilter
from app.ai.interfaces.vector_store import VectorMatch
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeEmbedding
from app.ai.vector_store import capabilities as caps
from app.ai.vector_store.postgres import PostgresVectorStoreBase, require_pgvector_extension
from app.ai.vector_store.pg_types import as_vector_param
from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_NAME = "pgvector"

# The distance above which a candidate cannot reach a given normalized score. Inverts
# ``score_from_distance`` per metric so the threshold prunes in SQL instead of filtering rows the
# database already paid to rank.
_UNBOUNDED = float("inf")

# pgvector's own ceiling on ``hnsw.ef_search`` — a value above this is rejected by PostgreSQL, not
# clamped. ``MAX_TOP_K + MAX_OFFSET`` (500 + 10,000) exceeds it, so a page cannot simply ask for
# more candidates than this to cover its depth; see ``_apply_ef_search``.
_PGVECTOR_MAX_EF_SEARCH = 1000


class PgVectorStore(PostgresVectorStoreBase):
    """Indexed approximate nearest-neighbour search inside PostgreSQL."""

    def __init__(
        self,
        *,
        dimensions: int,
        metric: DistanceMetric = DistanceMetric.COSINE,
        session: Optional[Session] = None,
        session_provider: Any = None,
        ef_search: int = 100,
        recorder: Any = None,
    ) -> None:
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.PGVECTOR,
            metric=metric,
            session=session,
            session_provider=session_provider,
            recorder=recorder,
        )
        if ef_search < 1:
            raise AIValidationError(f"ef_search must be >= 1, received {ef_search}.")
        self._ef_search = ef_search

        if metric is not DistanceMetric.COSINE:
            # The index in migration 0004 is built with ``vector_cosine_ops``, which serves only the
            # ``<=>`` operator. Any other metric still returns correct results — by sequential scan.
            # Warned rather than refused: exact search on a small corpus is a legitimate choice, and
            # an unusable configuration is worse than a slow one.
            logger.warning(
                "ai.vector_store.metric_without_index",
                extra={
                    "store": STORE_NAME,
                    "metric": metric.value,
                    "detail": (
                        "The HNSW index uses vector_cosine_ops and will not be used for this "
                        "metric; queries fall back to a sequential scan."
                    ),
                },
            )

    def _clone(self) -> "PgVectorStore":
        return PgVectorStore(
            dimensions=self.dimensions,
            metric=self.metric,
            session_provider=self._session_provider,
            ef_search=self._ef_search,
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
            self._apply_ef_search(session, top_k=top_k, offset=offset)

            distance = self._distance_expression(vector)
            labelled = distance.label("distance")
            stmt: Select = self._candidate_query(
                spec_key=vector.spec_key, tenant_id=tenant_id, filters=normalized
            ).add_columns(labelled)

            bound = self._distance_bound(score_threshold)
            if bound != _UNBOUNDED:
                stmt = stmt.where(distance <= bound)

            # Ordering by the label, so the distance is computed once per row rather than a second
            # time inside ORDER BY.
            stmt = stmt.order_by(asc(labelled), asc(KnowledgeChunk.id))
            stmt = stmt.limit(top_k).offset(offset)

            try:
                rows = session.execute(stmt).all()
            except SQLAlchemyError as exc:
                raise ProviderError(self.name, f"vector search failed: {exc}") from exc

            matches = [self._match(row[0], float(row[2])) for row in rows]
            if span is not None:
                span.set_counts(candidates_in=len(rows), candidates_out=len(matches))
            return matches

    def _distance_expression(self, vector: EmbeddingVector):
        """The pgvector operator for the configured metric, against a typed bound parameter."""
        query = as_vector_param(vector.values, self.dimensions)
        column = KnowledgeEmbedding.embedding
        if self.metric is DistanceMetric.EUCLIDEAN:
            return column.l2_distance(query)
        if self.metric is DistanceMetric.INNER_PRODUCT:
            return column.negative_inner_product(query)
        return column.cosine_distance(query)

    def _distance_bound(self, score_threshold: float) -> float:
        """The largest distance that can still reach ``score_threshold``.

        The inverse of :func:`~app.ai.vector_store.base.score_from_distance`, so a threshold becomes
        a ``WHERE`` clause the index can prune with rather than a post-filter that quietly shortens
        the page. A threshold of 0 admits everything, including the anti-correlated tail, which is
        what "no threshold" has to mean.
        """
        if score_threshold <= 0.0:
            return _UNBOUNDED
        if self.metric is DistanceMetric.COSINE:
            return 1.0 - score_threshold
        if self.metric is DistanceMetric.INNER_PRODUCT:
            return -score_threshold
        # Euclidean: score = 1 - d^2/2  =>  d = sqrt(2 * (1 - score)).
        return (2.0 * (1.0 - score_threshold)) ** 0.5

    def _apply_ef_search(self, session: Session, *, top_k: int, offset: int) -> None:
        """Set ``hnsw.ef_search`` for this transaction.

        The recall dial, and the one HNSW parameter that has to be set per query rather than per
        index. It must exceed the number of rows being asked for or the index cannot supply them:
        ``ef_search`` below ``top_k + offset`` returns short pages that look like a corpus with
        nothing in it. So the configured value is a floor, raised when a caller pages deep — but
        only up to pgvector's own ceiling of 1000. ``MAX_TOP_K + MAX_OFFSET`` (500 + 10,000) is
        reachable and exceeds that ceiling, and ``SET LOCAL`` to a value above it is an error that
        would otherwise discard the tuning entirely (see the savepoint note below); clamping and
        warning gets a deep, wide page reduced recall instead of no tuning at all.

        ``SET LOCAL`` — scoped to the surrounding transaction, so this never leaks into another
        request that happens to reuse the pooled connection.

        Wrapped in a savepoint because ``SET LOCAL`` on a parameter PostgreSQL does not recognize is
        an error, and an error aborts the *whole* transaction: catching the exception without a
        savepoint to roll back to would leave the search that follows failing with "current
        transaction is aborted", turning a missing tuning knob into an outage.
        """
        wanted = max(self._ef_search, top_k + offset)
        if wanted > _PGVECTOR_MAX_EF_SEARCH:
            logger.warning(
                "ai.vector_store.ef_search_clamped",
                extra={
                    "store": self.name, "wanted": wanted, "ceiling": _PGVECTOR_MAX_EF_SEARCH,
                    "detail": "top_k + offset exceeds pgvector's ef_search ceiling; this page may "
                    "have reduced recall.",
                },
            )
            wanted = _PGVECTOR_MAX_EF_SEARCH
        try:
            with session.begin_nested():
                session.execute(text(f"SET LOCAL hnsw.ef_search = {int(wanted)}"))
        except SQLAlchemyError as exc:
            # The parameter exists only when pgvector is loaded. Without it the query still returns
            # correct results by sequential scan, so this is a tuning failure, not a query failure.
            logger.warning(
                "ai.vector_store.ef_search_unset",
                extra={"store": self.name, "error": f"{type(exc).__name__}: {exc}"},
            )

    # --- readiness -----------------------------------------------------------

    def verify_extension(self) -> None:
        """Confirm the ``vector`` extension is present, with a remedy in the error if it is not.

        Separate from ``ensure_ready`` (a no-op because Alembic owns the schema) and from
        ``is_available`` (which must not raise). Called by the startup check and the governance
        endpoint, where a precise, actionable failure is what is wanted.
        """
        require_pgvector_extension(self._require_session())


__all__ = ["STORE_NAME", "PgVectorStore"]
