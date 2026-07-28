import time
import random
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.services.store import claims_store, INITIAL_EMPLOYEES, add_audit_log
from app.services.policy_engine import evaluate_expense_policy
from app.services.fraud_engine import screen_for_anomalies
from app.schemas.schemas import ExpenseClaimCreateSchema, ActionRequestSchema
from app.core.deps import CurrentUser, get_current_user, require_roles

router = APIRouter(prefix="/claims", tags=["Claims"])

@router.get("", response_model=List[dict])
def get_claims(
    category: Optional[str] = None,
    status: Optional[str] = None,
    employeeId: Optional[str] = None,
    riskLevel: Optional[str] = None,
    current: CurrentUser = Depends(get_current_user),
):
    # Employees may only see their own claims — ignore any employeeId they pass.
    if current.role == "employee":
        employeeId = current.employee_id
    filtered = list(claims_store)
    if category:
        filtered = [c for c in filtered if c.get("category") == category]
    if status:
        filtered = [c for c in filtered if c.get("status") == status]
    if employeeId:
        filtered = [c for c in filtered if c.get("employeeId") == employeeId]
    if riskLevel:
        filtered = [c for c in filtered if c.get("fraudScreening", {}).get("riskLevel") == riskLevel]
    return filtered

@router.post("", status_code=status.HTTP_201_CREATED)
def create_claim(
    payload: ExpenseClaimCreateSchema,
    current: CurrentUser = Depends(require_roles("employee")),
):
    claim_data = payload.model_dump()
    # An employee can only file claims for themselves — bind to the token identity.
    claim_data["employeeId"] = current.employee_id

    new_id = f"claim-{int(time.time()*1000)}"
    claim_number = f"EXP-{datetime.now().year}-{random.randint(1000, 9999)}"

    employee = next((e for e in INITIAL_EMPLOYEES if e["id"] == claim_data.get("employeeId")), INITIAL_EMPLOYEES[0])

    # 1. Policy Engine Evaluation
    policy_report = evaluate_expense_policy({
        **claim_data,
        "employeeGrade": employee["grade"]
    })

    # 2. Fraud Screening
    fraud_report = screen_for_anomalies({
        **claim_data,
        "id": new_id,
        "employeeId": employee["id"]
    }, claims_store)

    # Determine Status
    if fraud_report["isFlagged"] and fraud_report["riskScore"] >= 40:
        final_status = "Flagged_Fraud"
    elif not policy_report["overallPassed"]:
        final_status = "Manager_Review"
    elif policy_report["requiresManualReview"]:
        final_status = "Manager_Review"
    else:
        final_status = "Auto_Approved"

    now_iso = datetime.utcnow().isoformat() + "Z"

    amount = claim_data.get("amount") or 0.0
    amount_usd = claim_data.get("amountUSD") or amount

    new_claim = {
        "id": new_id,
        "claimNumber": claim_number,
        "employeeId": employee["id"],
        "employeeName": employee["name"],
        "employeeGrade": employee["grade"],
        "department": employee["department"],
        "expenseDate": claim_data.get("expenseDate") or datetime.now().strftime("%Y-%m-%d"),
        "submissionDate": datetime.now().strftime("%Y-%m-%d"),
        "category": claim_data.get("category") or "Misc / Other",
        "subCategory": claim_data.get("subCategory") or "General Expense",
        "amount": amount,
        "currency": claim_data.get("currency") or "USD",
        "amountUSD": amount_usd,
        "merchantVendor": claim_data.get("merchantVendor") or "Vendor",
        "purposeDescription": claim_data.get("purposeDescription") or "",
        "attendees": claim_data.get("attendees"),
        "tripLog": claim_data.get("tripLog"),
        "hasPreApproval": claim_data.get("hasPreApproval", False),
        "preApprovalDocRef": claim_data.get("preApprovalDocRef"),
        "receiptAttached": claim_data.get("receiptAttached", True),
        "receiptUrl": claim_data.get("receiptUrl"),
        "extractedReceipt": claim_data.get("extractedReceipt"),
        "policyValidation": policy_report,
        "fraudScreening": fraud_report,
        "status": final_status,
        "workflowHistory": [
            {
                "timestamp": now_iso,
                "actorName": employee["name"],
                "actorRole": "employee",
                "stepName": "Submit Claim",
                "action": "Submitted expense claim with receipt",
                "status": "SUCCESS",
                "traceId": f"trace-sf-{int(time.time()*1000)}-1"
            },
            {
                "timestamp": now_iso,
                "actorName": "FastAPI: Policy Engine",
                "actorRole": "admin",
                "stepName": "Policy Validation",
                "action": "Passed policy constraints" if policy_report["overallPassed"] else "Flagged policy violations",
                "status": "SUCCESS" if policy_report["overallPassed"] else "WARNING",
                "notes": policy_report["reasoningSummary"],
                "traceId": f"trace-sf-{int(time.time()*1000)}-2"
            },
            {
                "timestamp": now_iso,
                "actorName": "FastAPI: Anomaly Engine",
                "actorRole": "admin",
                "stepName": "Fraud Screening",
                "action": f"Risk Score: {fraud_report['riskScore']}/100 ({fraud_report['riskLevel']})",
                "status": "FAILED" if fraud_report["isFlagged"] else "SUCCESS",
                "notes": fraud_report["rationale"],
                "traceId": f"trace-sf-{int(time.time()*1000)}-3"
            }
        ],
        "comments": [
            {
                "id": f"c-{int(time.time()*1000)}",
                "authorName": "FastAPI Workflow Engine",
                "authorRole": "admin",
                "timestamp": now_iso,
                "text": f"Claim processed. Route status set to: {final_status.replace('_', ' ')}."
            }
        ]
    }

    claims_store.insert(0, new_claim)

    add_audit_log(
        employee["name"],
        "employee",
        "SUBMIT_CLAIM",
        claim_number,
        f"Submitted claim for ${amount_usd:.2f} ({new_claim['category']}) -> Route: {final_status}"
    )

    return new_claim

