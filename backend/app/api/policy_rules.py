from typing import List
from fastapi import APIRouter, Depends
from app.services.store import policy_rules_store, add_audit_log
from app.core.deps import get_current_user, require_roles

router = APIRouter(prefix="/policy-rules", tags=["Policy Rules"])

@router.get("", response_model=List[dict], dependencies=[Depends(get_current_user)])
def get_policy_rules():
    return policy_rules_store

@router.put("", dependencies=[Depends(require_roles("finance", "admin"))])
def update_policy_rules(rules: List[dict]):
    policy_rules_store.clear()
    policy_rules_store.extend(rules)

    add_audit_log(
        "Finance Admin",
        "admin",
        "POLICY_UPDATE",
        "policy_rules",
        f"Updated authoritative expense policy rules table ({len(rules)} rules active)."
    )

    return {"status": "updated", "count": len(policy_rules_store)}
