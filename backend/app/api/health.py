from datetime import datetime
from fastapi import APIRouter

router = APIRouter(tags=["Health"])

@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "framework": "FastAPI (Python 3.10)",
        "service": "ExpenseFlow Enterprise Backend",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
