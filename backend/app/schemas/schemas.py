from typing import Annotated, Any, Dict, List, Optional, Union
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ExpenseItemCreateSchema(BaseModel):
    """One expense line of ``POST /claims``.

    Carries the expense facts plus the receipt payload the client received from
    ``POST /expense-items/upload`` and is echoing back. Nothing here is trusted blindly: the
    service re-resolves each field as *correction -> submitted -> extraction* and verifies that
    ``fileUrl`` points at an object this employee uploaded.

    ``amount`` and ``merchantVendor`` may be omitted **only** when the extraction supplies them —
    the service raises a field-level error if neither source has a value.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    category: Optional[str] = Field(default="Misc / Other", max_length=64)
    subCategory: Optional[str] = Field(default="General Expense", max_length=120)
    amount: Optional[MoneyAmount] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    amountUSD: Optional[MoneyAmount] = None
    merchantVendor: Optional[str] = Field(default=None, max_length=200)
    expenseDate: Optional[date] = None
    purposeDescription: Optional[str] = ""
    attendees: Optional[str] = Field(default=None, max_length=4000)
    tripLog: Optional[Union[str, Dict[str, Any]]] = None
    hasPreApproval: Optional[bool] = False
    preApprovalDocRef: Optional[str] = Field(default=None, max_length=200)
    fxRate: Optional[Decimal] = Field(default=None, gt=0)

    # --- receipt, echoed back from the upload response ---
    receiptAttached: Optional[bool] = None
    fileUrl: Optional[str] = None
    fileName: Optional[str] = None
    mimeType: Optional[str] = Field(default=None, max_length=200)
    fileSizeBytes: Optional[int] = Field(default=None, ge=0)
    fileHash: Optional[str] = Field(default=None, min_length=64, max_length=64)
    ocrSource: Optional[str] = Field(default=None, max_length=32)
    ocrConfidence: Optional[Dict[str, Any]] = None
    ocrExtractedJson: Optional[Dict[str, Any]] = None
    #: Only the fields the employee edited away from the extraction's guess.
    employeeCorrectedData: Optional[Dict[str, Any]] = None
    #: Historical alias for ``ocrExtractedJson``.
    extractedReceipt: Optional[ReceiptDataSchema] = None

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
    def _default_amount_usd(self) -> "ExpenseItemCreateSchema":
        """Single-currency items need no conversion: USD amount defaults to the amount."""
        if self.amountUSD is None and self.amount is not None:
            self.amountUSD = self.amount
        return self


class ExpenseClaimCreateSchema(BaseModel):
    """Request body for ``POST /claims`` (create + submit) — a report header plus its items.

    Shape validation only — anything needing the database (employee exists, duplicate, receipt
    ownership) is enforced by ``app.domain.validators`` inside the service.

    ``employeeId`` is accepted for backwards compatibility but ignored: the owner is always bound
    to the authenticated identity so a caller cannot file a claim against someone else.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    employeeId: Optional[str] = Field(
        default=None, deprecated="Ignored — the claim owner is taken from the access token."
    )
    title: Optional[str] = Field(default=None, max_length=200)
    purpose: Optional[str] = None
    fromDate: Optional[date] = None
    toDate: Optional[date] = None
    currency: Optional[str] = Field(default="USD", min_length=3, max_length=3)
    #: At least one item — a claim with none would roll up to nothing and never resolve.
    items: List[ExpenseItemCreateSchema] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("must be a 3-letter ISO 4217 currency code")
        return code

    @model_validator(mode="after")
    def _dates_ordered(self) -> "ExpenseClaimCreateSchema":
        if self.fromDate and self.toDate and self.toDate < self.fromDate:
            raise ValueError("'toDate' cannot be earlier than 'fromDate'")
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


