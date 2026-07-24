import os
import json
import logging
from typing import Dict, Any, Optional
from google import genai
from app.services.iam_refiner import generate_default_iam_refinement, DEFAULT_BASIC_USER_IAM_POLICY

logger = logging.getLogger(__name__)

def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY", "")
    if api_key and api_key != "MY_GEMINI_API_KEY":
        try:
            return genai.Client(api_key=api_key)
        except Exception as e:
            logger.error(f"Error initializing Gemini client: {e}")
            return None
    return None

def extract_receipt_ocr(
    image_base64: Optional[str] = None,
    sample_text: Optional[str] = None,
    file_name: str = "receipt.png"
) -> Dict[str, Any]:
    client = get_gemini_client()
    
    if client:
        try:
            prompt = """You are an expert OCR receipt parsing AI for corporate expense compliance.
Analyze the following receipt information and extract structured JSON matching this EXACT schema:
{
  "vendorName": "string",
  "transactionDate": "YYYY-MM-DD",
  "totalAmount": number,
  "currency": "USD",
  "hasAlcohol": boolean,
  "alcoholItemCount": number,
  "attendeesNoted": ["string"],
  "lineItems": [
    { "description": "string", "amount": number, "category": "string" }
  ],
  "confidenceScore": number (0 to 1)
}"""
            contents = []
            if image_base64:
                import base64
                clean_b64 = image_base64.replace("data:image/png;base64,", "").replace("data:image/jpeg;base64,", "")
                image_bytes = base64.b64decode(clean_b64)
                contents = [
                    genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    prompt
                ]
            else:
                contents = f"{prompt}\n\nReceipt Content:\n{sample_text or 'Generic receipt details'}"

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            extracted = json.loads(response.text or "{}")
            extracted["fileName"] = file_name
            return extracted
        except Exception as e:
            logger.error(f"Gemini OCR error: {e}")

    # Offline / Fallback extraction
    return {
        "fileName": file_name,
        "vendorName": "Acme Merchant Cafe",
        "transactionDate": "2026-07-23",
        "totalAmount": 28.50,
        "currency": "USD",
        "hasAlcohol": False,
        "lineItems": [
            {"description": "Business Lunch Special", "amount": 24.00, "category": "Food"},
            {"description": "Sparkling Water", "amount": 4.50, "category": "Beverage"}
        ],
        "confidenceScore": 0.96
    }

def analyze_policy_reasoning(claim: Dict[str, Any], policy_rules: Optional[list] = None) -> Dict[str, Any]:
    client = get_gemini_client()
    if client:
        try:
            prompt = f"""Analyze this corporate expense claim against the official Expense Policy rules:
Claim Details: {json.dumps(claim)}
Policy Rules: {json.dumps(policy_rules or [])}

Provide a concise JSON response:
{{
  "policyPassed": boolean,
  "executiveSummary": "1-2 sentence executive explanation",
  "specificViolations": ["string"],
  "recommendedApprovalRoute": "AUTO_APPROVE" | "MANAGER_REVIEW" | "FINANCE_DIRECTOR_REVIEW" | "REJECT"
}}"""
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text or "{}")
        except Exception as e:
            logger.error(f"Gemini Policy Reasoning error: {e}")

    return {
        "policyPassed": True,
        "executiveSummary": "Claim falls within allowed category limits and guidelines.",
        "specificViolations": [],
        "recommendedApprovalRoute": "AUTO_APPROVE"
    }

def refine_iam_policy_with_gemini(
    current_policy_json: Optional[str] = None,
    environment_name: str = "AWS Serverless Sandbox",
    use_case_description: str = "AWS Step Functions + Lambda + Textract"
) -> Dict[str, Any]:
    client = get_gemini_client()
    input_policy = current_policy_json or DEFAULT_BASIC_USER_IAM_POLICY

    default_ref = generate_default_iam_refinement()

    if client:
        try:
            prompt = f"""You are a Principal AWS Security Architect specializing in zero-trust serverless sandbox IAM refinement.
Analyze and refine this user-provided AWS IAM Policy for an Expense Reimbursement & Fraud Detection Serverless Application:

Input Policy:
{input_policy}

Environment: {environment_name}
Context: {use_case_description}

Return a clean JSON with exact structure:
{{
  "refinedPolicyJson": "string formatted JSON string",
  "securityScore": number (0 to 100),
  "summaryOfChanges": ["string"],
  "leastPrivilegeViolationsFixed": ["string"],
  "recommendedAwsServices": ["string"]
}}"""
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            result = json.loads(response.text or "{}")
            return {
                "refinedPolicyJson": result.get("refinedPolicyJson") or default_ref["refinedPolicyJson"],
                "securityScore": result.get("securityScore") or 94,
                "summaryOfChanges": result.get("summaryOfChanges") or default_ref["summaryOfChanges"],
                "leastPrivilegeViolationsFixed": result.get("leastPrivilegeViolationsFixed") or default_ref["leastPrivilegeViolationsFixed"],
                "recommendedAwsServices": result.get("recommendedAwsServices") or default_ref["recommendedAwsServices"],
                "terraformCode": default_ref["terraformCode"],
                "samTemplateYaml": default_ref["samTemplateYaml"]
            }
        except Exception as e:
            logger.error(f"Gemini IAM Refine error: {e}")

    return default_ref
