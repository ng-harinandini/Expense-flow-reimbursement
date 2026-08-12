"""FastAPI application factory and wiring.

Startup order matters: logging is configured before anything else so import-time and boot messages
are already structured JSON; the request-context middleware is added before the routers so every
request has correlation ids available to the handlers; and the exception handlers are registered so
no route needs to translate a domain error into a status code itself.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.api import admin as ai_admin_api
from app.ai.api import duplicates as ai_duplicates_api
from app.ai.api import health as ai_health_api
from app.ai.api import knowledge as ai_knowledge_api
from app.ai.api import rule_extraction as ai_rule_extraction_api
from app.ai.api import search as ai_search_api
from app.api import (
    admin_users,
    ai,
    audit_logs,
    auth,
    aws,
    categories,
    claims,
    expense_items,
    health,
    policy_rules,
    roles,
)
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware

configure_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise Expense Reimbursement & AI Fraud Detection Platform API"
)

# Correlation ids + access logging. Added before CORS so it wraps the whole stack.
app.add_middleware(RequestContextMiddleware)

# CORS: origins come from the comma-separated FE_URL env var. `allow_credentials=True`
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Domain/infrastructure errors -> HTTP status codes, in one place.
register_exception_handlers(app)

# Register API routers under /api prefix
app.include_router(claims.router, prefix=settings.API_PREFIX)
app.include_router(policy_rules.router, prefix=settings.API_PREFIX)
app.include_router(categories.router, prefix=settings.API_PREFIX)
app.include_router(audit_logs.router, prefix=settings.API_PREFIX)
app.include_router(ai.router, prefix=settings.API_PREFIX)
app.include_router(aws.router, prefix=settings.API_PREFIX)
app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(expense_items.router, prefix=settings.API_PREFIX)
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(admin_users.router, prefix=settings.API_PREFIX)
app.include_router(ai_knowledge_api.router, prefix=settings.API_PREFIX)
app.include_router(ai_rule_extraction_api.router, prefix=settings.API_PREFIX)
app.include_router(ai_search_api.router, prefix=settings.API_PREFIX)
app.include_router(ai_duplicates_api.router, prefix=settings.API_PREFIX)
app.include_router(ai_admin_api.router, prefix=settings.API_PREFIX)
app.include_router(ai_health_api.router, prefix=settings.API_PREFIX)
app.include_router(roles.router, prefix=settings.API_PREFIX)


logger.info(
    "app.initialized",
    extra={
        "version": settings.VERSION,
        "databaseConfigured": settings.database_configured,
        "cognitoConfigured": settings.cognito_configured,
    },
)


@app.get("/")
def root():
    return {
        "message": "Welcome to ExpenseFlow FastAPI Backend Service",
        "docs": "/docs",
        "health": "/api/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
