"""FastAPI dependencies: authentication, authorization, and object graph wiring.

**Auth.** The caller presents a verified Cognito **access token**, whose `sub` is the canonical
identity. Access tokens carry no profile or custom attributes, so the role, email and employee
link are read from the matching ``employees`` row — the database is the single source of truth
for authorization. Cognito Groups are ignored.

- `get_current_user`  -> 401 on missing/invalid/expired token; 403 when `sub` matches no active
  employee, or that employee's role is not a valid application role.
- `require_roles(...)` -> 403 when the caller's role isn't in the allowlist.

**Composition.** The provider functions at the bottom of this module construct one repository /
service graph per request, all sharing the request's single ``Session`` (so a route's whole unit of
work — business change plus audit record — commits or rolls back together). Routes depend on
services, never on repositories or sessions directly, and tests override any provider in place.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.ai.classification.service import DocumentClassificationService
from app.ai.core.config import ai_settings
from app.ai.duplicate_detection.service import DuplicateDetectionService
from app.ai.extraction.receipt_extractor import BedrockReceiptExtractor
from app.ai.governance.feature_flags import PersistedFeatureFlagStore
from app.ai.knowledge.service import KnowledgeService
from app.ai.prompts.registry import PromptRegistry
from app.ai.registry.flags import feature_flags
from app.ai.services.composition import build_duplicate_detection_service, build_knowledge_service
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import TokenError, verify_access_token
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.repositories.ai_inference_repository import AIInferenceRepository
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.claim_repository import ClaimRepository
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.fraud_repository import FraudResultRepository
from app.repositories.category_repository import CategoryRepository
from app.repositories.claim_policy_rule_repository import ClaimPolicyRuleRepository
from app.repositories.policy_rule_repository import PolicyRuleRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.workflow_repository import ApprovalWorkflowRepository
from app.services.audit_service import AuditService
from app.services.claim_service import ClaimService
from app.services.employee_service import EmployeeService
from app.services.category_service import CategoryService
from app.services.policy_rule_service import PolicyRuleService
from app.services.receipt_extraction import ReceiptExtractor, TextractReceiptExtractor

logger = get_logger(__name__)

# The only valid application roles (mirrors frontend/src/types.ts UserRole).
VALID_ROLES = frozenset({"employee", "manager", "finance", "admin", "auditor"})

_bearer = HTTPBearer(auto_error=False)


class CurrentUser:
    """Verified caller: `sub` from the access token, everything else from ``employees``."""

    def __init__(self, sub: Optional[str], email: Optional[str], role: str,
                 employee_id: Optional[str], claims: dict,
                 display_name: Optional[str] = None):
        self.sub = sub
        self.email = email
        self.role = role
        self.employee_id = employee_id
        self.claims = claims
        self.display_name = display_name


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = verify_access_token(credentials.credentials)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has no subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The access token carries no role/email, so the employee row is authoritative.
    employee = EmployeeRepository(db).get_by_cognito_sub(sub)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No employee record is linked to this account.",
        )
    if not employee.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is deactivated.",
        )

    role = employee.role_name
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has no valid application role.",
        )

    return CurrentUser(
        sub=sub,
        email=employee.email,
        role=role,
        employee_id=employee.employee_code,
        claims=claims,
        display_name=employee.full_name,
    )


def require_roles(*roles: str):
    """Dependency factory: allow only callers whose employee role is in `roles`."""
    allowed = frozenset(roles)

    def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation.",
            )
        return user

    return _dependency


def get_actor(user: CurrentUser = Depends(get_current_user)) -> Actor:
    """The domain-level identity for the caller (services never see ``CurrentUser``)."""
    return Actor.from_current_user(user)


def get_unit_of_work(db: Session = Depends(get_db)) -> UnitOfWork:
    """Commit control over the request's transaction, for mutating routes."""
    return UnitOfWork(db)


# --- repository providers -----------------------------------------------------
# Each takes the request-scoped Session, so every repository in one request shares
# one transaction.

def get_claim_repository(db: Session = Depends(get_db)) -> ClaimRepository:
    return ClaimRepository(db)


