from typing import List
from fastapi import APIRouter
from app.services.store import audit_logs_store

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])

@router.get("", response_model=List[dict])
def get_audit_logs():
    return audit_logs_store
