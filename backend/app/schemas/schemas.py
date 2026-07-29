from typing import Annotated, Any, Dict, List, Optional, Union
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class EmployeeSchema(BaseModel):
    id: str
    name: str
    grade: str
    department: str
    email: str
    managerName: str

class WorkflowStepSchema(BaseModel):
    timestamp: str
    actorName: str
    actorRole: str
    stepName: str
    action: str
    status: str
    notes: Optional[str] = ""
    traceId: Optional[str] = None

class CommentSchema(BaseModel):
    id: str
    authorName: str
    authorRole: str
    timestamp: str
    text: str

class ReceiptLineItemSchema(BaseModel):
    description: str
    amount: float
    category: Optional[str] = "General"

class ReceiptDataSchema(BaseModel):
    fileName: Optional[str] = "receipt.png"
    vendorName: str
    transactionDate: str
    totalAmount: float
    currency: Optional[str] = "USD"
    hasAlcohol: Optional[bool] = False
    alcoholItemCount: Optional[int] = 0
    attendeesNoted: Optional[List[str]] = []
    lineItems: Optional[List[ReceiptLineItemSchema]] = []
    confidenceScore: Optional[float] = 0.95

class PolicyCheckResultSchema(BaseModel):
    ruleId: str
    ruleName: str
    category: str
    passed: bool
    severity: str
    message: str
    details: Optional[str] = None

class PolicyValidationReportSchema(BaseModel):
    overallPassed: bool
    requiresManualReview: bool
    isWithinMaxLimit: bool
    isWithinAutoApproveLimit: bool
    maxLimitAllowed: float
    autoApproveLimit: float
    receiptRequired: bool
    receiptProvided: bool
    daysSinceExpense: int
    requiresDirectorApprovalForAge: bool
    checks: List[PolicyCheckResultSchema]
    reasoningSummary: str

class AnomalyFlagSchema(BaseModel):
    code: str
    title: str
    severity: str
    description: str
    evidence: str

class FraudScreeningReportSchema(BaseModel):
    riskScore: int
    isFlagged: bool
    riskLevel: str
    flags: List[AnomalyFlagSchema]
    rationale: str
    recommendedAction: str

class ExpenseClaimSchema(BaseModel):
    id: str
    claimNumber: str
    employeeId: str
    employeeName: str
    employeeGrade: str
    department: str
    expenseDate: str
    submissionDate: str
    category: str
    subCategory: Optional[str] = "General Expense"
    amount: float
    currency: Optional[str] = "USD"
    amountUSD: float
    merchantVendor: str
    purposeDescription: str
    attendees: Optional[str] = None
    tripLog: Optional[str] = None
    hasPreApproval: Optional[bool] = False
    preApprovalDocRef: Optional[str] = None
    receiptAttached: Optional[bool] = True
    receiptUrl: Optional[str] = None
    extractedReceipt: Optional[ReceiptDataSchema] = None
    policyValidation: Optional[PolicyValidationReportSchema] = None
    fraudScreening: Optional[FraudScreeningReportSchema] = None
    status: str
    workflowHistory: List[WorkflowStepSchema] = []
    comments: List[CommentSchema] = []

# Money on the wire. ``gt=0`` rejects zero and negative amounts before any query runs;
# ``max_digits``/``decimal_places`` mirror the NUMERIC(14,2) columns so a value that the database
# would reject is refused at the edge with a field-level message instead of a 500.
MoneyAmount = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]

# Grace for a client whose local date is a day ahead of the server's UTC date.
_FUTURE_DATE_GRACE_DAYS = 1


