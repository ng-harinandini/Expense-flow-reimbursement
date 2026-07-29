"""Protocols the whole AI platform is written against — the dependency-inversion boundary.

Nothing in this package may import infrastructure. The permitted imports are:

* the standard library (``typing``, ``dataclasses``, ``abc``, ``types``, ``uuid``, ``datetime``)
* :mod:`app.ai.core` (value types, enums — themselves infrastructure-free)
* :mod:`app.domain` (``Actor`` and the error hierarchy, which T003 already guarantees are free of
  FastAPI and session machinery)

Explicitly forbidden: ``sqlalchemy``, ``fastapi``, ``boto3``, ``onnxruntime``, ``httpx``,
``requests``, ``redis``, and any ``app.ai`` sibling package.
``tests/test_ai_architecture.py`` walks this package's import graph and fails the build on a
violation — the rule is enforced, not merely documented.

Why it matters: these protocols are what let the platform claim provider independence. The moment
an interface imports a concrete driver, every consumer of that interface inherits the dependency
and the claim becomes false.
"""

from __future__ import annotations

from app.ai.interfaces.cache import CacheBackend
from app.ai.interfaces.chunker import Chunker, ChunkingConfig
from app.ai.interfaces.embeddings import EmbeddingProvider, TokenCounter
from app.ai.interfaces.llm import LLMProvider
from app.ai.interfaces.parser import DocumentParser
from app.ai.interfaces.planner import (
    Memory,
    Plan,
    Planner,
    PlanStep,
    Reasoner,
    Retriever,
    Tool,
    ToolExecutor,
    ToolResult,
    ToolSpec,
    Validator,
)
from app.ai.interfaces.reranker import Reranker
from app.ai.interfaces.telemetry import Span, TelemetryRecorder
from app.ai.interfaces.vector_store import (
    VectorMatch,
    VectorStore,
    VectorStoreCapabilities,
)

__all__ = [
    "CacheBackend",
    "Chunker",
    "ChunkingConfig",
    "DocumentParser",
    "EmbeddingProvider",
    "LLMProvider",
    "Memory",
    "Plan",
    "PlanStep",
    "Planner",
    "Reasoner",
    "Reranker",
    "Retriever",
    "Span",
    "TelemetryRecorder",
    "TokenCounter",
    "Tool",
    "ToolExecutor",
    "ToolResult",
    "ToolSpec",
    "Validator",
    "VectorMatch",
    "VectorStore",
    "VectorStoreCapabilities",
]
