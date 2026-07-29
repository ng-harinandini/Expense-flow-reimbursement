"""Policy rules API — reads and publishes the durable ruleset.

Previously backed by a module-level Python list that ``PUT`` cleared and refilled; now backed by
the versioned, effective-dated ``policy_rules`` table. ``PUT`` therefore *publishes a new version*
of each submitted rule and retires the rules absent from the payload, instead of destroying the
previous ruleset — so a claim decided last month can still be explained by the rule text that was
live when it was submitted.

The response shape is unchanged (``PolicyRuleDefinition[]``); durable metadata (``code``,
``version``, ``effectiveDate``, …) is additive.
"""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends

from app.core.deps import (
    CurrentUser,
    get_current_user,
    get_policy_rule_service,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.schemas.schemas import PolicyRuleDefinitionSchema
from app.services.mappers import policy_rule_to_dict
from app.services.policy_rule_service import PolicyRuleService

router = APIRouter(prefix="/policy-rules", tags=["Policy Rules"])


@router.get("", response_model=List[dict], dependencies=[Depends(get_current_user)])
def get_policy_rules(
    service: PolicyRuleService = Depends(get_policy_rule_service),
) -> list[dict[str, Any]]:
    """The active ruleset, priority first."""
    return service.list_active_as_dicts()


@router.get("/{code}/versions", response_model=List[dict],
            dependencies=[Depends(require_roles("finance", "admin", "auditor"))])
def get_policy_rule_versions(
    code: str,
    service: PolicyRuleService = Depends(get_policy_rule_service),
) -> list[dict[str, Any]]:
    """Every version of one rule, newest first — the rule's change history."""
    return [policy_rule_to_dict(rule) for rule in service.get_versions(code)]


@router.put("", response_model=dict)
def update_policy_rules(
    rules: List[PolicyRuleDefinitionSchema],
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    service: PolicyRuleService = Depends(get_policy_rule_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict[str, Any]:
    """Publish a complete ruleset.

    Each submitted rule becomes a new version; active rules missing from the payload are retired.
    Nothing is deleted, and the change is audited with a before/after snapshot.
    """
    published = service.replace_ruleset(
        [rule.model_dump(exclude_none=True) for rule in rules],
        actor=Actor.from_current_user(current),
    )
    uow.commit()
    return {
        "status": "updated",
        "count": len(published),
        "rules": [policy_rule_to_dict(rule) for rule in published],
    }