class ExpenseClaimCreateSchema(BaseModel):
    """Request body for ``POST /claims`` (create + submit).

    Shape validation only — anything needing the database (employee exists, duplicate, receipt
    already claimed) is enforced by ``app.domain.validators`` inside the service.

    ``employeeId`` is accepted for backwards compatibility but ignored: the owner is always bound
    to the authenticated identity so a caller cannot file a claim against someone else.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    employeeId: Optional[str] = Field(
        default=None, deprecated="Ignored — the claim owner is taken from the access token."
    )
    category: Optional[str] = Field(default="Misc / Other", max_length=64)
    subCategory: Optional[str] = Field(default="General Expense", max_length=120)
    amount: MoneyAmount
    currency: Optional[str] = Field(default="USD", min_length=3, max_length=3)
    amountUSD: Optional[MoneyAmount] = None
    merchantVendor: str = Field(min_length=1, max_length=200)
    expenseDate: Optional[date] = None
    purposeDescription: Optional[str] = ""
    attendees: Optional[str] = Field(default=None, max_length=4000)
    tripLog: Optional[Union[str, Dict[str, Any]]] = None
    hasPreApproval: Optional[bool] = False
    preApprovalDocRef: Optional[str] = Field(default=None, max_length=200)
    receiptAttached: Optional[bool] = True
    receiptUrl: Optional[str] = None
    receiptId: Optional[str] = Field(
        default=None, description="Id of an uploaded receipt to attach (must be unclaimed)."
    )
    extractedReceipt: Optional[ReceiptDataSchema] = None
    fxRate: Optional[Decimal] = Field(default=None, gt=0)

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("must be a 3-letter ISO 4217 currency code")
        return code

    @field_validator("expenseDate")
    @classmethod
    def _not_future(cls, value: Optional[date]) -> Optional[date]:
        if value is not None and (value - date.today()).days > _FUTURE_DATE_GRACE_DAYS:
            raise ValueError("cannot be in the future")
        return value

    @model_validator(mode="after")
    def _default_amount_usd(self) -> "ExpenseClaimCreateSchema":
        """Single-currency claims need no conversion: USD total defaults to the amount."""
        if self.amountUSD is None:
            self.amountUSD = self.amount
        return self


class ExpenseClaimUpdateSchema(BaseModel):
    """Request body for ``PATCH /claims/{id}`` — editing a claim that is still a draft.

    Every field is optional; only the keys present are applied.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    category: Optional[str] = Field(default=None, max_length=64)
    subCategory: Optional[str] = Field(default=None, max_length=120)
    amount: Optional[MoneyAmount] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    amountUSD: Optional[MoneyAmount] = None
    merchantVendor: Optional[str] = Field(default=None, min_length=1, max_length=200)
    expenseDate: Optional[date] = None
    purposeDescription: Optional[str] = None
    attendees: Optional[str] = Field(default=None, max_length=4000)
    receiptUrl: Optional[str] = None
    preApprovalDocRef: Optional[str] = Field(default=None, max_length=200)

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("must be a 3-letter ISO 4217 currency code")
        return code

    @field_validator("expenseDate")
    @classmethod
    def _not_future(cls, value: Optional[date]) -> Optional[date]:
        if value is not None and (value - date.today()).days > _FUTURE_DATE_GRACE_DAYS:
            raise ValueError("cannot be in the future")
        return value


class ActionRequestSchema(BaseModel):
    """Request body for ``POST /claims/{id}/action``.

    ``actorName`` / ``actorRole`` are retained for compatibility but ignored — the actor is the
    authenticated caller, never a client-supplied string.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    action: str = Field(
        pattern="^(APPROVE|REJECT|DISBURSE|FLAG_FRAUD)$",
        description="APPROVE, REJECT, DISBURSE, or FLAG_FRAUD.",
    )
    actorName: Optional[str] = Field(default=None, deprecated="Ignored — taken from the token.")
    actorRole: Optional[str] = Field(default=None, deprecated="Ignored — taken from the token.")
    notes: Optional[str] = Field(default="", max_length=4000)
    expectedVersion: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Claim 'version' from the last read. When supplied, a concurrent modification is "
            "rejected with 409 instead of silently overwriting the other decision."
        ),
    )

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class AssignReviewerSchema(BaseModel):
    """Request body for ``POST /claims/{id}/assign``. ``null`` clears the assignment."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    reviewerId: Optional[str] = Field(
        default=None, description="Employee code of the reviewer (e.g. 'emp-101'); null to clear."
    )
    notes: Optional[str] = Field(default=None, max_length=4000)


