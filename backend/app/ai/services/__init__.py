"""Composition roots: where a request's session and the platform's registries meet.

``app/core/deps.py`` calls into here to build request-scoped AI services — this package is where
provider/store/reranker resolution actually happens, kept separate from ``app/core/deps.py`` itself
so the FastAPI-specific wiring stays thin.
"""

from __future__ import annotations

from app.ai.services.composition import build_knowledge_service

__all__ = ["build_knowledge_service"]
