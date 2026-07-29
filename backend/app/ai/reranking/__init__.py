"""Reranking (Task 7): cross-encoder re-scoring of an already-retrieved candidate set.

``RerankService`` is the framework — one resolved provider, graceful degradation, telemetry.
``HeuristicReranker`` is the always-available default. Cloud and local cross-encoder adapters live
in ``app.ai.providers.rerank`` (Task 4's provider pattern applied to reranking), registered lazily
so all of them are selectable on a host with none of their dependencies installed.
"""

from app.ai.reranking.heuristic import HeuristicReranker
from app.ai.reranking.service import RerankService

__all__ = ["HeuristicReranker", "RerankService"]