def get_employee_repository(db: Session = Depends(get_db)) -> EmployeeRepository:
    return EmployeeRepository(db)


def get_role_repository(db: Session = Depends(get_db)) -> RoleRepository:
    return RoleRepository(db)


def get_policy_rule_repository(db: Session = Depends(get_db)) -> PolicyRuleRepository:
    return PolicyRuleRepository(db)


def get_claim_policy_rule_repository(
    db: Session = Depends(get_db),
) -> ClaimPolicyRuleRepository:
    return ClaimPolicyRuleRepository(db)


def get_category_repository(db: Session = Depends(get_db)) -> CategoryRepository:
    return CategoryRepository(db)


def get_audit_repository(db: Session = Depends(get_db)) -> AuditLogRepository:
    return AuditLogRepository(db)


def get_fraud_repository(db: Session = Depends(get_db)) -> FraudResultRepository:
    return FraudResultRepository(db)


def get_workflow_repository(db: Session = Depends(get_db)) -> ApprovalWorkflowRepository:
    return ApprovalWorkflowRepository(db)


def get_ai_inference_repository(db: Session = Depends(get_db)) -> AIInferenceRepository:
    return AIInferenceRepository(db)


# --- service providers --------------------------------------------------------

def get_knowledge_service(db: Session = Depends(get_db)) -> KnowledgeService:
    """The AI platform's one public surface, bound to this request's session and transaction."""
    return build_knowledge_service(db)


