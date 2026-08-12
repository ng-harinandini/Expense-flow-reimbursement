"""Category policy evaluation.

Unchanged rule logic — only its *input* changed in Phase 1: the ruleset now arrives as an argument
sourced from the ``policy_rules`` table (``PolicyRuleService.rules_for_engine``) instead of being
imported from a module-level Python list. Behaviour is intentionally identical; evaluating the new
declarative ``conditions``/``actions`` payloads is a later phase.

Category names were migrated from the original five (``Meals``, ``Ground Transport``, ``Flights``,
``Lodging``, ``Client Entertainment``) to the fifteen-category invoice-classification vocabulary in
migration ``0012_category_custom_fields`` — see :mod:`app.models.category`. The five categories with
genuinely bespoke rules below (alcohol prohibition, grade-tiered cabin class, grade-tiered nightly
caps, attendee-listing + grade gate) keep dedicated branches under their new names
(``Taxi / Cab / Ride-hailing``, ``Air Travel``, ``Hotel / Lodging``,
``Client / Business Entertainment``); the other ten categories fall through to the generic ``else``
branch, which already reads ``maxAmountUSD``/``autoApproveLimitUSD``/``receiptRequiredAboveUSD`` off
the matching ``policy_rules`` row rather than needing a hardcoded branch of their own.
"""

from datetime import datetime, date
from typing import Any, Dict, List, Optional, Sequence

GENERAL_POLICY_CONSTANTS = {
    "CLAIM_AGE_MAX_DAYS": 90,
    "DEFAULT_CURRENCY": "USD"
}

