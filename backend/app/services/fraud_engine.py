"""Anomaly/fraud screening heuristics.

Category names match the fifteen-category vocabulary from migration ``0012_category_custom_fields``
(see :mod:`app.models.category` / :mod:`app.services.policy_engine`), not the original five.
"""

from typing import Dict, Any, List

def screen_for_anomalies(
    current_claim: Dict[str, Any],
    existing_claims: List[Dict[str, Any]]
) -> Dict[str, Any]:
    flags: List[Dict[str, Any]] = []
    risk_score = 0

    vendor = str(current_claim.get("merchantVendor") or "").lower().strip()
    amount = float(current_claim.get("amountUSD") or current_claim.get("amount") or 0.0)
    date = str(current_claim.get("expenseDate") or "")
    emp_id = str(current_claim.get("employeeId") or "")
    category = str(current_claim.get("category") or "Miscellaneous / Others")

    # 1. Check for Duplicate Submissions
    duplicate = next(
        (c for c in existing_claims if
         c.get("id") != current_claim.get("id") and
         c.get("employeeId") == emp_id and
         str(c.get("merchantVendor", "")).lower().strip() == vendor and
         abs(float(c.get("amountUSD", 0.0)) - amount) < 0.01 and
         str(c.get("expenseDate", "")) == date and
         c.get("status") != "Rejected"),
        None
    )

    if duplicate:
        risk_score += 50
        flags.append({
            "code": "DUPLICATE_SUBMISSION",
            "title": "Duplicate Claim Detected",
            "severity": "CRITICAL",
            "description": f"Identical claim ({duplicate.get('claimNumber')} - ${amount:.2f}) already exists for vendor '{current_claim.get('merchantVendor')}' on date {date}.",
            "evidence": f"Matches existing claim {duplicate.get('claimNumber')} (${duplicate.get('amountUSD', 0.0):.2f})"
        })

    # 2. Check for Split Transactions
    same_day_claims = [
        c for c in existing_claims if
        c.get("id") != current_claim.get("id") and
        c.get("employeeId") == emp_id and
        str(c.get("merchantVendor", "")).lower().strip() == vendor and
        str(c.get("expenseDate", "")) == date and
        c.get("status") != "Rejected"
    ]

    if same_day_claims:
        previous_total = sum(float(c.get("amountUSD", 0.0)) for c in same_day_claims)
        combined_total = previous_total + amount

        if category == "Taxi / Cab / Ride-hailing" and combined_total > 50.0:
            risk_score += 35
            flags.append({
                "code": "SPLIT_TRANSACTION",
                "title": "Suspected Split Transaction",
                "severity": "HIGH",
                "description": f"Multiple same-day claims for {current_claim.get('merchantVendor')} total ${combined_total:.2f}, exceeding $50.00 auto-approve ceiling.",
                "evidence": f"Current: ${amount:.2f} + Previous: ${previous_total:.2f}"
            })
        elif category == "Meals" and combined_total > 25.0:
            risk_score += 30
            flags.append({
                "code": "SPLIT_TRANSACTION",
                "title": "Suspected Split Meal Expense",
                "severity": "MEDIUM",
                "description": f"Multiple same-day claims for {current_claim.get('merchantVendor')} total ${combined_total:.2f}, exceeding $25 auto-approve limit.",
                "evidence": f"Combined total: ${combined_total:.2f}"
            })

    # 3. Threshold Proximity Check
    if (
        (23.50 < amount <= 25.00) or
        (47.50 < amount <= 50.00) or
        (115.00 < amount <= 120.00) or
        (242.00 < amount <= 250.00) or
        (485.00 < amount <= 500.00)
    ):
        risk_score += 15
        flags.append({
            "code": "THRESHOLD_ENGINEERING",
            "title": "Proximity to Auto-Approve / Policy Limit",
            "severity": "LOW",
            "description": f"Expense amount (${amount:.2f}) is positioned within 5% of a policy threshold limit.",
            "evidence": f"Amount ${amount:.2f} is suspiciously close to policy threshold ceiling."
        })

    # 4. Unusual Spend Velocity Check
    emp_claims = [c for c in existing_claims if c.get("employeeId") == emp_id]
    if len(emp_claims) > 3:
        avg_amount = sum(float(c.get("amountUSD", 0.0)) for c in emp_claims) / len(emp_claims)
        if amount > avg_amount * 3.5 and amount > 150.0:
            risk_score += 20
            flags.append({
                "code": "HIGH_SPEND_VELOCITY",
                "title": "Unusual Expense Velocity",
                "severity": "MEDIUM",
                "description": f"Claim amount (${amount:.2f}) is 3.5x higher than employee average claim (${avg_amount:.2f}).",
                "evidence": f"Employee average claim size: ${avg_amount:.2f}"
            })

    # 5. Vendor Category Mismatch
    is_uber_taxi = "uber" in vendor or "lyft" in vendor or "taxi" in vendor
    if is_uber_taxi and category == "Meals":
        risk_score += 25
        flags.append({
            "code": "VENDOR_CATEGORY_MISMATCH",
            "title": "Vendor Category Mismatch",
            "severity": "MEDIUM",
            "description": f"Rideshare vendor '{current_claim.get('merchantVendor')}' was categorized as '{category}'.",
            "evidence": f"Vendor type: Rideshare vs Category: {category}"
        })

    if risk_score >= 70:
        risk_level = "CRITICAL"
        recommended_action = "INVESTIGATE_FRAUD"
    elif risk_score >= 40:
        risk_level = "HIGH"
        recommended_action = "FINANCE_AUDIT"
    elif risk_score >= 15:
        risk_level = "MEDIUM"
        recommended_action = "MANAGER_REVIEW"
    else:
        risk_level = "LOW"
        recommended_action = "AUTO_APPROVE"

    is_flagged = len(flags) > 0 and risk_score >= 15

    if not flags:
        rationale = "No anomaly flags or suspicious patterns detected. Normal spend pattern."
    else:
        rationale = f"Detected {len(flags)} anomaly flag(s): {', '.join(f['title'] for f in flags)}."

    return {
        "riskScore": risk_score,
        "isFlagged": is_flagged,
        "riskLevel": risk_level,
        "flags": flags,
        "rationale": rationale,
        "recommendedAction": recommended_action
    }
