"""Policy rule extraction: what a Finance reviewer is guaranteed to receive.

The extractor's output is reviewed by a human who approves or edits each candidate without
reopening the source PDF. These tests pin the three promises that makes possible:

1. **Nothing is silently lost** — long pages are windowed rather than truncated, unparseable
   model output is discarded loudly, and clauses that resist structuring survive verbatim.
2. **Every candidate is self-contained** — citation, section, page, conditions, actions,
   exclusions and required documents travel with the rule.
3. **Uncertainty is reported, never guessed** — bad values are dropped and explained in
   ``review_notes`` instead of being coerced into something plausible.

No provider is contacted: a stub client returns canned responses, which is also how the
malformed-output cases are reachable at all.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import pytest

from app.ai.core.config import ai_settings
from app.ai.extraction.policy_rule_extractor import RULE_CATEGORIES, PolicyRuleExtractor, _loads_lenient


@dataclass
class FakeChunk:
    """The subset of ``KnowledgeChunk`` the extractor reads."""

    content: str
    page_number: Optional[int] = 1
    chunk_index: int = 0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    section: Optional[str] = None
    heading_path: Optional[list] = None


class FakeProvider:
    """Bedrock-shaped stub: records prompts, replays canned responses."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses) or ['{"rules": []}']
        self.prompts: list[str] = []

    def generate_text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # The last response repeats, so a windowed page needs only one canned reply.
        index = min(len(self.prompts) - 1, len(self._responses) - 1)
        return self._responses[index]


@contextmanager
def captured_warnings():
    """Collect the extractor's own warnings.

    A handler attached directly to the named logger, and re-enabling it, for the two reasons
    ``test_ai_vector_store.captured_logs`` documents at length: ``configure_logging`` strips the
    root handler pytest installs, and the Alembic run in the session fixtures leaves every
    already-imported logger with ``disabled = True``.
    """
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("app.ai.extraction.policy_rule_extractor")
    handler = Collector(level=logging.WARNING)
    previous_level, previously_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previously_disabled


def run_extract(*responses: str, chunks: Optional[list[FakeChunk]] = None) -> list:
    client = FakeProvider(*responses)
    extractor = PolicyRuleExtractor(model="test-model", provider="bedrock", client=client)
    return extractor.extract(
        chunks or [FakeChunk(content="Some policy text about meals.", section="3.1 Meals")],
        document_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
    )


def rule(**overrides: Any) -> str:
    """One well-formed rule in the response envelope, with per-test overrides."""
    payload = {
        "name": "Domestic Meal Daily Cap",
        "category": "meals",
        "description": "Up to USD 75 per day for meals on domestic travel.",
        "source_text": "Employees on domestic travel may claim up to USD 75 per day for meals.",
        "source_section": "3.1 Meals",
        "expense_limit": 75,
        "receipt_required_above": 25,
        "requires_pre_approval": False,
        "currency": "USD",
        "conditions": {"travel_type": "domestic"},
        "actions": {"receipt_required": True},
        "special_rules": ["Claims must be submitted within 30 days."],
        "exclusions": ["Alcoholic beverages are not reimbursable."],
        "required_documents": ["Itemised meal receipt above USD 25"],
        "field_confidence": {"expense_limit": 0.98},
        "overall_confidence": 0.95,
        "review_notes": None,
    }
    payload.update(overrides)
    return json.dumps({"rules": [payload]})


# --- the reviewer's payload --------------------------------------------------


def test_a_candidate_carries_everything_needed_to_review_it_without_the_document():
    (candidate,) = run_extract(rule())

    assert candidate.source_text.startswith("Employees on domestic travel")
    assert candidate.source_section == "3.1 Meals"
    assert candidate.source_page_number == 1
    assert candidate.conditions == {"travel_type": "domestic"}
    assert candidate.actions == {"receipt_required": True}
    assert candidate.special_rules == ["Claims must be submitted within 30 days."]
    assert candidate.exclusions == ["Alcoholic beverages are not reimbursable."]
    assert candidate.required_documents == ["Itemised meal receipt above USD 25"]
    assert candidate.field_confidence == {"expense_limit": 0.98}
    assert candidate.overall_confidence == Decimal("0.950")
    assert candidate.review_notes is None
    assert candidate.needs_review is False


