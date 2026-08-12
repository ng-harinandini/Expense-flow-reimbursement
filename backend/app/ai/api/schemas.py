"""Request/response schemas for the AI platform's HTTP surface (M13).

Follows the T003 convention exactly (``app/schemas/schemas.py``): plain camelCase field names (no
``alias=``), ``ConfigDict(extra="ignore", str_strip_whitespace=True)`` on every request body. Only
JSON request bodies get a schema here — multipart form fields (the document upload) use FastAPI's
own ``Form(...)`` parameters, matching ``app/api/receipts.py``'s ``upload_receipt``, and read-only
responses that just mirror a service's own ``dict`` (``describe()``, ``DuplicateReport.explain()``)
are returned as plain ``dict`` with ``response_model=dict``, matching ``app/api/policy_rules.py``.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.extraction.policy_rule_extractor import RULE_CATEGORIES


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


# --- rule extraction: candidate editing -----------------------------------------

class CandidateRuleUpdateRequestSchema(_RequestModel):
    """Partial update for a PENDING ``CandidatePolicyRule``.

    Every field is optional; only fields actually present in the request body are changed — the
    route reads that via ``model_dump(exclude_unset=True)``, so sending ``"currency": null``
    explicitly clears the field, while omitting ``currency`` entirely leaves it untouched.
    """

    name: Optional[str] = None
    category: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None
    gradeTier: Optional[str] = None
    expenseLimit: Optional[Decimal] = Field(default=None, ge=0)
    limitExpression: Optional[str] = None
    autoApproveLimitUSD: Optional[Decimal] = Field(default=None, ge=0)
    receiptRequiredAboveUSD: Optional[Decimal] = Field(default=None, ge=0)
    requiresPreApproval: Optional[bool] = None
    priority: Optional[int] = None
    specialRules: Optional[list[str]] = None
    conditions: Optional[dict] = None
    actions: Optional[dict] = None
    exclusions: Optional[list[str]] = None
    requiredDocuments: Optional[list[str]] = None
    sourceSection: Optional[str] = None
    sourceText: Optional[str] = None
    reviewNotes: Optional[str] = None

    @field_validator("category")
    @classmethod
    def _category_is_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in RULE_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(RULE_CATEGORIES)}")
        return v

    @field_validator("currency")
    @classmethod
    def _currency_is_iso4217_shaped(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        code = v.upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError("currency must be a 3-letter ISO 4217 code, e.g. 'USD'.")
        return code

    @field_validator("country")
    @classmethod
    def _country_is_alpha2_shaped(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        code = v.upper()
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError("country must be a 2-letter ISO 3166-1 alpha-2 code, e.g. 'US'.")
        return code


class CandidateRuleBulkUpdateItemSchema(CandidateRuleUpdateRequestSchema):
    """One entry in a bulk edit — same editable fields as a single PATCH, plus the target id.

    Extra keys (``status``, ``createdAt``, ``sourceChunkId``, ...) are silently ignored thanks to
    ``_RequestModel``'s ``extra="ignore"``, so a reviewer can paste back the *entire* object they
    got from ``GET .../candidate-rules`` — editing whichever fields they touched — without first
    stripping the read-only ones out.
    """

    id: uuid.UUID


class CandidateRuleBulkUpdateRequestSchema(_RequestModel):
    candidates: list[CandidateRuleBulkUpdateItemSchema] = Field(min_length=1)


__all__ = [
    "CandidateRuleBulkUpdateItemSchema",
    "CandidateRuleBulkUpdateRequestSchema",
    "CandidateRuleUpdateRequestSchema",
    "ContextRequestSchema",
    "DuplicateScanRequestSchema",
    "FlagOverrideRequestSchema",
    "PromptPublishRequestSchema",
    "SearchRequestSchema",
]
