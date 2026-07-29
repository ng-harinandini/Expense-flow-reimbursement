from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check():
    """Liveness: process is up. Does not touch the database."""
    return {
        "status": "ok",
        "framework": "FastAPI (Python 3.10)",
        "service": "ExpenseFlow Enterprise Backend",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/ready")
def readiness_check():
    """Readiness: verifies PostgreSQL connectivity. 503 when the DB is unreachable/unconfigured."""
    if not settings.database_configured:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "unconfigured",
                "detail": "DATABASE_URL / DB_* not set.",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    # Import lazily so a missing DB driver never breaks liveness or app import.
    from app.core.database import get_engine

    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "database": "reachable",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "unreachable",
                "detail": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
