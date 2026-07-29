"""``RerankService`` — the framework around one resolved :class:`Reranker`.

Mirrors :class:`~app.ai.embeddings.service.EmbeddingService`'s split deliberately: business code
(the retrieval engine) talks to this service and never to a provider directly, so swapping the
reranker is a registry change, not a code change.

The service, not the engine, owns graceful degradation — matching the ``Reranker`` protocol's own
stated contract (clause 4: an unavailable provider raises, and *the caller* proceeds without
reranking). Putting that catch here rather than in every caller means a future second caller (a
duplicate-detection ranking pass, say) gets the same safety by construction rather than by every
call site remembering to wrap its own ``try/except``.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.ai.core.enums import TelemetryOperation
from app.ai.core.types import RetrievedChunk
from app.ai.interfaces.reranker import Reranker
from app.ai.telemetry.recorder import NullTelemetryRecorder
from app.core.logging import get_logger

logger = get_logger(__name__)


class RerankService:
    """Reranks a candidate set, never raising and never dropping a candidate on failure."""

    def __init__(self, *, provider: Reranker, recorder: Any = None) -> None:
        self._provider = provider
        self._recorder = recorder or NullTelemetryRecorder()

    @property
    def name(self) -> str:
        return self._provider.name

    def rerank(
        self,
        query_text: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_n: Optional[int] = None,
    ) -> list[RetrievedChunk]:
        """Reorder ``candidates``, or return them unchanged if reranking cannot happen.

        Never raises: a broken or unreachable reranker degrades precision to whatever ordering the
        caller already had, which is always better than failing a retrieval over an optional stage.

        ``top_n`` truncation is applied here, once, regardless of whether the underlying provider
        already honoured it — some (an HTTP-based one, to shrink its response) do; the in-process
        ones don't bother, since there is no payload to save. Either behaviour is contract-compliant
        (clause 1 makes truncation "the caller's decision"); this is the one place that decision
        actually gets made, so a caller gets the same truncated shape back regardless of provider.
        """
        if top_n is not None and top_n < 0:
            # Caught here rather than left to Python's own slicing: ``reranked[:-1]`` is a real,
            # silent behaviour — "everything except the last result" — not an error, so a negative
            # value would mis-slice instead of failing loudly.
            raise ValueError(f"top_n must be >= 0, received {top_n}.")
        if not candidates:
            return list(candidates)

        with self._recorder.span(
            TelemetryOperation.RERANK, attributes={"provider": self._provider.name}
        ) as span:
            try:
                if not self._provider.is_available():
                    span.set_attribute("skipped", "unavailable")
                    return list(candidates)
                reranked = self._provider.rerank(query_text, candidates, top_n=top_n)
            except Exception as exc:  # noqa: BLE001 - reranking is always optional, by contract
                span.fail(f"{type(exc).__name__}: {exc}")
                logger.warning(
                    "ai.rerank.failed",
                    extra={
                        "provider": self._provider.name, "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                return list(candidates)

            # A provider may return everything reordered, or (only when top_n was given) may
            # already have truncated to it server-side — see the docstring. Any other count is a
            # contract violation and is treated the same as a raise: the ordering cannot be trusted.
            valid_counts = {len(candidates)}
            if top_n is not None:
                valid_counts.add(min(top_n, len(candidates)))
            if len(reranked) not in valid_counts:
                span.fail(f"returned {len(reranked)} candidates for {len(candidates)} given")
                logger.warning(
                    "ai.rerank.count_mismatch",
                    extra={
                        "provider": self._provider.name,
                        "given": len(candidates), "returned": len(reranked), "topN": top_n,
                    },
                )
                return list(candidates)[:top_n] if top_n is not None else list(candidates)

            span.set_counts(candidates_in=len(candidates), candidates_out=len(reranked))
        return reranked[:top_n] if top_n is not None else reranked

    def is_available(self) -> bool:
        try:
            return bool(self._provider.is_available())
        except Exception:  # pragma: no cover - defensive; availability must never raise upward
            return False

    def describe(self) -> dict[str, object]:
        """Active configuration, for ``/metrics`` and API response metadata."""
        return {"provider": self._provider.name, "available": self.is_available()}


__all__ = ["RerankService"]
