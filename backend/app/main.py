from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import claims, policy_rules, audit_logs, ai, aws, health, receipts

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise Expense Reimbursement & AI Fraud Detection Platform API"
)

# CORS Middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers under /api prefix
app.include_router(claims.router, prefix=settings.API_PREFIX)
app.include_router(policy_rules.router, prefix=settings.API_PREFIX)
app.include_router(audit_logs.router, prefix=settings.API_PREFIX)
app.include_router(ai.router, prefix=settings.API_PREFIX)
app.include_router(aws.router, prefix=settings.API_PREFIX)
app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(receipts.router, prefix=settings.API_PREFIX)

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
