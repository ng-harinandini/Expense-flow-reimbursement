"""ExpenseFlow AI Knowledge Platform.

This package is the **single intelligence layer** for the whole product. Nothing outside it may
query a vector database, call an embedding model, or invoke retrieval directly — every consumer
goes through :class:`app.ai.knowledge.service.KnowledgeService`. That rule is not a convention:
``tests/test_ai_architecture.py`` scans the import graph and fails the build if it is broken.

Layering (each layer may import only from the ones below it)::

    app/ai/api            HTTP adapters
    app/ai/services       composition roots wired into app/core/deps.py
    app/ai/knowledge      KnowledgeService facade + context builder  <-- the ONLY public surface
    app/ai/{retrieval, reranking, ingestion, memory,
            duplicate_detection, planner, tools, reasoning}
    app/ai/{chunking, parsing, embeddings, vector_store, prompts, governance}
    app/ai/providers      concrete vendor adapters (guarded imports)
    app/ai/{registry, telemetry, repositories, models}
    app/ai/interfaces     Protocols only — zero infrastructure imports
    app/ai/core           value types, errors, config, primitives

Two hard constraints hold everywhere in this package:

1. **The LLM is never the source of truth.** Deterministic engines own every claim decision. This
   platform retrieves, enriches, explains and recommends. `app/ai/**` must not import
   ``ClaimService`` or ``ClaimRepository``, and cannot write ``claims.status``.
2. **Every AI operation is reproducible.** Whatever is returned carries the embedding version,
   retrieval configuration and prompt version that produced it, so any past output can be
   re-derived from versioned artefacts.
"""

from __future__ import annotations

__all__ = ["__version__"]

# Bumped when the platform's public contracts change. Recorded alongside stored artefacts so a
# row can be traced to the code generation that wrote it.
__version__ = "1.0.0"
