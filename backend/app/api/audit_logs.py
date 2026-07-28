from typing import List
from fastapi import APIRouter, Depends
from app.services.store import audit_logs_store
from app.core.deps import require_roles

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])

@router.get("", response_model=List[dict],
            dependencies=[Depends(require_roles("finance", "admin", "auditor"))])
def get_audit_logs():
    return audit_logs_store
