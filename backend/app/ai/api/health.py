"""Platform health and observability.

``/health`` is public (no bearer token) — the same reasoning as ``app/api/health.py``'s own liveness
route: an orchestrator's health check must not require a Cognito token. ``/metrics`` requires
admin/auditor per the M13 RBAC matrix, since it exposes active feature-flag state and duplicate
thresholds an ordinary caller has no need to see.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.duplicate_detection.service import DuplicateDetectionService
from app.ai.knowledge.service import KnowledgeService
from app.ai.registry.flags import feature_flags
from app.core.deps import (
    CurrentUser,
    get_duplicate_detection_service,
    get_knowledge_service,
    require_roles,
)

router = APIRouter(prefix="/ai/knowledge", tags=["AI Knowledge Platform"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "featureFlags": feature_flags.snapshot()}


@router.get("/metrics")
def metrics(
    current: CurrentUser = Depends(require_roles("admin", "auditor")),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
    duplicate_detection: DuplicateDetectionService = Depends(get_duplicate_detection_service),
) -> dict:
    return {
        "knowledge": knowledge_service.describe(),
        "duplicateDetection": duplicate_detection.describe(),
    }
