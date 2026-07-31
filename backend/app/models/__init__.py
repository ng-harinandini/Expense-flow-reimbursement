"""ORM models package. Importing this registers all models on ``Base.metadata``.

Alembic's ``env.py`` imports this module, so **every new model file must be imported here** or
autogenerate will propose dropping its table.
"""

from app.models.audit import AIInferenceLog, AuditLog  # noqa: F401
from app.models.claim import (  # noqa: F401
    Attachment,
    Claim,
    ClaimStatusHistory,
    Comment,
)
from app.models.enums import (  # noqa: F401
    AIInferenceStatus,
    ApprovalStepStatus,
    ApprovalWorkflowStatus,
    AttachmentKind,
    AuditAction,
    AuditEntity,
    ClaimStatus,
    EmployeeGrade,
    FraudRiskLevel,
)
from app.models.fraud import FRAUD_ENGINE_VERSION, FraudResult  # noqa: F401
from app.models.organization import Employee  # noqa: F401
from app.models.policy import PolicyRule  # noqa: F401
from app.models.role import Role  # noqa: F401
from app.models.receipt import (  # noqa: F401
    ExtractionStatus,
    Receipt,
    ReceiptField,
    ReceiptLineItem,
)
from app.models.workflow import (  # noqa: F401
    STANDARD_WORKFLOW_CODE,
    ApprovalStep,
    ApprovalWorkflow,
)

__all__ = [
    "AIInferenceLog",
    "AIInferenceStatus",
    "ApprovalStep",
    "ApprovalStepStatus",
    "ApprovalWorkflow",
    "ApprovalWorkflowStatus",
    "Attachment",
    "AttachmentKind",
    "AuditAction",
    "AuditEntity",
    "AuditLog",
    "Claim",
    "ClaimStatus",
    "ClaimStatusHistory",
    "Comment",
    "Employee",
    "EmployeeGrade",
    "ExtractionStatus",
    "FRAUD_ENGINE_VERSION",
    "FraudResult",
    "FraudRiskLevel",
    "PolicyRule",
    "Receipt",
    "ReceiptField",
    "ReceiptLineItem",
    "Role",
    "STANDARD_WORKFLOW_CODE",
]