def get_optional_decision_memory(
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> Optional[KnowledgeService]:
    """``ClaimService``'s decision-memory dependency, or ``None`` when the feature is off.

    Deciding the flag here — not inside ``ClaimService`` — is what makes "disabling AI via feature
    flag leaves the claim pipeline byte-identical to today" true: the service never even sees a
    recorder to call when the flag is off, rather than seeing one and choosing not to use it.
    """
    if not feature_flags.is_enabled("ai.decision_memory"):
        return None
    return knowledge_service


def get_duplicate_detection_service(
    db: Session = Depends(get_db),
) -> DuplicateDetectionService:
    """Advisory duplicate scanning + vendor resolution, bound to this request's session."""
    return build_duplicate_detection_service(db)


def get_optional_duplicate_detection(
    duplicate_detection_service: DuplicateDetectionService = Depends(
        get_duplicate_detection_service
    ),
) -> Optional[DuplicateDetectionService]:
    """``ClaimService``'s duplicate-scan dependency, or ``None`` when the feature is off.

    Same reasoning as :func:`get_optional_decision_memory`: the flag is decided here, not inside
    ``ClaimService``, so disabling it leaves the claim pipeline byte-identical to today.
    """
    if not feature_flags.is_enabled("ai.duplicate_detection"):
        return None
    return duplicate_detection_service


def get_document_classification_service(
    category_repository: CategoryRepository = Depends(get_category_repository),
    ai_inference_repository: AIInferenceRepository = Depends(get_ai_inference_repository),
) -> DocumentClassificationService:
    return DocumentClassificationService(category_repository, ai_inference_repository)


def get_optional_document_classification(
    service: DocumentClassificationService = Depends(get_document_classification_service),
) -> Optional[DocumentClassificationService]:
    """``ClaimService``'s document-classification dependency, or ``None`` when the feature is off.

    Same reasoning as :func:`get_optional_decision_memory`: the flag is decided here, not inside
    ``ClaimService``, so disabling it leaves the claim pipeline byte-identical to today.
    """
    if not feature_flags.is_enabled("ai.category_classification"):
        return None
    return service


def get_receipt_extractor(
    category_repository: CategoryRepository = Depends(get_category_repository),
    ai_inference_repository: AIInferenceRepository = Depends(get_ai_inference_repository),
) -> ReceiptExtractor:
    """Which engine reads an uploaded receipt: AWS Textract, or one multimodal Bedrock call.

    A provider switch rather than a feature flag, so it is read from settings here rather than from
    the flag tree — the same treatment ``AI_RULE_EXTRACTION_PROVIDER`` gets. The default is
    ``textract``, which leaves an untouched deployment behaving exactly as it did before this
    dependency existed.

    The choice is made here, in ``deps``, for the same reason the classification flag is: the route
    depends on the ``ReceiptExtractor`` protocol in ``app.services``, so ``app/api`` never imports
    ``app.ai``. An unrecognized provider name falls back to Textract with a warning rather than
    failing the request — a typo in an env var must not take receipt upload down.
    """
    provider = (ai_settings.RECEIPT_EXTRACTION_PROVIDER or "").strip().lower()
    if provider == "bedrock":
        return BedrockReceiptExtractor(category_repository, ai_inference_repository)
    if provider not in ("", "textract"):
        logger.warning(
            "receipt.unknown_extraction_provider",
            extra={"provider": provider[:64], "using": "textract"},
        )
    return TextractReceiptExtractor()


def get_audit_service(
    audit_repository: AuditLogRepository = Depends(get_audit_repository),
) -> AuditService:
    return AuditService(audit_repository)


def get_prompt_registry(
    db: Session = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
) -> PromptRegistry:
    """The M13 admin router's caller for publishing/rolling back a prompt template."""
    return PromptRegistry(db, audit_service=audit_service)


def get_feature_flag_store(
    db: Session = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
) -> PersistedFeatureFlagStore:
    """The M13 admin router's caller for a durable, audited feature-flag override."""
    return PersistedFeatureFlagStore(db, audit_service=audit_service)


def get_employee_service(
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
    role_repository: RoleRepository = Depends(get_role_repository),
) -> EmployeeService:
    return EmployeeService(employee_repository, role_repository)


def get_policy_rule_service(
    policy_rule_repository: PolicyRuleRepository = Depends(get_policy_rule_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> PolicyRuleService:
    return PolicyRuleService(policy_rule_repository, audit_service)


def get_optional_policy_rule_service(
    policy_rule_service: PolicyRuleService = Depends(get_policy_rule_service),
) -> Optional[PolicyRuleService]:
    """``ClaimService``'s legacy category-policy-engine dependency, or ``None`` when
    ``POLICY_RULES_ENGINE_ENABLED`` is off (the default).

    Same reasoning as :func:`get_optional_decision_memory`: the flag is decided here, not inside
    ``ClaimService``, so a disabled engine leaves the claim pipeline byte-identical to never having
    called it. Does not affect the standalone `policy_rules` admin CRUD API, which depends on
    :func:`get_policy_rule_service` directly rather than through ``ClaimService``.
    """
    if not settings.POLICY_RULES_ENGINE_ENABLED:
        return None
    return policy_rule_service


def get_category_service(
    category_repository: CategoryRepository = Depends(get_category_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> CategoryService:
    return CategoryService(category_repository, audit_service)


def get_claim_service(
    claim_repository: ClaimRepository = Depends(get_claim_repository),
    fraud_repository: FraudResultRepository = Depends(get_fraud_repository),
    workflow_repository: ApprovalWorkflowRepository = Depends(get_workflow_repository),
    employee_service: EmployeeService = Depends(get_employee_service),
    policy_rule_service: Optional[PolicyRuleService] = Depends(get_optional_policy_rule_service),
    audit_service: AuditService = Depends(get_audit_service),
    decision_memory: Optional[KnowledgeService] = Depends(get_optional_decision_memory),
    duplicate_detection: Optional[DuplicateDetectionService] = Depends(
        get_optional_duplicate_detection
    ),
    document_classification: Optional[DocumentClassificationService] = Depends(
        get_optional_document_classification
    ),
    claim_policy_rule_repository: ClaimPolicyRuleRepository = Depends(
        get_claim_policy_rule_repository
    ),
) -> ClaimService:
    return ClaimService(
        claim_repository=claim_repository,
        fraud_repository=fraud_repository,
        workflow_repository=workflow_repository,
        employee_service=employee_service,
        policy_rule_service=policy_rule_service,
        audit_service=audit_service,
        decision_memory=decision_memory,
        duplicate_detection=duplicate_detection,
        document_classification=document_classification,
        claim_policy_rule_repository=claim_policy_rule_repository,
    )
