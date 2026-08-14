"""Policy rule service.

Owns the reference ruleset: reading the active/effective rules and publishing changes as new
versions. Every publish is audited with a before/after snapshot, so "who changed the meal cap, when,
and from what" is answerable from the audit trail alone.

This service stores and serves rules. The policy engine evaluates the typed fields and the
``conditions`` / ``actions`` payloads returned by :meth:`rules_for_engine`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.errors import NotFoundError, ValidationError
from app.models.candidate_rule import CandidatePolicyRule
from app.models.enums import AuditAction, AuditEntity
from app.models.policy import PolicyRule
from app.repositories.policy_rule_repository import PolicyRuleRepository
from app.services.audit_service import AuditService
from app.services.mappers import policy_rule_to_dict

logger = get_logger(__name__)

# Derives a stable rule code from a category name when a caller supplies rules without one
# (the legacy PUT payload has no identity field).
_CODE_SUFFIX = "STANDARD"


def _code_for_category(category: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in category.upper())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return f"{slug.strip('_')}_{_CODE_SUFFIX}"


def _published_special_rules(candidate: CandidatePolicyRule) -> list[str]:
    """Fold a candidate's exclusions and document requirements into ``special_rules``.

    ``policy_rules`` has no dedicated column for either, and they are the two things a claimant
    most needs to see. Prefixing keeps them identifiable without a schema change, and means
    approving a candidate never drops a clause the reviewer saw.
    """
    merged: list[str] = [str(item) for item in (candidate.special_rules or []) if item]
    merged += [f"Not reimbursable: {item}" for item in (candidate.exclusions or []) if item]
    merged += [f"Required document: {item}" for item in (candidate.required_documents or []) if item]

    seen: set[str] = set()
    deduped: list[str] = []
    for item in merged:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            deduped.append(item)
    return deduped


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed >= 0 else None


class PolicyRuleService:
    def __init__(
        self, policy_rule_repository: PolicyRuleRepository, audit_service: AuditService
    ) -> None:
        self._rules = policy_rule_repository
        self._audit = audit_service

    # --- reads ---------------------------------------------------------------

    def list_active(self) -> Sequence[PolicyRule]:
        return self._rules.list_active()

    def list_effective(self, on_date: Optional[date] = None) -> Sequence[PolicyRule]:
        """Rules in force on ``on_date`` — how a historical claim's evaluation is reproduced."""
        return self._rules.list_effective(on_date)

    def list_active_as_dicts(self) -> list[dict[str, Any]]:
        """Active ruleset in the legacy ``PolicyRuleDefinition`` wire shape."""
        return [policy_rule_to_dict(rule) for rule in self._rules.list_active()]

    def rules_for_engine(self, on_date: Optional[date] = None) -> list[dict[str, Any]]:
        """Effective ruleset in the shape ``policy_engine.evaluate_expense_policy`` consumes."""
        return [policy_rule_to_dict(rule) for rule in self._rules.list_effective(on_date)]

    def get_versions(self, code: str) -> Sequence[PolicyRule]:
        versions = self._rules.list_versions(code)
        if not versions:
            raise NotFoundError("PolicyRule", code)
        return versions

    # --- writes --------------------------------------------------------------

    def replace_ruleset(
        self, payload: Sequence[dict[str, Any]], *, actor: Actor,
        effective_date: Optional[date] = None,
    ) -> list[PolicyRule]:
        """Publish a complete new ruleset (the semantics of ``PUT /policy-rules``).

        Rules present in ``payload`` are published as a new version of their code; rules absent
        from it are retired. Nothing is deleted or edited in place, so the superseded ruleset stays
        fully queryable for historical claims.
        """
        if not isinstance(payload, (list, tuple)):
            raise ValidationError("Policy rules payload must be a list.")

        as_of = effective_date or date.today()
        before = self.list_active_as_dicts()
        submitted_codes: set[str] = set()
        published: list[PolicyRule] = []

        for index, raw in enumerate(payload):
            if not isinstance(raw, dict):
                raise ValidationError(
                    f"Policy rule at index {index} must be an object.",
                    details={"index": index},
                )
            category = (raw.get("category") or "").strip()
            if not category:
                raise ValidationError(
                    f"Policy rule at index {index} is missing 'category'.",
                    details={"index": index, "field": "category"},
                )

            code = (raw.get("code") or _code_for_category(category)).strip()
            submitted_codes.add(code)

            max_amount = raw.get("maxAmountUSD")
            # ``maxAmountUSD`` is a number-or-prose union in the wire contract.
            expense_limit = _decimal_or_none(max_amount)
            limit_expression = (
                str(max_amount) if expense_limit is None and max_amount not in (None, "") else None
            )

            special_rules = raw.get("specialRules") or []
            if not isinstance(special_rules, list):
                raise ValidationError(
                    f"'specialRules' for '{category}' must be a list.",
                    details={"index": index, "field": "specialRules"},
                )

            published.append(
                self._rules.publish_version(
                    code=code,
                    name=(raw.get("name") or f"{category} Policy").strip(),
                    category=category,
                    effective_date=as_of,
                    created_by_sub=actor.sub,
                    is_active=bool(raw.get("isActive", True)),
                    description=raw.get("description"),
                    country=(raw.get("country") or None),
                    currency=(raw.get("currency") or "USD").upper()[:3],
                    grade_tier=raw.get("gradeTier") or "All Staff",
                    expense_limit=expense_limit,
                    limit_expression=limit_expression,
                    auto_approve_limit=_decimal_or_none(raw.get("autoApproveLimitUSD")),
                    receipt_required_above=_decimal_or_none(
                        raw.get("receiptRequiredAboveUSD")
                    ),
                    requires_pre_approval=bool(raw.get("requiresPreApproval", False)),
                    priority=int(raw.get("priority") or 100),
                    conditions=raw.get("conditions"),
                    actions=raw.get("actions"),
                    special_rules=special_rules,
                )
            )

        retired = self._retire_absent(submitted_codes, on_date=as_of)

        self._audit.record(
            actor=actor,
            action=AuditAction.POLICY_UPDATE,
            entity_type=AuditEntity.POLICY_RULE,
            entity_id="policy_rules",
            details=(
                f"Published {len(published)} policy rule version(s); "
                f"retired {retired} rule(s) absent from the submitted set."
            ),
            before={"rules": before},
            after={"rules": [policy_rule_to_dict(rule) for rule in published]},
        )
        logger.info(
            "policy_rules.replaced",
            extra={"published": len(published), "retired": retired},
        )
        return published

    def publish_single_rule(
        self,
        candidate: CandidatePolicyRule,
        *,
        actor: Actor,
        effective_date: Optional[date] = None,
    ) -> PolicyRule:
        """Publish one AI-extracted candidate as the next version of its rule code.

        Unlike ``replace_ruleset``, this does not retire any other rules — it only
        publishes a single new version, carrying full provenance from the candidate.
        """
        as_of = effective_date or date.today()
        code = (candidate.code or _code_for_category(candidate.category)).strip()

        rule = self._rules.publish_version(
            code=code,
            name=candidate.name,
            category=candidate.category,
            effective_date=as_of,
            created_by_sub=actor.sub,
            description=candidate.description,
            country=candidate.country or None,
            currency=(candidate.currency or "USD").upper()[:3],
            grade_tier=candidate.grade_tier or "All Staff",
            expense_limit=candidate.expense_limit,
            limit_expression=candidate.limit_expression,
            auto_approve_limit=candidate.auto_approve_limit,
            receipt_required_above=candidate.receipt_required_above,
            requires_pre_approval=candidate.requires_pre_approval,
            priority=candidate.priority,
            conditions=candidate.conditions,
            actions=candidate.actions,
            special_rules=_published_special_rules(candidate),
            # provenance
            source_document_id=candidate.document_id,
            source_page_number=candidate.source_page_number,
            source_chunk_id=candidate.source_chunk_id,
            extracted_by=candidate.extracted_by,
        )

        self._audit.record(
            actor=actor,
            action=AuditAction.POLICY_UPDATE,
            entity_type=AuditEntity.POLICY_RULE,
            entity_id=rule.id,
            details=(
                f"Published AI-extracted rule '{rule.name}' ({rule.code} v{rule.version}) "
                f"from document page {candidate.source_page_number}."
            ),
            after={"rules": [policy_rule_to_dict(rule)]},
        )
        logger.info(
            "policy_rules.single_published",
            extra={"code": rule.code, "version": rule.version, "category": rule.category},
        )
        return rule

    def _retire_absent(self, submitted_codes: set[str], *, on_date: date) -> int:
        """Deactivate active rules whose code was not part of the submitted ruleset."""
        retired = 0
        for rule in list(self._rules.list_active()):
            if rule.code not in submitted_codes:
                self._rules.deactivate(rule, on_date=on_date)
                retired += 1
        return retired