class CommentCreateSchema(BaseModel):
    """Request body for ``POST /claims/{id}/comments``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4000)
    isInternal: bool = Field(
        default=False, description="Reviewer-only note; hidden from the claim owner."
    )


class ClaimStatusHistorySchema(BaseModel):
    """One entry of ``GET /claims/{id}/history``."""

    sequence: int
    fromStatus: Optional[str] = None
    toStatus: str
    stepName: str
    action: str
    outcome: str
    notes: Optional[str] = None
    actorName: Optional[str] = None
    actorRole: Optional[str] = None
    occurredAt: Optional[str] = None
    requestId: Optional[str] = None
    correlationId: Optional[str] = None

class LoginRequestSchema(BaseModel):
    email: str
    password: str


class RespondChallengeRequestSchema(BaseModel):
    email: str
    session: str                                # the Session returned by /auth/login
    newPassword: str                            # for NEW_PASSWORD_REQUIRED
    challenge: Optional[str] = "NEW_PASSWORD_REQUIRED"
    name: Optional[str] = None                  # satisfies a required 'name' attribute on first login
    userAttributes: Optional[Dict[str, str]] = None  # any other required attributes to set now


class AuthenticatedUserSchema(BaseModel):
    """Identity decoded from the Cognito ID token returned by login.

    Role comes exclusively from custom:role_id — Cognito Groups are not used for RBAC.
    """
    sub: Optional[str] = None           # canonical immutable identity
    email: Optional[str] = None
    role: Optional[str] = None          # from custom:role_id (one of the 5 app roles)
    employeeId: Optional[str] = None    # from custom:employeeId (optional ownership link)


class LoginResponseSchema(BaseModel):
    # Present on success:
    idToken: Optional[str] = None
    accessToken: Optional[str] = None
    refreshToken: Optional[str] = None
    expiresIn: Optional[int] = None
    tokenType: Optional[str] = "Bearer"
    user: Optional[AuthenticatedUserSchema] = None
    # Present when Cognito returns a challenge instead of tokens (e.g. NEW_PASSWORD_REQUIRED):
    challenge: Optional[str] = None
    session: Optional[str] = None


class AdminCreateUserSchema(BaseModel):
    email: str
    role: str                              # custom:role_id — must be one of the 5 app roles
    employeeId: Optional[str] = None       # custom:employeeId (optional ownership link)
    name: Optional[str] = None


class AdminUpdateUserSchema(BaseModel):
    employeeId: Optional[str] = None
    name: Optional[str] = None


class AdminChangeRoleSchema(BaseModel):
    role: str                              # new custom:role_id value (validated against the 5)


class AdminUserSummarySchema(BaseModel):
    username: Optional[str] = None         # Cognito username (immutable) — the sub-linked handle
    sub: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None             # custom:role_id
    employeeId: Optional[str] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None


class AdminUserListSchema(BaseModel):
    users: List[AdminUserSummarySchema] = []
    nextToken: Optional[str] = None


class PolicyRuleDefinitionSchema(BaseModel):
    """One entry of the policy ruleset, as ``GET``/``PUT /policy-rules`` exchange it.

    ``maxAmountUSD`` stays a number-or-prose union ("Per signed agreement"): the service stores a
    numeric value in ``expense_limit`` and prose in ``limit_expression``.

    The trailing fields are additive read-only metadata from the ``policy_rules`` table — a client
    may echo ``code`` back on ``PUT`` to update a specific rule; omitting it derives the code from
    the category, which is how the existing frontend payload keeps working.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    category: str = Field(min_length=1, max_length=64)
    maxAmountUSD: Optional[Union[Decimal, str]] = None
    autoApproveLimitUSD: Optional[Decimal] = Field(default=None, ge=0)
    receiptRequiredAboveUSD: Optional[Decimal] = Field(default=Decimal("0"), ge=0)
    requiresPreApproval: bool = False
    gradeTier: Optional[str] = Field(default="All Staff", max_length=64)
    specialRules: Optional[List[str]] = Field(default_factory=list)

    # Additive / optional durable-model fields.
    code: Optional[str] = Field(default=None, max_length=64)
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    currency: Optional[str] = Field(default="USD", min_length=3, max_length=3)
    priority: Optional[int] = Field(default=100, ge=0, le=10_000)
    conditions: Optional[Dict[str, Any]] = None
    actions: Optional[Dict[str, Any]] = None
    version: Optional[int] = None
    isActive: Optional[bool] = None
    effectiveDate: Optional[str] = None
    expirationDate: Optional[str] = None

class AuditLogEntrySchema(BaseModel):
    id: str
    timestamp: str
    actor: str
    role: str
    eventType: str
    targetId: str
    details: str
    ipAddress: Optional[str] = "127.0.0.1"

class OcrExtractRequestSchema(BaseModel):
    imageBase64: Optional[str] = None
    sampleReceiptText: Optional[str] = None
    fileName: Optional[str] = "receipt.png"

class PolicyReasoningRequestSchema(BaseModel):
    claim: dict
    policyRules: Optional[List[dict]] = None

class RefineIamRequestSchema(BaseModel):
    currentPolicyJson: Optional[str] = None
    environmentName: Optional[str] = "AWS Serverless Sandbox"
    useCaseDescription: Optional[str] = "AWS Step Functions + Lambda + Textract"


# --- Receipts domain (S3 + Textract + PostgreSQL) ---

class ReceiptFieldSchema(BaseModel):
    fieldType: Optional[str] = None
    fieldLabel: Optional[str] = None
    fieldValue: Optional[str] = None
    confidence: Optional[float] = None


class ReceiptLineItemDetailSchema(BaseModel):
    lineNumber: Optional[int] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unitPrice: Optional[float] = None
    amount: Optional[float] = None
    raw: Optional[dict] = None


class ReceiptS3LocationSchema(BaseModel):
    bucket: Optional[str] = None
    key: Optional[str] = None
    region: Optional[str] = None


class ReceiptSummarySchema(BaseModel):
    """Summary-level view returned by the list endpoint."""
    id: str
    fileName: str
    contentType: Optional[str] = None
    fileSizeBytes: Optional[int] = None
    employeeId: Optional[str] = None
    extractionStatus: str
    extractionSource: Optional[str] = None
    vendorName: Optional[str] = None
    transactionDate: Optional[str] = None
    totalAmount: Optional[float] = None
    currency: Optional[str] = None
    s3: Optional[ReceiptS3LocationSchema] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class ReceiptDetailSchema(ReceiptSummarySchema):
    """Full record including raw Textract JSON, fields and line items."""
    rawTextract: Optional[dict] = None
    normalizedExtraction: Optional[dict] = None
    errorMessage: Optional[str] = None
    fields: List[ReceiptFieldSchema] = []
    lineItems: List[ReceiptLineItemDetailSchema] = []


class ReceiptUploadResponseSchema(ReceiptDetailSchema):
    """201 response for POST /receipts/upload (same shape as detail)."""
    pass