def test_monetary_limits_and_thresholds_survive_the_models_formatting():
    """Models leak "$" and "1,200.00" into numeric fields; the amount must still land."""
    (candidate,) = run_extract(
        rule(expense_limit="$1,200.00", receipt_required_above="USD 25", auto_approve_limit="50")
    )
    assert candidate.expense_limit == Decimal("1200.00")
    assert candidate.receipt_required_above == Decimal("25")
    assert candidate.auto_approve_limit == Decimal("50")


def test_a_receipt_threshold_stated_only_in_actions_reaches_the_typed_column():
    """The policy engine reads the column, not the JSON blob — the two must not diverge."""
    (candidate,) = run_extract(
        rule(receipt_required_above=None, actions={"receipt_required_above": 40})
    )
    assert candidate.receipt_required_above == Decimal("40")


def test_the_section_falls_back_to_the_chunks_own_heading():
    (candidate,) = run_extract(
        rule(source_section=None),
        chunks=[FakeChunk(content="text", section="4.2 International Travel")],
    )
    assert candidate.source_section == "4.2 International Travel"


def test_prose_limits_are_kept_rather_than_forced_into_a_number():
    (candidate,) = run_extract(
        rule(expense_limit=None, limit_expression="Actual cost with manager approval")
    )
    assert candidate.expense_limit is None
    assert candidate.limit_expression == "Actual cost with manager approval"


def test_a_reference_based_limit_survives_verbatim_without_a_number():
    """A variable/formula-based limit (no fixed policy number) must reach limit_expression."""
    expression = "150% of the standard government per-diem for the destination city, published annually by Finance"
    (candidate,) = run_extract(rule(expense_limit=None, limit_expression=expression))
    assert candidate.expense_limit is None
    assert candidate.limit_expression == expression
    assert candidate.review_notes is None


def test_a_limit_expression_longer_than_the_old_120_char_cap_is_not_truncated():
    expression = (
        "At the mileage rate published quarterly by Finance in the Global Travel Portal, "
        "which is updated to match the IRS standard mileage rate whenever it changes"
    )
    assert len(expression) > 120
    (candidate,) = run_extract(rule(expense_limit=None, limit_expression=expression))
    assert candidate.limit_expression == expression


def test_two_numbers_in_one_field_are_not_glued_into_a_fabricated_amount():
    """Stripping non-digits and concatenating would turn "2024" + "0.67" into 20240.67."""
    (candidate,) = run_extract(
        rule(
            expense_limit="Reimbursed at the rate last revised in 2024 to $0.67 per mile",
            limit_expression=None,
        )
    )
    assert candidate.expense_limit is None


def test_a_multi_word_amount_with_units_still_parses():
    """The fix must not reject a legitimate amount just because it has several words."""
    (candidate,) = run_extract(rule(expense_limit="USD 2,000 per employee per year"))
    assert candidate.expense_limit == Decimal("2000")


# --- uncertainty is reported, not guessed ------------------------------------


def test_an_invalid_country_is_dropped_and_explained_rather_than_truncated():
    """Truncating "India" to "IN" is luck; "Ireland" would silently become the wrong country."""
    (candidate,) = run_extract(rule(country="India"))
    assert candidate.country is None
    assert "not a valid ISO" in candidate.review_notes
    assert candidate.needs_review is True


def test_a_valid_alpha_2_country_is_kept():
    (candidate,) = run_extract(rule(country="in"))
    assert candidate.country == "IN"
    assert candidate.review_notes is None


def test_a_missing_currency_is_left_null_and_flagged():
    """A generic '$' with no ISO code must not be silently turned into an assumed USD."""
    (candidate,) = run_extract(rule(currency=None))
    assert candidate.currency is None
    assert "No currency was explicitly stated" in candidate.review_notes
    assert candidate.needs_review is True


def test_an_invalid_currency_is_dropped_and_left_null():
    """Truncating 'Dollars'/'$' to 3 chars would look like a plausible ISO code without being one."""
    (candidate,) = run_extract(rule(currency="Dollars"))
    assert candidate.currency is None
    assert "not a valid ISO 4217 code" in candidate.review_notes

    (candidate,) = run_extract(rule(currency="$"))
    assert candidate.currency is None
    assert "not a valid ISO 4217 code" in candidate.review_notes


