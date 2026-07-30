"""Document-type classification — keyword scoring over :class:`KnowledgeSourceType`, never a model.

Explicitly out of scope (per the task): training a classifier. What this is instead is a
deterministic scorer — each source type has a small set of representative terms, every term's
occurrences in the text are counted, and the type with the highest normalized score wins, provided
it clears a minimum signal threshold. Below that threshold the answer is
:attr:`KnowledgeSourceType.OTHER` — a document a human uploads without keying its type stays
retrievable rather than being silently mis-labelled with whatever type happened to score a lone
incidental match.

This is deliberately weaker than a trained model, and that is the point: a knowledge source's
declared ``source_type`` at upload time is authoritative when given (see M8's ingestion contract);
this classifier exists only for the case where it was not, as a best-effort default a human reviewer
can always override. Nothing downstream treats its output as ground truth.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Mapping

from app.ai.core.enums import KnowledgeSourceType

# One word/phrase per line where it helps readability; matched case-insensitively as whole words, so
# "tax" does not also match "taxonomy" or "syntax".
_KEYWORDS: Mapping[KnowledgeSourceType, tuple[str, ...]] = {
    KnowledgeSourceType.TRAVEL_POLICY: (
        "travel policy", "airfare", "per diem", "mileage", "flight class", "layover",
        "business travel", "travel expense",
    ),
    KnowledgeSourceType.MEDICAL_POLICY: (
        "medical policy", "health insurance", "prescription", "co-pay", "deductible",
        "medical claim", "physician",
    ),
    KnowledgeSourceType.FINANCE_POLICY: (
        "finance policy", "general ledger", "cost centre", "cost center", "budget approval",
        "expense report", "accounts payable",
    ),
    KnowledgeSourceType.COUNTRY_POLICY: (
        "country policy", "local regulation", "statutory requirement", "jurisdiction",
        "country-specific",
    ),
    KnowledgeSourceType.EMPLOYEE_HANDBOOK: (
        "employee handbook", "code of conduct", "onboarding", "probation period",
        "performance review", "leave policy",
    ),
    KnowledgeSourceType.VENDOR_CONTRACT: (
        "vendor contract", "master service agreement", "statement of work", "termination clause",
        "governing law", "indemnification",
    ),
    KnowledgeSourceType.VENDOR_MANUAL: (
        "vendor manual", "user guide", "installation instructions", "product manual",
        "troubleshooting",
    ),
    KnowledgeSourceType.HISTORICAL_CLAIM: (
        "claim number", "claim status", "reimbursement amount", "approved amount",
        "claim submitted",
    ),
    KnowledgeSourceType.HISTORICAL_DECISION: (
        "decision rationale", "approved by", "rejected by", "escalated to", "final decision",
    ),
    KnowledgeSourceType.REVIEWER_NOTE: (
        "reviewer note", "internal note", "flagged for review", "manager comment",
        "review comment",
    ),
    KnowledgeSourceType.FRAUD_INVESTIGATION: (
        "fraud investigation", "suspicious activity", "duplicate submission", "fraud alert",
        "investigation findings",
    ),
    KnowledgeSourceType.TAX_RULE: (
        "tax rule", "tax rate", "withholding tax", "value added tax", "vat rate",
        "taxable benefit",
    ),
    KnowledgeSourceType.GOVERNMENT_GUIDELINE: (
        "government guideline", "regulatory guidance", "statutory guideline",
        "official gazette", "ministry of",
    ),
    KnowledgeSourceType.RECEIPT: (
        "receipt", "subtotal", "total due", "thank you for your purchase", "cashier",
    ),
    KnowledgeSourceType.INVOICE: (
        "invoice number", "invoice date", "bill to", "payment terms", "remit to", "amount due",
    ),
    KnowledgeSourceType.TRAINING_DOCUMENT: (
        "training material", "learning objective", "course outline", "module 1", "quiz",
    ),
    KnowledgeSourceType.FAQ: (
        "frequently asked questions", "faq", "q:", "a:",
    ),
    KnowledgeSourceType.POLICY: (
        "policy", "shall", "must comply", "effective date", "policy owner",
    ),
}

# The lowest normalized score (matched keyword occurrences per 1,000 words) that is trusted as a
# real signal rather than one incidental mention. Chosen so a single stray word in a long document
# cannot swing the classification, while a genuinely on-topic document — which repeats its own
# vocabulary — clears it easily.
MIN_SCORE = 0.5

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def classify(text: str) -> KnowledgeSourceType:
    """Best-effort :class:`KnowledgeSourceType`, or :attr:`OTHER` when no type has enough signal."""
    if not text or not text.strip():
        return KnowledgeSourceType.OTHER

    word_count = max(1, len(_WORD.findall(text)))
    lowered = text.lower()
    scores = scores_for(lowered, word_count=word_count)
    if not scores:
        return KnowledgeSourceType.OTHER

    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    return best_type if best_score >= MIN_SCORE else KnowledgeSourceType.OTHER


def scores_for(lowered_text: str, *, word_count: int) -> Counter[KnowledgeSourceType]:
    """Per-type normalized scores, exposed separately so a caller can see the runner-up too —
    useful for the governance/metrics view of "how confident was this classification"."""
    scores: Counter[KnowledgeSourceType] = Counter()
    for source_type, keywords in _KEYWORDS.items():
        occurrences = sum(lowered_text.count(keyword) for keyword in keywords)
        if occurrences:
            scores[source_type] = (occurrences / word_count) * 1000
    return scores


__all__ = ["MIN_SCORE", "classify", "scores_for"]
