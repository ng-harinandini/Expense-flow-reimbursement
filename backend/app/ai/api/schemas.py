"""Request/response schemas for the AI platform's HTTP surface (M13).

Follows the T003 convention exactly (``app/schemas/schemas.py``): plain camelCase field names (no
``alias=``), ``ConfigDict(extra="ignore", str_strip_whitespace=True)`` on every request body. Only
JSON request bodies get a schema here — multipart form fields (the document upload) use FastAPI's
own ``Form(...)`` parameters, matching ``app/api/receipts.py``'s ``upload_receipt``, and read-only
responses that just mirror a service's own ``dict`` (``describe()``, ``DuplicateReport.explain()``)
are returned as plain ``dict`` with ``response_model=dict``, matching ``app/api/policy_rules.py``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --- knowledge: search / context ---------------------------------------------

class SearchRequestSchema(_RequestModel):
    text: str
    topK: int = Field(default=8, ge=1, le=50)
    strategy: Literal["LEXICAL", "DENSE", "HYBRID"] = "HYBRID"
    rerank: bool = True
    effectiveOn: Optional[date] = None


class ContextRequestSchema(_RequestModel):
    kind: Literal["policy", "similar_claims", "vendor"]
    query: str
    topK: int = Field(default=5, ge=1, le=50)
    category: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None
    effectiveOn: Optional[date] = None


# --- duplicates ----------------------------------------------------------------

class DuplicateScanRequestSchema(_RequestModel):
    claimId: uuid.UUID
    employeeId: uuid.UUID
    merchantVendor: str
    expenseDate: date
    amountUsd: Decimal
    currency: str
    invoiceNumber: Optional[str] = None


# --- admin: prompts / flags ------------------------------------------------------

class PromptPublishRequestSchema(_RequestModel):
    name: str
    templateText: str
    variables: list[str] = Field(default_factory=list)
    description: Optional[str] = None


class FlagOverrideRequestSchema(_RequestModel):
    enabled: bool


__all__ = [
    "ContextRequestSchema",
    "DuplicateScanRequestSchema",
    "FlagOverrideRequestSchema",
    "PromptPublishRequestSchema",
    "SearchRequestSchema",
]
