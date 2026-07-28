from fastapi import APIRouter, Depends
from app.schemas.schemas import OcrExtractRequestSchema, PolicyReasoningRequestSchema, RefineIamRequestSchema
from app.services.gemini_service import extract_receipt_ocr, analyze_policy_reasoning, refine_iam_policy_with_gemini
from app.services.store import add_audit_log
from app.core.deps import get_current_user, require_roles

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

@router.post("/refine-iam-policy", dependencies=[Depends(require_roles("admin"))])
def refine_iam_policy(payload: RefineIamRequestSchema):
    result = refine_iam_policy_with_gemini(
        current_policy_json=payload.currentPolicyJson,
        environment_name=payload.environmentName or "AWS Serverless Sandbox",
        use_case_description=payload.useCaseDescription or "AWS Step Functions + Lambda + Textract"
    )

    add_audit_log(
        "Security Architect",
        "admin",
        "IAM_REFINED",
        "iam-policy-sandbox",
        f"Refined IAM policy. Security Score improved to {result.get('securityScore', 94)}/100."
    )

    return result