@router.post("/{claim_id}/action")
def execute_claim_action(
    claim_id: str,
    payload: ActionRequestSchema,
    current: CurrentUser = Depends(require_roles("manager", "finance", "admin")),
):
    claim = next((c for c in claims_store if c.get("id") == claim_id or c.get("claimNumber") == claim_id), None)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    action = payload.action
    # Only finance/admin may disburse funds; managers can approve/reject/flag.
    if action == "DISBURSE" and current.role not in ("finance", "admin"):
        raise HTTPException(status_code=403, detail="Only finance or admin can disburse.")

    # Actor is the authenticated caller, not client-supplied strings.
    actor_role = current.role
    actor_name = current.email or payload.actorName or actor_role
    notes = payload.notes or ""

    if action == "APPROVE":
        if claim.get("status") == "Manager_Review":
            claim["status"] = "Finance_Review"
        else:
            claim["status"] = "Approved"
    elif action == "REJECT":
        claim["status"] = "Rejected"
    elif action == "DISBURSE":
        claim["status"] = "Disbursed"
    elif action == "FLAG_FRAUD":
        claim["status"] = "Flagged_Fraud"

    now_iso = datetime.utcnow().isoformat() + "Z"

    claim.setdefault("workflowHistory", []).append({
        "timestamp": now_iso,
        "actorName": actor_name,
        "actorRole": actor_role,
        "stepName": f"Action: {action}",
        "action": f"Executed {action} on claim {claim.get('claimNumber')}",
        "status": "WARNING" if action in ["REJECT", "FLAG_FRAUD"] else "SUCCESS",
        "notes": notes
    })

    if notes:
        claim.setdefault("comments", []).append({
            "id": f"c-{int(time.time()*1000)}",
            "authorName": actor_name,
            "authorRole": actor_role,
            "timestamp": now_iso,
            "text": notes
        })

    add_audit_log(
        actor_name,
        actor_role,
        "APPROVAL_ACTION",
        claim.get("claimNumber", claim_id),
        f"Action {action} executed. Status updated to {claim['status']}"
    )

    return claim
