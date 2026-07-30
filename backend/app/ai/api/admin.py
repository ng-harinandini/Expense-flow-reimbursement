"""Prompt publishing and feature-flag overrides — admin-only governance actions.

Both underlying calls (``PromptRegistry.publish``, ``PersistedFeatureFlagStore.set``) already write
their own audit record (see their own modules) — this router does not audit a second time, unlike
the knowledge-document and duplicate-scan routes, which call a bare repository/pipeline function
that has no audit trail of its own.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.api.schemas import FlagOverrideRequestSchema, PromptPublishRequestSchema
from app.ai.governance.feature_flags import PersistedFeatureFlagStore
from app.ai.models.prompt import PromptTemplate
from app.ai.prompts.registry import PromptRegistry
from app.core.deps import (
    CurrentUser,
    get_feature_flag_store,
    get_prompt_registry,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor

router = APIRouter(prefix="/ai/admin", tags=["AI Knowledge Platform"])


def _serialize_prompt(prompt: PromptTemplate) -> dict:
    return {
        "code": prompt.code,
        "version": prompt.version,
        "name": prompt.name,
        "status": prompt.status.value if hasattr(prompt.status, "value") else str(prompt.status),
        "variables": prompt.variables,
    }


@router.post("/prompts/{code}/publish", status_code=201)
def publish_prompt(
    code: str,
    payload: PromptPublishRequestSchema,
    current: CurrentUser = Depends(require_roles("admin")),
    registry: PromptRegistry = Depends(get_prompt_registry),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    actor = Actor.from_current_user(current)
    prompt = registry.publish(
        code, name=payload.name, template_text=payload.templateText,
        variables=payload.variables, actor=actor, description=payload.description,
    )
    uow.commit()
    return _serialize_prompt(prompt)


@router.post("/flags/{flag_name}")
def override_flag(
    flag_name: str,
    payload: FlagOverrideRequestSchema,
    current: CurrentUser = Depends(require_roles("admin")),
    flag_store: PersistedFeatureFlagStore = Depends(get_feature_flag_store),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    actor = Actor.from_current_user(current)
    flag_store.set(flag_name, payload.enabled, actor=actor)
    uow.commit()
    return {"flag": flag_name, "enabled": payload.enabled}