def test_a_valid_explicit_currency_is_kept_without_a_note():
    (candidate,) = run_extract(rule(currency="eur"))
    assert candidate.currency == "EUR"
    assert candidate.review_notes is None


def test_an_unknown_category_becomes_other_and_names_what_the_model_proposed():
    """An invented category publishes a rule the policy engine can never match."""
    (candidate,) = run_extract(rule(category="airport lounges"))
    assert candidate.category == "other"
    assert "airport lounges" in candidate.review_notes


def test_a_missing_limit_is_flagged_when_the_source_text_reads_like_it_states_one():
    (candidate,) = run_extract(
        rule(
            expense_limit=None,
            limit_expression=None,
            source_text="Employees may claim up to the standard rate for meals.",
        )
    )
    assert "reads like it states a limit" in candidate.review_notes


def test_an_uncaptured_exception_clause_is_flagged():
    (candidate,) = run_extract(
        rule(
            special_rules=[],
            exclusions=[],
            source_text="Lounge access is reimbursable, except on domestic sectors.",
        )
    )
    assert "qualifier" in candidate.review_notes


def test_a_missing_citation_is_flagged_and_the_paraphrase_is_kept_rather_than_dropped():
    (candidate,) = run_extract(rule(source_text=None))
    assert candidate.source_text == "Up to USD 75 per day for meals on domestic travel."
    assert "paraphrase" in candidate.review_notes


def test_the_models_own_review_note_leads_the_notes():
    (candidate,) = run_extract(rule(review_notes="Confirm whether L5 includes contractors."))
    assert candidate.review_notes.startswith("Confirm whether L5 includes contractors.")


def test_a_percentage_confidence_is_normalised_to_the_zero_to_one_range():
    (candidate,) = run_extract(rule(overall_confidence=95, field_confidence={"expense_limit": 98}))
    assert candidate.overall_confidence == Decimal("0.950")
    assert candidate.field_confidence == {"expense_limit": 0.98}


def test_low_confidence_marks_a_candidate_for_review():
    (candidate,) = run_extract(rule(overall_confidence=0.4))
    assert candidate.needs_review is True


# --- nothing is silently lost ------------------------------------------------


def test_a_long_page_is_windowed_so_no_paragraph_is_dropped():
    """The previous implementation truncated at 12k characters, losing every later rule."""
    paragraphs = [f"Clause {i} states an obligation. " + ("Filler text. " * 8) for i in range(200)]
    windows = PolicyRuleExtractor._split_into_windows("\n\n".join(paragraphs))

    assert len(windows) > 1
    seen = " ".join(windows)
    for i in range(200):
        assert f"Clause {i} states" in seen, f"lost clause {i}"


def test_windows_overlap_so_a_rule_on_a_boundary_is_seen_whole():
    paragraphs = [f"Clause {i}. " + ("Filler text. " * 8) for i in range(200)]
    windows = PolicyRuleExtractor._split_into_windows("\n\n".join(paragraphs))

    # The guarantee: each window after the first re-states the tail of the previous one.
    for earlier, later in zip(windows, windows[1:]):
        assert later[:100] in earlier, "windows do not overlap"


def test_the_duplicates_that_overlap_produces_are_collapsed():
    duplicated = rule()
    candidates = run_extract(duplicated, duplicated)
    assert len(candidates) == 1


def test_dedupe_keeps_the_fuller_copy_of_a_rule_seen_twice():
    """Overlap can show a fragment in one window and the whole clause in the next."""
    fragment = json.loads(rule(special_rules=[], exclusions=[], actions={}))
    whole = json.loads(rule())
    kept = PolicyRuleExtractor._dedupe([fragment["rules"][0], whole["rules"][0]])
    assert len(kept) == 1
    assert kept[0]["exclusions"] == ["Alcoholic beverages are not reimbursable."]


def test_two_rules_from_one_page_both_become_candidates():
    """Splitting a paragraph into independent rules is the model's job; keeping both is ours."""
    payload = json.loads(rule())["rules"]
    second = dict(payload[0], name="Senior Grade Meal Cap", expense_limit=110, grade_tier="L5")
    candidates = run_extract(json.dumps({"rules": [payload[0], second]}))

    assert [c.name for c in candidates] == ["Domestic Meal Daily Cap", "Senior Grade Meal Cap"]
    assert [c.expense_limit for c in candidates] == [Decimal("75"), Decimal("110")]


