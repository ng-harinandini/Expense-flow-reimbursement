"""Gemini AI service endpoints.

Unchanged behaviour; the only Phase 1 change is that the IAM-refinement audit record now goes to
the durable ``audit_logs`` table (via ``AuditService``) instead of an in-memory list, and is
attributed to the authenticated caller rather than a hardcoded name.
"""

from fastapi import APIRouter, Depends

from app.core.deps import (
    CurrentUser,
    get_audit_service,
    get_current_user,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.models.enums import AuditAction, AuditEntity
from app.schemas.schemas import (
    OcrExtractRequestSchema,
    PolicyReasoningRequestSchema,
    RefineIamRequestSchema,
)
from app.services.audit_service import AuditService
from app.services.gemini_service import (
    analyze_policy_reasoning,
    extract_receipt_ocr,
    refine_iam_policy_with_gemini,
)

router = APIRouter(prefix="/ai", tags=["Gemini AI Services"])

@router.post("/ocr-extract", dependencies=[Depends(get_current_user)])
def ocr_extract(payload: OcrExtractRequestSchema):
    result = extract_receipt_ocr(
        image_base64=payload.imageBase64,
        sample_text=payload.sampleReceiptText,
        file_name=payload.fileName or "receipt.png"
    )
    return result

@router.post("/policy-reasoning", dependencies=[Depends(require_roles("manager", "finance", "admin"))])
def policy_reasoning(payload: PolicyReasoningRequestSchema):
    result = analyze_policy_reasoning(
        claim=payload.claim,
        policy_rules=payload.policyRules
    )
    return result

@router.post("/refine-iam-policy")
def refine_iam_policy(
    payload: RefineIamRequestSchema,
    current: CurrentUser = Depends(require_roles("admin")),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    result = refine_iam_policy_with_gemini(
        current_policy_json=payload.currentPolicyJson,
        environment_name=payload.environmentName or "AWS Serverless Sandbox",
        use_case_description=payload.useCaseDescription or "AWS Step Functions + Lambda + Textract"
    )

    audit.record(
        actor=Actor.from_current_user(current),
        action=AuditAction.IAM_REFINED,
        entity_type=AuditEntity.IAM_POLICY,
        entity_id="iam-policy-sandbox",
        details=f"Refined IAM policy. Security Score improved to {result.get('securityScore', 94)}/100.",
        after={"securityScore": result.get("securityScore")},
    )
    uow.commit()

    return result