def evaluate_expense_policy(
    claim: Dict[str, Any],
    policy_rules: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Evaluate ``claim`` against ``policy_rules`` (the active, effective-dated ruleset).

    ``policy_rules`` is the list of rule dictionaries produced by
    ``app.services.mappers.policy_rules_to_engine_input``. When omitted, category defaults apply —
    the same fallback the old module-level list provided for unknown categories.
    """
    rules: Sequence[Dict[str, Any]] = policy_rules or []
    checks: List[Dict[str, Any]] = []

    category = claim.get("category") or "Miscellaneous / Others"
    amount_usd = float(claim.get("amountUSD") or claim.get("amount") or 0.0)
    grade = claim.get("employeeGrade") or "L1"
    has_receipt = bool(claim.get("receiptAttached", False))
    
    # Calculate days since expense
    expense_date_str = claim.get("expenseDate") or date.today().isoformat()
    submission_date_str = claim.get("submissionDate") or date.today().isoformat()
    
    try:
        exp_date = datetime.strptime(expense_date_str[:10], "%Y-%m-%d").date()
        sub_date = datetime.strptime(submission_date_str[:10], "%Y-%m-%d").date()
        days_since_expense = abs((sub_date - exp_date).days)
    except Exception:
        days_since_expense = 0

    overall_passed = True
    requires_manual_review = False
    max_limit_allowed = 50.0
    auto_approve_limit = 0.0
    receipt_required = True
    requires_director_approval_for_age = False

    # 1. Check Age / 90-Day Rule
    if days_since_expense > GENERAL_POLICY_CONSTANTS["CLAIM_AGE_MAX_DAYS"]:
        overall_passed = False
        requires_manual_review = True
        requires_director_approval_for_age = True
        checks.append({
            "ruleId": "AGE_LIMIT_90_DAYS",
            "ruleName": "90-Day Claim Submission Limit",
            "category": category,
            "passed": False,
            "severity": "VIOLATION",
            "message": f"Claim submitted {days_since_expense} days after transaction date (exceeds {GENERAL_POLICY_CONSTANTS['CLAIM_AGE_MAX_DAYS']} day limit).",
            "details": "Claims submitted after 90 days require written justification and Finance Director approval."
        })
    else:
        checks.append({
            "ruleId": "AGE_LIMIT_90_DAYS",
            "ruleName": "90-Day Submission Limit",
            "category": category,
            "passed": True,
            "severity": "INFO",
            "message": f"Submitted within {days_since_expense} days of expense date (limit 90 days)."
        })

    # Find matching policy rule definition
    policy_def = next((r for r in rules if r.get("category") == category), None)

    if category == "Meals":
        max_limit_allowed = 40.0
        auto_approve_limit = 25.0
        receipt_required = amount_usd > 25.0

        if amount_usd > 40.0:
            overall_passed = False
            requires_manual_review = True
            checks.append({
                "ruleId": "MEAL_MAX_EXCEEDED",
                "ruleName": "Daily Meal Maximum ($40)",
                "category": "Meals",
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Meal claim ${amount_usd:.2f} exceeds daily maximum limit of $40.00."
            })
        else:
            checks.append({
                "ruleId": "MEAL_MAX_PASS",
                "ruleName": "Daily Meal Maximum ($40)",
                "category": "Meals",
                "passed": True,
                "severity": "INFO",
                "message": f"Amount ${amount_usd:.2f} is within $40.00 daily limit."
            })

        if amount_usd > 25.0:
            requires_manual_review = True
            checks.append({
                "ruleId": "MEAL_REQUIRES_REVIEW",
                "ruleName": "Auto-Approve Ceiling ($25)",
                "category": "Meals",
                "passed": True,
                "severity": "REQUIREMENT",
                "message": f"Amount ${amount_usd:.2f} is above $25 auto-approve limit. Requires manager review."
            })

        extracted = claim.get("extractedReceipt") or {}
        if extracted.get("hasAlcohol"):
            overall_passed = False
            requires_manual_review = True
            checks.append({
                "ruleId": "MEAL_ALCOHOL_PROHIBITED",
                "ruleName": "Alcohol Non-Reimbursable in Meals",
                "category": "Meals",
                "passed": False,
                "severity": "VIOLATION",
                "message": "Receipt contains alcohol. Alcohol is NOT reimbursable under Meals."
            })

    elif category == "Taxi / Cab / Ride-hailing":
        max_limit_allowed = 150.0
        auto_approve_limit = 50.0
        receipt_required = True

        if amount_usd > 150.0:
            overall_passed = False
            requires_manual_review = True
            checks.append({
                "ruleId": "TAXI_MAX_EXCEEDED",
                "ruleName": "Taxi / Cab Max ($150/trip)",
                "category": "Taxi / Cab / Ride-hailing",
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Amount ${amount_usd:.2f} exceeds $150.00 trip maximum."
            })

        if amount_usd > 50.0:
            requires_manual_review = True
            checks.append({
                "ruleId": "TAXI_REVIEW_REQUIRED",
                "ruleName": "Taxi / Cab Auto-Approve Limit ($50)",
                "category": "Taxi / Cab / Ride-hailing",
                "passed": True,
                "severity": "REQUIREMENT",
                "message": f"Amount ${amount_usd:.2f} exceeds $50.00 auto-approve limit."
            })

    elif category == "Air Travel":
        max_limit_allowed = 10000.0
        auto_approve_limit = 0.0
        receipt_required = True
        requires_manual_review = True

        checks.append({
            "ruleId": "FLIGHT_ALWAYS_MANUAL",
            "ruleName": "Air Travel Always Manual Review",
            "category": "Air Travel",
            "passed": True,
            "severity": "REQUIREMENT",
            "message": "Air travel is NEVER auto-approved. Mandatory Manager review required."
        })

        if grade in ["L1", "L2", "L3", "L4"]:
            checks.append({
                "ruleId": "FLIGHT_CLASS_L1_L4",
                "ruleName": "Flight Class Policy (Economy)",
                "category": "Air Travel",
                "passed": True,
                "severity": "INFO",
                "message": f"Grade {grade} permitted class: Economy class only."
            })
        elif grade in ["L5", "Director"]:
            checks.append({
                "ruleId": "FLIGHT_CLASS_L5",
                "ruleName": "Flight Class Policy (Premium Economy > 6hrs)",
                "category": "Air Travel",
                "passed": True,
                "severity": "INFO",
                "message": f"Grade {grade} permitted class: Premium Economy for flights > 6 hours."
            })
        elif grade == "VP":
            checks.append({
                "ruleId": "FLIGHT_CLASS_VP",
                "ruleName": "Flight Class Policy (Business Class > 6hrs)",
                "category": "Air Travel",
                "passed": True,
                "severity": "INFO",
                "message": "Grade VP permitted class: Business class for flights > 6 hours."
            })

    elif category == "Hotel / Lodging":
        auto_approve_limit = 0.0
        requires_manual_review = True
        receipt_required = True

        is_l1_l3 = grade in ["L1", "L2", "L3"]
        max_limit_allowed = 120.0 if is_l1_l3 else 250.0

        if amount_usd > max_limit_allowed:
            overall_passed = False
            checks.append({
                "ruleId": "LODGING_LIMIT_EXCEEDED",
                "ruleName": f"Lodging Nightly Limit (${max_limit_allowed:.0f}/night)",
                "category": "Hotel / Lodging",
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Amount ${amount_usd:.2f} exceeds Grade {grade} lodging limit of ${max_limit_allowed:.0f}/night."
            })
        else:
            checks.append({
                "ruleId": "LODGING_LIMIT_PASS",
                "ruleName": f"Lodging Nightly Limit (${max_limit_allowed:.0f}/night)",
                "category": "Hotel / Lodging",
                "passed": True,
                "severity": "INFO",
                "message": f"Amount ${amount_usd:.2f} is compliant with ${max_limit_allowed:.0f}/night ceiling."
            })

        checks.append({
            "ruleId": "LODGING_ALWAYS_MANUAL",
            "ruleName": "Lodging Always Manual Review",
            "category": "Hotel / Lodging",
            "passed": True,
            "severity": "REQUIREMENT",
            "message": "Lodging claims always require manual manager review."
        })

    elif category == "Client / Business Entertainment":
        max_limit_allowed = 500.0
        auto_approve_limit = 0.0
        requires_manual_review = True
        receipt_required = amount_usd > 50.0

        is_manager_or_above = grade in ["L5", "Director", "VP"]
        if not is_manager_or_above:
            overall_passed = False
            checks.append({
                "ruleId": "CLIENT_ENT_GRADE_RESTRICTION",
                "ruleName": "Manager+ Grade Requirement for Client Entertainment",
                "category": "Client / Business Entertainment",
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Grade {grade} is not authorized for Client / Business Entertainment. Requires Manager+ (L5+) grade."
            })
        else:
            checks.append({
                "ruleId": "CLIENT_ENT_GRADE_PASS",
                "ruleName": "Manager+ Grade Authorization",
                "category": "Client / Business Entertainment",
                "passed": True,
                "severity": "INFO",
                "message": f"Grade {grade} authorized for Client / Business Entertainment."
            })

        if amount_usd > 500.0:
            overall_passed = False
            checks.append({
                "ruleId": "CLIENT_ENT_MAX_EXCEEDED",
                "ruleName": "Event Cap ($500)",
                "category": "Client / Business Entertainment",
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Amount ${amount_usd:.2f} exceeds $500 per event limit."
            })

        attendees = claim.get("attendees") or ""
        if len(attendees.strip()) < 5:
            overall_passed = False
            checks.append({
                "ruleId": "CLIENT_ENT_MISSING_ATTENDEES",
                "ruleName": "Attendee Listing Requirement",
                "category": "Client / Business Entertainment",
                "passed": False,
                "severity": "VIOLATION",
                "message": "Must list all internal and external attendees and business purpose."
            })

    else:
        max_val = policy_def.get("maxAmountUSD") if policy_def else 300.0
        max_limit_allowed = float(max_val) if isinstance(max_val, (int, float)) else 300.0
        auto_approve_limit = float(policy_def.get("autoApproveLimitUSD") or 0.0) if policy_def else 0.0
        receipt_required = amount_usd > (policy_def.get("receiptRequiredAboveUSD", 0.0) if policy_def else 0.0)

        if auto_approve_limit == 0.0 or amount_usd > auto_approve_limit:
            requires_manual_review = True

        if amount_usd > max_limit_allowed:
            overall_passed = False
            checks.append({
                "ruleId": "CATEGORY_MAX_EXCEEDED",
                "ruleName": f"{category} Category Maximum",
                "category": category,
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Amount ${amount_usd:.2f} exceeds maximum limit of ${max_limit_allowed:.2f}."
            })

    if receipt_required and not has_receipt:
        overall_passed = False
        requires_manual_review = True
        checks.append({
            "ruleId": "RECEIPT_REQUIRED_MISSING",
            "ruleName": "Itemized Receipt Requirement",
            "category": category,
            "passed": False,
            "severity": "VIOLATION",
            "message": f"Receipt is required for claims in {category}. No receipt attached."
        })

    is_within_max_limit = amount_usd <= max_limit_allowed
    is_within_auto_approve_limit = auto_approve_limit > 0 and amount_usd <= auto_approve_limit

    if not overall_passed:
        violations = [c['message'] for c in checks if not c['passed']]
        reasoning_summary = f"Policy Violation(s) Detected: {' '.join(violations)}"
    elif requires_manual_review:
        reasoning_summary = "Claim passed maximum limit checks, but requires mandatory manager review per category rules."
    else:
        reasoning_summary = f"Claim satisfies all policy rules and falls within auto-approve threshold (${auto_approve_limit:.2f})."

    return {
        "overallPassed": overall_passed,
        "requiresManualReview": requires_manual_review,
        "isWithinMaxLimit": is_within_max_limit,
        "isWithinAutoApproveLimit": is_within_auto_approve_limit,
        "maxLimitAllowed": max_limit_allowed,
        "autoApproveLimit": auto_approve_limit,
        "receiptRequired": receipt_required,
        "receiptProvided": has_receipt,
        "daysSinceExpense": days_since_expense,
        "requiresDirectorApprovalForAge": requires_director_approval_for_age,
        "checks": checks,
        "reasoningSummary": reasoning_summary
    }
