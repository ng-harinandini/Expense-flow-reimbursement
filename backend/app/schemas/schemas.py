from typing import List, Optional, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime

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

class ExpenseClaimCreateSchema(BaseModel):
    employeeId: Optional[str] = None
    category: Optional[str] = "Misc / Other"
    subCategory: Optional[str] = "General Expense"
    amount: float
    currency: Optional[str] = "USD"
    amountUSD: Optional[float] = None
    merchantVendor: str
    expenseDate: Optional[str] = None
    purposeDescription: Optional[str] = ""
    attendees: Optional[str] = None
    tripLog: Optional[str] = None
    hasPreApproval: Optional[bool] = False
    preApprovalDocRef: Optional[str] = None
    receiptAttached: Optional[bool] = True
    receiptUrl: Optional[str] = None
    extractedReceipt: Optional[ReceiptDataSchema] = None

class ActionRequestSchema(BaseModel):
    action: str  # APPROVE, REJECT, DISBURSE, FLAG_FRAUD
    actorName: Optional[str] = "Manager"
    actorRole: Optional[str] = "manager"
    notes: Optional[str] = ""

class PolicyRuleDefinitionSchema(BaseModel):
    category: str
    maxAmountUSD: Union[float, str]
    autoApproveLimitUSD: Optional[float] = None
    receiptRequiredAboveUSD: float
    requiresPreApproval: bool
    gradeTier: str
    specialRules: Optional[List[str]] = []

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