# --- coverage: the whole response must survive, not just its first few rules -----


def test_a_response_covering_many_sections_is_not_truncated_to_the_first_few_rules():
    """The model's own scanning behavior can only be checked live, but the deterministic pipeline
    that turns its JSON into candidates must never itself cap or drop the tail of a long list —
    this guards against exactly that kind of regression in this file, independent of any one
    policy's sections or wording.
    """
    base = json.loads(rule())["rules"][0]
    many_sections = [
        {
            **base,
            "name": f"Section {i} Rule",
            "category": RULE_CATEGORIES[i % len(RULE_CATEGORIES)],
            "source_text": f"Section {i} states its own distinct obligation, numbered {i}.",
            "description": f"Distinct rule {i}, from a section late enough to be dropped by a cap.",
            "expense_limit": 10 + i,
        }
        for i in range(1, 13)
    ]
    candidates = run_extract(json.dumps({"rules": many_sections}))

    assert len(candidates) == 12
    assert {c.name for c in candidates} == {f"Section {i} Rule" for i in range(1, 13)}
    last = next(c for c in candidates if c.name == "Section 12 Rule")
    assert last.expense_limit == Decimal("22")


# --- provider output that is not clean JSON ----------------------------------


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param("{body}", id="bare"),
        pytest.param("```json\n{body}\n```", id="fenced"),
        pytest.param("```\n{body}\n```", id="unlabelled-fence"),
        pytest.param("Here are the rules:\n{body}\nHope that helps.", id="prose-around"),
    ],
)
def test_the_response_is_parsed_through_the_wrappers_models_actually_emit(wrapper):
    (candidate,) = run_extract(wrapper.format(body=rule()))
    assert candidate.name == "Domestic Meal Daily Cap"


def test_a_trailing_comma_does_not_cost_the_whole_page():
    (candidate,) = run_extract(rule().replace("}]}", "},]}"))
    assert candidate.name == "Domestic Meal Daily Cap"


def test_unparseable_output_yields_nothing_and_is_logged():
    """Silence here reads as "this policy has no rules", which is the worst failure mode."""
    with captured_warnings() as records:
        assert run_extract("I'm sorry, I can't help with that.") == []
    assert any(record.getMessage() == "ai.extraction.unparseable_response" for record in records)


