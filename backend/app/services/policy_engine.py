"""Database-driven expense policy evaluation.

The policy table is the source of truth.  Numeric limits and category-specific behaviour are read
from the typed rule columns plus the rule's ``conditions``/``actions`` JSON.  The evaluator keeps
the existing report shape and rule identifiers so existing API consumers remain compatible.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence


def _number(value: Any) -> Optional[float]:
    """Convert a configured numeric value without inventing a fallback."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _configured_number(mapping: dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        if key in mapping:
            return _number(mapping.get(key))
    return None


#: EmployeeGrade wire value -> GradeBand wire value. A fixed business mapping, not a database
#: table — see doc/travel-policy-rules.md. Grades not covered here have no band and therefore
#: never match a band-scoped ClaimPolicyRule (an unrecognized grade is a data problem, not a
#: reason to guess a band).
GRADE_TO_BAND: dict[str, str] = {
    "VP": "Band 1",
    "Director": "Band 1",
    "L5": "Band 2",
    "L4": "Band 3",
    "L3": "Band 3",
    "L2": "Band 4",
    "L1": "Band 4",
}


def _rule_id(code: str, suffix: str) -> str:
    """Use the durable database rule code as the stable report identity."""
    return f"{code}_{suffix}" if code else f"POLICY_{suffix}"


