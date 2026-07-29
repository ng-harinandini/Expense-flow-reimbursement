"""Hybrid retrieval (Task 7): lexical + dense + fusion + reranking + compression + token budget.

``HybridRetrievalEngine`` is the facade; everything else in this package is a stage it composes.
Nothing outside ``app/ai`` may import this package directly — ``app/api``, ``app/services``,
``app/repositories`` and ``app/domain`` must go through ``app.ai.knowledge.KnowledgeService`` (M9),
enforced by ``tests/test_ai_architecture.py``. Within ``app/ai``, M9's context builder is the
intended caller.

Layout:

* ``engine`` — ``HybridRetrievalEngine``, the composition root for one retrieval call. Imported from
  ``app.ai.retrieval.engine`` directly (see below), not re-exported here.
* ``lexical`` / ``dense`` — the two legs. Each returns candidates carrying only its own stage score.
* ``fusion`` — Reciprocal Rank Fusion, for combining the two legs. Not used for a single-leg query.
* ``scoring`` — the final score precedence (rerank > fused > single leg) and rank assignment.
* ``filters`` — document-level scoping (supersession, effective-dating) the vector-store DSL
  deliberately excludes; see that module's own docstring for why.
* ``compression`` — opt-in extractive sentence trimming.
* ``budget`` — the token-budget packer; the one stage that always runs.

**``engine`` is deliberately not imported here.** ``engine.py`` imports several of this package's
own siblings (``budget``, ``compression``, ``fusion``, ``scoring``) *through the package* — the
same shallow-``__init__`` discipline ``app/ai/vector_store`` established for the identical reason:
if this file also imported ``engine``, importing any sibling through the package would re-enter
``engine.py`` while it is still mid-import and fail with a circular-import error. Get
``HybridRetrievalEngine`` from ``app.ai.retrieval.engine`` directly.
"""

from app.ai.retrieval.budget import pack
from app.ai.retrieval.compression import compress_chunks
from app.ai.retrieval.dense import dense_leg
from app.ai.retrieval.filters import DocumentScope, build_document_scope, combined_chunk_filters
from app.ai.retrieval.fusion import DEFAULT_RRF_K, fuse
from app.ai.retrieval.lexical import lexical_leg
from app.ai.retrieval.scoring import finalize

__all__ = [
    "DEFAULT_RRF_K",
    "DocumentScope",
    "build_document_scope",
    "combined_chunk_filters",
    "compress_chunks",
    "dense_leg",
    "finalize",
    "fuse",
    "lexical_leg",
    "pack",
]