def test_a_provider_error_on_one_page_does_not_abort_the_document():
    class ExplodingOnFirstPage:
        def __init__(self) -> None:
            self.calls = 0

        def generate_text(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Bedrock throttled this request")
            return rule()

    extractor = PolicyRuleExtractor(
        model="test-model", provider="bedrock", client=ExplodingOnFirstPage()
    )
    candidates = extractor.extract(
        [FakeChunk(content="page one text", page_number=1),
         FakeChunk(content="page two text", page_number=2, chunk_index=1)],
        document_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
    )
    assert len(candidates) == 1
    assert candidates[0].source_page_number == 2


def test_a_rule_without_a_name_or_category_is_discarded():
    assert run_extract(json.dumps({"rules": [{"category": "meals"}, {"name": "Nameless"}]})) == []


def test_a_bare_rule_object_without_the_rules_wrapper_is_still_accepted():
    payload = json.loads(rule())["rules"][0]
    (candidate,) = run_extract(json.dumps(payload))
    assert candidate.name == "Domestic Meal Daily Cap"


@pytest.mark.parametrize("text", ["", "   ", "not json at all", "[1, 2, 3]"])
def test_lenient_parsing_never_raises(text):
    assert PolicyRuleExtractor._parse_rules(text) == []


def test_loads_lenient_returns_none_when_there_is_no_json():
    assert _loads_lenient("no json here") is None


# --- the prompt itself -------------------------------------------------------


def test_the_prompt_carries_the_page_text_and_its_section():
    client = FakeProvider(rule())
    extractor = PolicyRuleExtractor(model="test-model", provider="bedrock", client=client)
    extractor.extract(
        [FakeChunk(content="Meals are capped at USD 75.", page_number=7, section="3.1 Meals")],
        document_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
    )

    prompt = client.prompts[0]
    assert "Meals are capped at USD 75." in prompt
    assert "Page 7" in prompt
    assert "Section: 3.1 Meals" in prompt
    assert "<<" not in prompt, "an unsubstituted placeholder reached the model"


def test_the_prompt_asks_for_every_field_the_reviewer_depends_on():
    prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    for field_name in (
        "source_text", "source_section", "conditions", "actions", "special_rules",
        "exclusions", "required_documents", "field_confidence", "overall_confidence",
        "review_notes",
    ):
        assert f'"{field_name}"' in prompt, f"the prompt never asks for {field_name}"


def test_the_prompt_warns_against_bias_and_invented_values():
    """Regression guard: these instructions must survive future prompt edits."""
    prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    assert "None of it is real" in prompt, "the anti-anchoring instruction was dropped"
    assert "GUIDELINES ARE NOT CONDITIONS" in prompt, "the advisory-vs-condition instruction was dropped"
    assert "NOT pre-approval" in prompt, "the pre-approval vs manual-review distinction was dropped"
    assert "null rather than guessing" in prompt, "the currency-must-stay-null instruction was dropped"


def test_the_prompt_closes_the_finance_review_gaps():
    """Regression guard for v3: a real Finance review of v2's output found these three gaps —
    anchoring on the ACTIONS/CONDITIONS reference tables (not just the worked example), dropping
    the middle row of a multi-row table, and under-weighting prose-only rules versus table rows.
    """
    prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    assert "None of it is real" in prompt, "the broadened anchoring ban was dropped"
    assert "extract only the first and last row" in prompt, "the middle-of-table instruction was dropped"
    assert "IN TABLES AND IN PROSE ALIKE" in prompt, "the prose-rules-matter instruction was dropped"


def test_the_prompt_instructs_complete_whole_window_scanning():
    """Regression guard for the completeness rewrite (v2): must survive future prompt edits."""
    prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    assert "START TO FINISH" in prompt, "the whole-window scanning instruction was dropped"
    assert "KEEP DISTINCT RULES SEPARATE" in prompt, "the split/merge instruction was dropped"
    assert "NOT ONE RULE PER SECTION" in prompt, "the no-forced-rule-per-section instruction was dropped"
    assert "NEITHER VALUES NOR WHOLE RULES" in prompt, "the no-invented-rules instruction was dropped"


# --- prompt version selection -------------------------------------------------


def test_the_prompt_version_is_selectable_via_settings(monkeypatch):
    """AI_RULE_EXTRACTION_PROMPT_VERSION picks the template with no code change."""
    monkeypatch.setattr(ai_settings, "RULE_EXTRACTION_PROMPT_VERSION", "v1")
    v1_prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    assert "START TO FINISH" not in v1_prompt

    monkeypatch.setattr(ai_settings, "RULE_EXTRACTION_PROMPT_VERSION", "v2")
    v2_prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    assert "START TO FINISH" in v2_prompt
    assert "None of it is real" not in v2_prompt

    monkeypatch.setattr(ai_settings, "RULE_EXTRACTION_PROMPT_VERSION", "v3")
    v3_prompt = PolicyRuleExtractor._build_prompt("Page 1", "text", None)
    assert "START TO FINISH" in v3_prompt
    assert "None of it is real" in v3_prompt


def test_an_unknown_prompt_version_raises_clearly(monkeypatch):
    monkeypatch.setattr(ai_settings, "RULE_EXTRACTION_PROMPT_VERSION", "v99")
    with pytest.raises(ValueError, match="Unknown rule-extraction prompt version"):
        PolicyRuleExtractor._build_prompt("Page 1", "text", None)


# --- publishing --------------------------------------------------------------


def test_approving_a_candidate_does_not_drop_its_exclusions_or_document_requirements():
    """``policy_rules`` has no column for either; folding them into special_rules keeps them."""
    from app.services.policy_rule_service import _published_special_rules

    (candidate,) = run_extract(rule())
    assert _published_special_rules(candidate) == [
        "Claims must be submitted within 30 days.",
        "Not reimbursable: Alcoholic beverages are not reimbursable.",
        "Required document: Itemised meal receipt above USD 25",
    ]