def _global_age_limit(policy_rules: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Read the global claim-age rule from its persisted action payload."""
    for rule in policy_rules:
        actions = rule.get("actions") or {}
        value = _configured_number(actions, "maxClaimAgeDays")
        if value is not None:
            return value
        conditions = rule.get("conditions") or {}
        age_condition = conditions.get("submissionAgeDays")
        if isinstance(age_condition, dict):
            value = _configured_number(age_condition, "lte", "max")
            if value is not None:
                return value
    return None


def _receipt_has_prohibited_item(extracted: Any, item_name: str) -> bool:
    """Evaluate a configured prohibited-item name against normalized receipt extraction."""
    if not isinstance(extracted, dict):
        return False

    token = "".join(ch for ch in str(item_name) if ch.isalnum()).casefold()
    prohibited = extracted.get("prohibitedItems")
    if isinstance(prohibited, (list, tuple, set)):
        if any(token == "".join(ch for ch in str(item) if ch.isalnum()).casefold()
               for item in prohibited):
            return True

    for key, value in extracted.items():
        normalized_key = "".join(ch for ch in str(key) if ch.isalnum()).casefold()
        if normalized_key in {f"has{token}", f"contains{token}", token} and bool(value):
            return True
    return False


def _special_rule_ids(code: str) -> tuple[str, str, str]:
    """Legacy report ids, selected by durable rule code rather than policy values."""
    if code.startswith("MEALS"):
        return "MEAL_MAX_EXCEEDED", "MEAL_MAX_PASS", "MEAL_REQUIRES_REVIEW"
    if code.startswith("TAXI") or code.startswith("GROUND_TRANSPORT"):
        return "TAXI_MAX_EXCEEDED", "TAXI_MAX_PASS", "TAXI_REVIEW_REQUIRED"
    if code.startswith("AIR") or code.startswith("FLIGHT"):
        return "FLIGHT_MAX_EXCEEDED", "FLIGHT_MAX_PASS", "FLIGHT_ALWAYS_MANUAL"
    if code.startswith("HOTEL") or code.startswith("LODGING"):
        return "LODGING_LIMIT_EXCEEDED", "LODGING_LIMIT_PASS", "LODGING_ALWAYS_MANUAL"
    if code.startswith("CLIENT"):
        return "CLIENT_ENT_MAX_EXCEEDED", "CLIENT_ENT_MAX_PASS", "CLIENT_ENT_ALWAYS_MANUAL"
    return "CATEGORY_MAX_EXCEEDED", "CATEGORY_MAX_PASS", "CATEGORY_REQUIRES_REVIEW"


def evaluate_expense_policy(
    claim: Dict[str, Any],
    policy_rules: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Evaluate one expense against the effective, database-backed ruleset."""
    rules: Sequence[Dict[str, Any]] = policy_rules or []
    checks: List[Dict[str, Any]] = []
    category = claim.get("category") or ""
    amount_usd = float(claim.get("amountUSD") or claim.get("amount") or 0.0)
    grade = claim.get("employeeGrade") or ""
    has_receipt = bool(claim.get("receiptAttached", False))

    expense_date_str = claim.get("expenseDate") or date.today().isoformat()
    submission_date_str = claim.get("submissionDate") or date.today().isoformat()
    try:
        exp_date = datetime.strptime(str(expense_date_str)[:10], "%Y-%m-%d").date()
        sub_date = datetime.strptime(str(submission_date_str)[:10], "%Y-%m-%d").date()
        days_since_expense = max((sub_date - exp_date).days, 0)
    except (TypeError, ValueError):
        days_since_expense = 0

    overall_passed = True
    requires_manual_review = False
    requires_director_approval_for_age = False

    # Global rules are also persisted in policy_rules, identified by their action payload.
    age_limit = _global_age_limit(rules)
    if age_limit is not None:
        if days_since_expense > age_limit:
            overall_passed = False
            requires_manual_review = True
            requires_director_approval_for_age = True
            checks.append({
                "ruleId": "AGE_LIMIT_90_DAYS",
                "ruleName": "Claim Submission Age Limit",
                "category": category,
                "passed": False,
                "severity": "VIOLATION",
                "message": (
                    f"Claim submitted {days_since_expense} days after transaction date "
                    f"(exceeds {age_limit:g} day limit)."
                ),
                "details": "Claims beyond the configured age limit require Finance approval.",
            })
        else:
            checks.append({
                "ruleId": "AGE_LIMIT_90_DAYS",
                "ruleName": "Claim Submission Age Limit",
                "category": category,
                "passed": True,
                "severity": "INFO",
                "message": (
                    f"Submitted within {days_since_expense} days of expense date "
                    f"(limit {age_limit:g} days)."
                ),
            })

    policy_def = next((rule for rule in rules if rule.get("category") == category), None)
    if policy_def is None:
        overall_passed = False
        requires_manual_review = True
        checks.append({
            "ruleId": "POLICY_RULE_MISSING",
            "ruleName": "Configured Policy Rule Required",
            "category": category,
            "passed": False,
            "severity": "VIOLATION",
            "message": f"No active database policy rule is configured for '{category}'.",
        })
        return {
            "overallPassed": overall_passed,
            "requiresManualReview": requires_manual_review,
            "isWithinMaxLimit": False,
            "isWithinAutoApproveLimit": False,
            "maxLimitAllowed": None,
            "autoApproveLimit": None,
            "receiptRequired": False,
            "receiptProvided": has_receipt,
            "daysSinceExpense": days_since_expense,
            "requiresDirectorApprovalForAge": requires_director_approval_for_age,
            "checks": checks,
            "reasoningSummary": "Policy configuration is missing; manual review is required.",
        }

    actions = policy_def.get("actions") or {}
    conditions = policy_def.get("conditions") or {}
    code = str(policy_def.get("code") or "")
    max_id, pass_id, review_id = _special_rule_ids(code)

    # Typed columns are the normal source. JSON actions can override them when a policy contains
    # richer, declarative limits such as grade-specific nightly caps.
    max_limit_allowed = _configured_number(actions, "maxAmountUSD", "maxAmountUsd")
    if max_limit_allowed is None:
        max_limit_allowed = _number(policy_def.get("maxAmountUSD"))

    grade_caps = actions.get("nightlyCapUsdByGrade")
    if isinstance(grade_caps, dict) and grade in grade_caps:
        max_limit_allowed = _number(grade_caps.get(grade))

    auto_approve_limit = _configured_number(
        actions, "autoApproveLimitUSD", "autoApproveLimitUsd", "autoApproveBelowUsd"
    )
    if auto_approve_limit is None:
        auto_approve_limit = _number(policy_def.get("autoApproveLimitUSD"))

    receipt_threshold = _configured_number(
        actions, "requireReceiptAboveUSD", "requireReceiptAboveUsd"
    )
    if receipt_threshold is None:
        receipt_threshold = _number(policy_def.get("receiptRequiredAboveUSD"))
    if "requireReceipt" in actions:
        receipt_required = bool(actions["requireReceipt"])
    else:
        receipt_required = receipt_threshold is not None and amount_usd > receipt_threshold

    if actions.get("autoApprove") is False or auto_approve_limit is None:
        requires_manual_review = True
    elif amount_usd > auto_approve_limit:
        requires_manual_review = True

    if max_limit_allowed is None:
        overall_passed = False
        requires_manual_review = True
        checks.append({
            "ruleId": _rule_id(code, "MAX_NOT_CONFIGURED"),
            "ruleName": "Maximum Amount Configuration",
            "category": category,
            "passed": False,
            "severity": "VIOLATION",
            "message": "The database policy does not contain a numeric maximum amount.",
        })
    elif amount_usd > max_limit_allowed:
        overall_passed = False
        requires_manual_review = True
        checks.append({
            "ruleId": max_id,
            "ruleName": "Configured Category Maximum",
            "category": category,
            "passed": False,
            "severity": "VIOLATION",
            "message": (
                f"Amount ${amount_usd:.2f} exceeds configured maximum "
                f"${max_limit_allowed:.2f}."
            ),
        })
    else:
        checks.append({
            "ruleId": pass_id,
            "ruleName": "Configured Category Maximum",
            "category": category,
            "passed": True,
            "severity": "INFO",
            "message": (
                f"Amount ${amount_usd:.2f} is within configured maximum "
                f"${max_limit_allowed:.2f}."
            ),
        })

    if actions.get("autoApprove") is False or (
        auto_approve_limit is not None and amount_usd > auto_approve_limit
    ):
        checks.append({
            "ruleId": review_id,
            "ruleName": "Configured Approval Threshold",
            "category": category,
            "passed": True,
            "severity": "REQUIREMENT",
            "message": "This expense requires manual review under the configured policy.",
        })

    allowed_grades = conditions.get("gradeTiers")
    if isinstance(allowed_grades, list) and allowed_grades and "*" not in allowed_grades:
        grade_restriction_id = (
            "CLIENT_ENT_GRADE_RESTRICTION"
            if code.startswith("CLIENT")
            else _rule_id(code, "GRADE_RESTRICTION")
        )
        grade_pass_id = (
            "CLIENT_ENT_GRADE_PASS"
            if code.startswith("CLIENT")
            else _rule_id(code, "GRADE_PASS")
        )
        if grade not in allowed_grades:
            overall_passed = False
            requires_manual_review = True
            checks.append({
                "ruleId": grade_restriction_id,
                "ruleName": "Configured Grade Authorization",
                "category": category,
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Grade {grade} is not authorized by the configured policy.",
            })
        else:
            checks.append({
                "ruleId": grade_pass_id,
                "ruleName": "Configured Grade Authorization",
                "category": category,
                "passed": True,
                "severity": "INFO",
                "message": f"Grade {grade} is authorized by the configured policy.",
            })

    cabin_by_grade = actions.get("cabinClassByGrade")
    if isinstance(cabin_by_grade, dict) and grade in cabin_by_grade:
        permitted = str(cabin_by_grade[grade])
        if permitted == "Economy":
            cabin_id = "FLIGHT_CLASS_L1_L4"
        elif permitted.startswith("Premium"):
            cabin_id = "FLIGHT_CLASS_L5"
        elif permitted.startswith("Business"):
            cabin_id = "FLIGHT_CLASS_VP"
        else:
            cabin_id = _rule_id(code, "CABIN_CLASS")
        checks.append({
            "ruleId": cabin_id,
            "ruleName": "Configured Cabin Class Policy",
            "category": category,
            "passed": True,
            "severity": "INFO",
            "message": f"Grade {grade} permitted class: {permitted}.",
        })

    requires_pre_approval = bool(
        actions.get("requirePreApproval", policy_def.get("requiresPreApproval", False))
    )
    if requires_pre_approval and not bool(claim.get("hasPreApproval", False)):
        overall_passed = False
        requires_manual_review = True
        checks.append({
            "ruleId": _rule_id(code, "PRE_APPROVAL_REQUIRED"),
            "ruleName": "Pre-Approval Requirement",
            "category": category,
            "passed": False,
            "severity": "VIOLATION",
            "message": "This expense required pre-approval before it was incurred.",
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
            "message": f"Receipt is required for claims in {category}. No receipt attached.",
        })

    extracted = claim.get("extractedReceipt") or {}
    for prohibited in actions.get("prohibitedItems") or []:
        if _receipt_has_prohibited_item(extracted, str(prohibited)):
            overall_passed = False
            requires_manual_review = True
            token = "".join(ch for ch in str(prohibited) if ch.isalnum()).upper()
            prohibited_id = (
                "MEAL_ALCOHOL_PROHIBITED"
                if token == "ALCOHOL"
                else _rule_id(code, f"PROHIBITED_{token}")
            )
            checks.append({
                "ruleId": prohibited_id,
                "ruleName": "Configured Non-Reimbursable Item",
                "category": category,
                "passed": False,
                "severity": "VIOLATION",
                "message": f"Receipt contains configured non-reimbursable item: {prohibited}.",
            })

    if actions.get("requireAttendees"):
        attendees = str(claim.get("attendees") or "").strip()
        minimum_length = _number(actions.get("minimumAttendeeTextLength"))
        valid_attendees = (
            len(attendees) >= minimum_length
            if minimum_length is not None
            else bool(attendees)
        )
        if not valid_attendees:
            overall_passed = False
            requires_manual_review = True
            attendees_id = (
                "CLIENT_ENT_MISSING_ATTENDEES"
                if code.startswith("CLIENT")
                else _rule_id(code, "MISSING_ATTENDEES")
            )
            checks.append({
                "ruleId": attendees_id,
                "ruleName": "Configured Attendee Requirement",
                "category": category,
                "passed": False,
                "severity": "VIOLATION",
            "message": (
                "The configured policy requires internal and external attendees "
                "and business purpose."
            ),
            })

    within_max = max_limit_allowed is not None and amount_usd <= max_limit_allowed
    within_auto = auto_approve_limit is not None and amount_usd <= auto_approve_limit
    violations = [check["message"] for check in checks if not check["passed"]]
    if violations:
        reasoning_summary = f"Policy Violation(s) Detected: {' '.join(violations)}"
    elif requires_manual_review:
        reasoning_summary = "Claim passed configured limit checks, but requires manual review."
    else:
        reasoning_summary = "Claim satisfies the configured policy rules and auto-approval threshold."

    return {
        "overallPassed": overall_passed,
        "requiresManualReview": requires_manual_review,
        "isWithinMaxLimit": within_max,
        "isWithinAutoApproveLimit": within_auto,
        "maxLimitAllowed": max_limit_allowed,
        "autoApproveLimit": auto_approve_limit,
        "receiptRequired": receipt_required,
        "receiptProvided": has_receipt,
        "daysSinceExpense": days_since_expense,
        "requiresDirectorApprovalForAge": requires_director_approval_for_age,
        "checks": checks,
        "reasoningSummary": reasoning_summary,
    }


def _travel_rule_matches(
    rule: Dict[str, Any],
    *,
    category: Optional[str],
    travel_type: Optional[str],
    grade_band: Optional[str],
    duration: Optional[str],
) -> bool:
    """``ClaimPolicyRule`` scope match: each axis is either a wildcard (``None``) or exact."""

    def _axis(rule_value: Any, item_value: Any) -> bool:
        return rule_value is None or rule_value == item_value

    return (
        _axis(rule.get("category"), category)
        and _axis(rule.get("travelType"), travel_type)
        and _axis(rule.get("gradeBand"), grade_band)
        and _axis(rule.get("duration"), duration)
    )


def evaluate_travel_policy(
    items: Sequence[Dict[str, Any]],
    rules: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate every item of a claim against the database-backed local-travel ruleset.

    A separate, additive layer from :func:`evaluate_expense_policy` (which is unchanged and keeps
    matching on category alone) — see ``doc/travel-policy-rules.md``. Runs over the whole claim in
    one call (not one item at a time) because that is this engine's natural unit, even though
    today's rules happen to be independent per item.

    Verification is plain arithmetic and dictionary lookups, deliberately: an LLM has no role in
    deciding pass/fail here, only (upstream, elsewhere) in extracting the raw amount/category off
    a receipt in the first place.
    """
    rule_list: Sequence[Dict[str, Any]] = rules or []
    reports: List[Dict[str, Any]] = []

    for item in items:
        item_id = item.get("id")
        category = item.get("category")
        travel_type = item.get("travelType")
        duration = item.get("duration")
        grade = str(item.get("employeeGrade") or "")
        grade_band = GRADE_TO_BAND.get(grade)
        amount = _number(item.get("amount")) or 0.0
        currency = str(item.get("currency") or "INR")

        matches = [
            rule
            for rule in rule_list
            if _travel_rule_matches(
                rule,
                category=category,
                travel_type=travel_type,
                grade_band=grade_band,
                duration=duration,
            )
        ]

        if not matches:
            reports.append({
                "itemId": item_id,
                "overallPassed": True,
                "requiresManualReview": False,
                "eligibleAmount": amount,
                "checks": [],
                "reasoningSummary": "No local travel policy rule applies to this item.",
            })
            continue

        checks: List[Dict[str, Any]] = []
        overall_passed = True
        requires_manual_review = False
        eligible_amount = amount

        for rule in matches:
            rule_type = rule.get("ruleType")
            code = str(rule.get("code") or "")
            rule_name = rule.get("name") or "Local Travel Policy Rule"

            if rule_type == "PROHIBITED":
                overall_passed = False
                requires_manual_review = True
                eligible_amount = 0.0
                checks.append({
                    "ruleId": code or "TRAVEL_PROHIBITED",
                    "ruleName": rule_name,
                    "category": category,
                    "passed": False,
                    "severity": "VIOLATION",
                    "message": rule.get("description")
                    or f"{category} is not reimbursable for this travel type.",
                })
                continue

            if rule_type == "AMOUNT_CAP":
                rule_amount = _number(rule.get("amount"))
                rule_currency = str(rule.get("currency") or "INR")
                if rule_amount is None:
                    continue
                if rule_currency != currency:
                    requires_manual_review = True
                    checks.append({
                        "ruleId": f"{code}_CURRENCY_MISMATCH" if code else "TRAVEL_CURRENCY_MISMATCH",
                        "ruleName": rule_name,
                        "category": category,
                        "passed": True,
                        "severity": "REQUIREMENT",
                        "message": (
                            f"Item currency {currency} does not match rule currency "
                            f"{rule_currency}; amount cap could not be compared automatically."
                        ),
                    })
                    continue

                eligible_amount = min(eligible_amount, rule_amount)
                if amount > rule_amount:
                    overall_passed = False
                    requires_manual_review = True
                    checks.append({
                        "ruleId": code or "TRAVEL_AMOUNT_CAP_EXCEEDED",
                        "ruleName": rule_name,
                        "category": category,
                        "passed": False,
                        "severity": "VIOLATION",
                        "message": (
                            f"Amount {currency} {amount:.2f} exceeds configured cap "
                            f"{currency} {rule_amount:.2f}. Eligible amount: "
                            f"{currency} {min(amount, rule_amount):.2f}."
                        ),
                    })
                else:
                    checks.append({
                        "ruleId": code or "TRAVEL_AMOUNT_CAP_PASS",
                        "ruleName": rule_name,
                        "category": category,
                        "passed": True,
                        "severity": "INFO",
                        "message": (
                            f"Amount {currency} {amount:.2f} is within configured cap "
                            f"{currency} {rule_amount:.2f}."
                        ),
                    })

        violations = [check["message"] for check in checks if not check["passed"]]
        if violations:
            reasoning_summary = f"Local travel policy violation(s): {' '.join(violations)}"
        elif requires_manual_review:
            reasoning_summary = (
                "Local travel policy checks passed but require manual review."
            )
        else:
            reasoning_summary = "Item satisfies the configured local travel policy rules."

        reports.append({
            "itemId": item_id,
            "overallPassed": overall_passed,
            "requiresManualReview": requires_manual_review,
            "eligibleAmount": round(eligible_amount, 2),
            "checks": checks,
            "reasoningSummary": reasoning_summary,
        })

    return reports