class ItemDecisionRequestSchema(BaseModel):
    """Request body for ``POST /claims/{id}/items/{item_id}/decision``.

    Decides one expense line. The claim's own status is re-derived afterwards and only moves once
    every item has an outcome — see ``ClaimService._roll_up_after_decisions``.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    action: str = Field(
        pattern="^(approve|reject)$", description="approve or reject this item."
    )
    #: Required when rejecting — a rejection without a reason is not actionable by the employee.
    notes: Optional[str] = Field(default=None, max_length=4000)
    expectedVersion: Optional[int] = Field(
        default=None,
        ge=1,
        description="The item's version, echoed back for optimistic-concurrency protection.",
    )


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


class WithdrawClaimSchema(BaseModel):
    """Request body for ``POST /claims/{id}/withdraw``.

    Everything is optional — a withdrawal needs no justification, unlike a rejection. The body may
    be omitted entirely.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    reason: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Optional note explaining why the claim was withdrawn.",
    )
    expectedVersion: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Claim 'version' from the last read. When supplied, a concurrent decision by a "
            "reviewer is rejected with 409 instead of racing the withdrawal."
        ),
    )


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
    """The signed-in caller.

    `sub` is the Cognito identity from the verified access token; everything else is read
    from the matching ``employees`` row. Cognito Groups are not used for RBAC.
    """
    sub: Optional[str] = None           # canonical immutable identity
    email: Optional[str] = None
    name: Optional[str] = None          # employees.full_name
    role: Optional[str] = None          # employees.role -> one of the 5 app roles
    employeeId: Optional[str] = None    # employees.employee_code (ownership link)


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


class ForgotPasswordRequestSchema(BaseModel):
    email: str


class ConfirmForgotPasswordRequestSchema(BaseModel):
    email: str
    code: str                                   # the confirmation code Cognito emailed
    newPassword: str


class MessageResponseSchema(BaseModel):
    """Generic acknowledgement for endpoints that return no data."""
    detail: str


class AdminCreateUserSchema(BaseModel):
    email: str
    name: str                              # backs employees.full_name (NOT NULL)
    grade: str                             # backs employees.grade
    role: str                              # custom:role_id — must be one of the 5 app roles
    managerId: Optional[str] = None        # another employee's UUID (employees.id)
    employeeId: Optional[str] = None       # deprecated: ignored, the server generates the code


class AdminUpdateUserSchema(BaseModel):
    name: Optional[str] = None
    grade: Optional[str] = None
    role: Optional[str] = None
    managerId: Optional[str] = None
    isActive: Optional[bool] = None


class AdminChangeRoleSchema(BaseModel):
    role: str                              # new custom:role_id value (validated against the 5)


class AdminUserSummarySchema(BaseModel):
    username: Optional[str] = None         # Cognito username (immutable) — the sub-linked handle
    sub: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None             # custom:role_id
    employeeCode: Optional[str] = None     # employees.employee_code (custom:employeeId)
    enabled: Optional[bool] = None
    status: Optional[str] = None
    employeeId: Optional[str] = None       # employees.id (UUID) — what managerId refers to
    fullName: Optional[str] = None
    grade: Optional[str] = None
    managerId: Optional[str] = None
    managerName: Optional[str] = None
    roleId: Optional[int] = None
    isActive: Optional[bool] = None


class AdminUserListSchema(BaseModel):
    users: List[AdminUserSummarySchema] = []
    nextToken: Optional[str] = None


class RoleOptionSchema(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


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


# --- Receipt upload / extraction (S3 + Textract, no persistence) ---

class ReceiptExtractionSchema(BaseModel):
    """200 response for ``POST /expense-items/upload``.

    Deliberately **not** a persisted entity: no claim and no expense item exists yet. The client
    holds this payload and echoes the ``file*``/``ocr*`` fields back inside an item of
    ``POST /claims``, which is where the row is finally written.

    There is no ``s3Bucket``/``s3Key``/``s3Region``: the bucket and region are server settings and
    the object key is recovered from ``fileUrl`` — which is also what the submit path checks to
    confirm the caller uploaded the document it is attaching.
    """

    # --- stored-file provenance the client echoes back verbatim ---
    fileUrl: Optional[str] = None
    fileName: str
    mimeType: Optional[str] = None
    fileSizeBytes: Optional[int] = None
    #: SHA-256 hex, computed server-side. Drives duplicate-receipt detection.
    fileHash: str

    # --- extraction ---
    ocrSource: str
    #: Per-field confidences on a 0-100 scale, e.g. ``{"vendor": 98.2, "total": 99.1}``.
    ocrConfidence: Optional[Dict[str, float]] = None
    #: Verbatim extractor output -> ``expense_items.ocr_extracted_json``.
    extraction: Optional[Dict[str, Any]] = None

    # --- prefill the client may present as editable defaults ---
    suggestedVendor: Optional[str] = None
    suggestedDate: Optional[str] = None
    suggestedAmount: Optional[float] = None
    suggestedCurrency: Optional[str] = None
    suggestedCategory: Optional[str] = None

    # --- advisory, never blocking ---
    duplicateOfClaimNumber: Optional[str] = None
    errorMessage: Optional[str] = None
