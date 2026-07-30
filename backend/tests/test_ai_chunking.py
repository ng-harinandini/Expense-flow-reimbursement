"""Chunking engine tests (T004-M4).

Three layers:

1. **A shared strategy contract suite**, parametrized over all seven chunkers. The
   ``Chunker`` protocol's five clauses (no text loss, budget respected, determinism, progress,
   parent-child integrity) are asserted here for every strategy, matching the pattern
   ``test_ai_embeddings.py`` uses for provider adapters and ``test_ai_parsing.py`` uses for format
   parsers — the platform's claim of interchangeability is only credible if one suite checks it.
2. **Packing helper unit tests** — ``pack_with_overlap``, ``recursive_pack``,
   ``merge_small_trailing_chunk`` — including the progress-guard edge case that keeps overlap from
   looping forever.
3. **Strategy-specific behaviour** — what actually differentiates the seven from each other, since
   the shared suite only proves each one is *safe*, not that it does its own distinctive job.
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.chunking.base import (
    BaseChunker,
    HeuristicTokenCounter,
    RawChunk,
    merge_small_trailing_chunk,
    pack_with_overlap,
    recursive_pack,
)
from app.ai.chunking.config import build_chunking_config
from app.ai.chunking.factory import register_chunkers, resolve_chunker
from app.ai.chunking.heading import HeadingChunker
from app.ai.chunking.hybrid import HybridChunker
from app.ai.chunking.parent_child import ParentChildChunker
from app.ai.chunking.recursive import RecursiveChunker
from app.ai.chunking.semantic import SemanticChunker
from app.ai.chunking.sliding_window import SlidingWindowChunker
from app.ai.chunking.token import TokenChunker
from app.ai.core.config import AISettings
from app.ai.core.enums import ChunkStrategy
from app.ai.core.errors import AIValidationError
from app.ai.core.types import Chunk, ChunkMetadata, DocumentSection, ParsedDocument
from app.ai.interfaces.chunker import Chunker, ChunkingConfig
from app.ai.providers.embeddings.deterministic import DeterministicEmbeddingProvider
from app.ai.registry.registry import chunking_registry, reset_all_registries

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

PARAGRAPH_A = (
    "Travel expenses must be submitted within thirty days of the trip's end date, and every "
    "receipt over twenty-five dollars requires an itemized breakdown of the charges incurred."
)
PARAGRAPH_B = (
    "Meals are reimbursed at actual cost up to the daily per-diem limit for the traveler's "
    "destination city, and alcohol is never a reimbursable expense under any circumstance."
)
PARAGRAPH_C = (
    "Hotel bookings should use the corporate rate whenever one is available, and any booking "
    "above the nightly cap needs a director's written approval before the trip begins."
)
FIXTURE_TEXT = f"{PARAGRAPH_A}\n\n{PARAGRAPH_B}\n\n{PARAGRAPH_C}"

FIXTURE_SECTIONS = (
    DocumentSection(
        text=f"Travel Policy\n{PARAGRAPH_A}",
        heading="Travel Policy", level=1, heading_path=("Travel Policy",), page_number=1,
    ),
    DocumentSection(
        text=f"Meals\n{PARAGRAPH_B}",
        heading="Meals", level=1, heading_path=("Meals",), page_number=1,
    ),
    DocumentSection(
        text=f"Hotels\n{PARAGRAPH_C}",
        heading="Hotels", level=1, heading_path=("Hotels",), page_number=2,
    ),
)


def fixture_document(*, with_sections: bool = False) -> ParsedDocument:
    return ParsedDocument(
        text=FIXTURE_TEXT,
        checksum="fixture-checksum-0001",
        sections=FIXTURE_SECTIONS if with_sections else (),
        language="en",
    )


def small_config(strategy: ChunkStrategy, **overrides: object) -> ChunkingConfig:
    fields: dict[str, object] = {
        "strategy": strategy, "max_tokens": 24, "overlap_tokens": 4, "min_tokens": 1,
        "parent_max_tokens": 80, "semantic_threshold": 0.0, "sliding_stride_tokens": 8,
    }
    fields.update(overrides)
    return ChunkingConfig(**fields)


def build_chunker(strategy: ChunkStrategy) -> BaseChunker:
    if strategy is ChunkStrategy.SEMANTIC:
        return SemanticChunker(
            embedding_provider=DeterministicEmbeddingProvider(dimensions=32, version="v1")
        )
    return _CHUNKER_CLASSES[strategy]()


_CHUNKER_CLASSES: dict[ChunkStrategy, type] = {
    ChunkStrategy.RECURSIVE: RecursiveChunker,
    ChunkStrategy.TOKEN: TokenChunker,
    ChunkStrategy.SLIDING_WINDOW: SlidingWindowChunker,
    ChunkStrategy.HEADING: HeadingChunker,
    ChunkStrategy.HYBRID: HybridChunker,
    ChunkStrategy.PARENT_CHILD: ParentChildChunker,
}

ALL_STRATEGIES = (
    ChunkStrategy.RECURSIVE, ChunkStrategy.TOKEN, ChunkStrategy.SLIDING_WINDOW,
    ChunkStrategy.HEADING, ChunkStrategy.HYBRID, ChunkStrategy.PARENT_CHILD, ChunkStrategy.SEMANTIC,
)

# The two strategies whose behaviour depends on ParsedDocument.sections being populated.
SECTIONED_STRATEGIES = (ChunkStrategy.HEADING, ChunkStrategy.HYBRID)


def _significant_words(text: str) -> set[str]:
    """Lowercased words of four+ letters, so 'a'/'the' near an overlap seam don't false-positive."""
    return {w.strip(".,'").lower() for w in text.split() if len(w.strip(".,'")) >= 4}


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_all_registries()
    yield
    reset_all_registries()


# ---------------------------------------------------------------------------
# 1. Shared contract suite (Chunker protocol clauses 1-5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_chunker_satisfies_the_protocol(strategy: ChunkStrategy) -> None:
    chunker = build_chunker(strategy)
    assert isinstance(chunker, Chunker)
    assert chunker.strategy is strategy


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_no_text_loss_outside_declared_overlap(strategy: ChunkStrategy) -> None:
    """Clause 1: every significant word of the source reappears in the chunked output."""
    chunker = build_chunker(strategy)
    document = fixture_document(with_sections=strategy in SECTIONED_STRATEGIES)
    config = small_config(strategy)
    chunks = chunker.chunk(document, config=config)
    assert chunks

    # Parent-child intentionally re-splits parent text into children (contract clause 5's "stored
    # once" is about identity/storage, not about excluding children from a reconstruction check);
    # only top-level (non-child) chunks are needed to reconstruct the whole document exactly once.
    is_parent_child = strategy is ChunkStrategy.PARENT_CHILD
    relevant = [c for c in chunks if not c.is_child] if is_parent_child else chunks
    reconstructed = " ".join(c.text for c in relevant)
    missing = _significant_words(FIXTURE_TEXT) - _significant_words(reconstructed)
    assert not missing, f"{strategy.value} dropped words: {missing}"


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_budget_is_respected(strategy: ChunkStrategy) -> None:
    """Clause 2: no chunk exceeds its budget (parent_max_tokens for a parent, else max_tokens)."""
    chunker = build_chunker(strategy)
    document = fixture_document(with_sections=strategy in SECTIONED_STRATEGIES)
    config = small_config(strategy)
    chunks = chunker.chunk(document, config=config)
    parent_ids = {c.parent_id for c in chunks if c.parent_id is not None}
    for c in chunks:
        budget = config.parent_max_tokens if c.id in parent_ids else config.max_tokens
        assert c.token_count <= budget, f"{strategy.value} chunk {c.index} over budget"


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_determinism_same_input_same_ids(strategy: ChunkStrategy) -> None:
    """Clause 3: the same input yields the same chunks with the same ids."""
    chunker = build_chunker(strategy)
    document = fixture_document(with_sections=strategy in SECTIONED_STRATEGIES)
    config = small_config(strategy)
    first = chunker.chunk(document, config=config)
    second = chunker.chunk(document, config=config)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.text for c in first] == [c.text for c in second]


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_selectable_by_name_from_config(strategy: ChunkStrategy) -> None:
    """Every strategy is resolvable through the factory/registry by its config-declared name."""
    register_chunkers()
    resolved = resolve_chunker(strategy)
    assert resolved.strategy is strategy
    assert strategy.value.lower() in chunking_registry.names()


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_empty_document_yields_no_chunks(strategy: ChunkStrategy) -> None:
    chunker = build_chunker(strategy)
    document = ParsedDocument(text="   \n  ", checksum="empty-checksum")
    assert chunker.chunk(document, config=small_config(strategy)) == ()


def test_parent_child_chunks_resolve_to_their_parent() -> None:
    chunker = ParentChildChunker()
    document = fixture_document()
    config = small_config(ChunkStrategy.PARENT_CHILD, max_tokens=12, parent_max_tokens=60)
    chunks = chunker.chunk(document, config=config)
    by_id = {c.id: c for c in chunks}
    children = [c for c in chunks if c.is_child]
    assert children, "fixture should be large enough to produce at least one child"
    for child in children:
        assert child.parent_id in by_id
        parent = by_id[child.parent_id]
        assert not parent.is_child

    # Parent text is stored once: exactly one Chunk row holds each parent's full text.
    parent_texts = [c.text for c in chunks if not c.is_child]
    assert len(parent_texts) == len(set(parent_texts))


def test_chunk_rejects_a_config_declaring_a_different_strategy() -> None:
    chunker = RecursiveChunker()
    mismatched = small_config(ChunkStrategy.TOKEN)
    with pytest.raises(AIValidationError):
        chunker.chunk(fixture_document(), config=mismatched)


def test_chunk_metadata_carries_document_id_and_language() -> None:
    chunker = RecursiveChunker()
    document = fixture_document()
    config = small_config(ChunkStrategy.RECURSIVE)
    template = ChunkMetadata(department="Sales", country="US")
    chunks = chunker.chunk(document, config=config, metadata=template)
    for c in chunks:
        assert c.metadata.document_id is not None
        assert c.metadata.department == "Sales"
        assert c.metadata.country == "US"
        assert c.metadata.language == "en"
        assert c.metadata.checksum is not None


def test_chunk_ids_change_when_document_id_metadata_differs() -> None:
    """Two tenants (or two versions) chunking identical text must not collide on chunk id."""
    chunker = RecursiveChunker()
    document = fixture_document()
    config = small_config(ChunkStrategy.RECURSIVE)
    meta_a = ChunkMetadata(document_id=uuid.uuid4())
    meta_b = ChunkMetadata(document_id=uuid.uuid4())
    chunks_a = chunker.chunk(document, config=config, metadata=meta_a)
    chunks_b = chunker.chunk(document, config=config, metadata=meta_b)
    assert {c.id for c in chunks_a}.isdisjoint({c.id for c in chunks_b})


def test_chunk_ids_are_stable_without_explicit_document_id() -> None:
    """No metadata supplied at all still produces stable, reproducible ids across calls."""
    chunker = RecursiveChunker()
    document = fixture_document()
    config = small_config(ChunkStrategy.RECURSIVE)
    first = chunker.chunk(document, config=config)
    second = chunker.chunk(document, config=config)
    assert [c.id for c in first] == [c.id for c in second]


# ---------------------------------------------------------------------------
# 2. Invariant enforcement (BaseChunker's own defensive checks)
# ---------------------------------------------------------------------------


class _OversizedChunker(BaseChunker):
    """Deliberately broken strategy: emits a chunk far over budget, to exercise the guard."""

    strategy = ChunkStrategy.RECURSIVE

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        return [RawChunk(text="word " * 500, index=0)]


def test_oversized_chunk_from_a_broken_strategy_is_caught() -> None:
    chunker = _OversizedChunker()
    config = small_config(ChunkStrategy.RECURSIVE, max_tokens=10)
    with pytest.raises(RuntimeError, match="over its"):
        chunker.chunk(fixture_document(), config=config)


def test_dangling_parent_reference_would_be_caught_if_it_occurred() -> None:
    """The validator's dangling-parent branch, exercised directly since a real strategy can never
    construct one (parent_index always names a position already in its own raw-chunk list)."""
    chunk = Chunk(
        id=uuid.uuid4(), text="child", index=0,
        strategy=ChunkStrategy.PARENT_CHILD, parent_id=uuid.uuid4(),
    )
    with pytest.raises(RuntimeError, match="absent from the same"):
        BaseChunker._validate_invariants([chunk], small_config(ChunkStrategy.PARENT_CHILD))


# ---------------------------------------------------------------------------
# 3. Packing helpers
# ---------------------------------------------------------------------------


def _word_counter(text: str) -> int:
    return len(text.split())


def test_pack_with_overlap_respects_budget_and_covers_all_units() -> None:
    units = [f"word{i}" for i in range(20)]
    packed = pack_with_overlap(units, max_tokens=5, overlap_tokens=2, count_tokens=_word_counter)
    assert packed
    for chunk in packed:
        assert _word_counter(chunk) <= 5
    all_words = {w for chunk in packed for w in chunk.split()}
    assert all_words == set(units)


def test_pack_with_overlap_carries_a_tail_into_the_next_chunk() -> None:
    units = [f"word{i}" for i in range(10)]
    packed = pack_with_overlap(units, max_tokens=4, overlap_tokens=2, count_tokens=_word_counter)
    assert len(packed) >= 2
    tail_of_first = packed[0].split()[-2:]
    head_of_second = packed[1].split()[:2]
    assert set(tail_of_first) & set(head_of_second)


def test_pack_with_overlap_never_stalls_when_overlap_covers_the_whole_window() -> None:
    """Progress guard: a lone oversized unit combined with a large overlap must still advance."""
    units = ["a-single-very-long-unit-token", "next", "another"]
    packed = pack_with_overlap(units, max_tokens=1, overlap_tokens=1, count_tokens=_word_counter)
    # Each input unit is exactly one "word" by this counter, so every unit becomes its own chunk;
    # the call must terminate (a stalled loop would hang the test under pytest's timeout/never
    # return) and account for every unit.
    assert len(packed) == len(units)


def test_pack_with_overlap_handles_empty_input() -> None:
    assert pack_with_overlap([], max_tokens=10, overlap_tokens=2, count_tokens=_word_counter) == []


def test_pack_with_overlap_zero_overlap_never_duplicates() -> None:
    units = [f"word{i}" for i in range(9)]
    packed = pack_with_overlap(units, max_tokens=3, overlap_tokens=0, count_tokens=_word_counter)
    all_words = [w for chunk in packed for w in chunk.split()]
    assert all_words == units  # no duplication, original order preserved


def test_recursive_pack_splits_an_oversized_paragraph_into_sentences() -> None:
    long_paragraph = " ".join(f"Sentence number {i} has a few words in it." for i in range(15))
    packed = recursive_pack(
        long_paragraph, max_tokens=12, overlap_tokens=2, count_tokens=_word_counter
    )
    assert len(packed) > 1
    for piece in packed:
        assert _word_counter(piece) <= 12


def test_recursive_pack_falls_back_to_words_for_an_unsplittable_sentence() -> None:
    """A single sentence longer than the budget still gets packed, at word granularity."""
    one_giant_sentence = " ".join(f"w{i}" for i in range(40))
    packed = recursive_pack(
        one_giant_sentence, max_tokens=10, overlap_tokens=1, count_tokens=_word_counter
    )
    assert len(packed) > 1
    all_words = [w for chunk in packed for w in chunk.split()]
    assert set(all_words) == {f"w{i}" for i in range(40)}


def test_recursive_pack_empty_text_yields_no_units() -> None:
    assert recursive_pack("   ", max_tokens=10, overlap_tokens=2, count_tokens=_word_counter) == []


def test_merge_small_trailing_chunk_folds_a_tiny_final_chunk_in() -> None:
    chunks = ["one two three four five", "six"]
    merged = merge_small_trailing_chunk(
        chunks, min_tokens=2, max_tokens=20, count_tokens=_word_counter
    )
    assert merged == ["one two three four five six"]


def test_merge_small_trailing_chunk_leaves_a_healthy_final_chunk_alone() -> None:
    chunks = ["one two three", "four five six"]
    merged = merge_small_trailing_chunk(
        chunks, min_tokens=2, max_tokens=20, count_tokens=_word_counter
    )
    assert merged == chunks


def test_merge_small_trailing_chunk_skips_when_merge_would_overflow_budget() -> None:
    chunks = ["one two three four five six seven eight nine ten", "eleven"]
    merged = merge_small_trailing_chunk(
        chunks, min_tokens=2, max_tokens=10, count_tokens=_word_counter
    )
    assert merged == chunks  # merging would be 11 words, over the 10-word budget


def test_merge_small_trailing_chunk_single_chunk_is_a_no_op() -> None:
    assert merge_small_trailing_chunk(
        ["only one"], min_tokens=5, max_tokens=10, count_tokens=_word_counter
    ) == ["only one"]


def test_heuristic_token_counter_matches_estimate_tokens() -> None:
    from app.ai.core.text import estimate_tokens

    counter = HeuristicTokenCounter()
    assert counter.count("Some example text for counting.") == estimate_tokens(
        "Some example text for counting."
    )


# ---------------------------------------------------------------------------
# 4. Strategy-specific differentiating behaviour
# ---------------------------------------------------------------------------


def test_heading_strategy_never_merges_sections() -> None:
    """HEADING: one section is always at least one chunk, and a chunk never spans two sections."""
    chunker = HeadingChunker()
    document = fixture_document(with_sections=True)
    config = small_config(ChunkStrategy.HEADING, max_tokens=200)  # generous: sections fit whole
    chunks = chunker.chunk(document, config=config)
    assert len(chunks) == len(FIXTURE_SECTIONS)
    assert [c.heading_path for c in chunks] == [s.heading_path for s in FIXTURE_SECTIONS]


def test_heading_strategy_splits_an_oversized_section_but_keeps_its_heading_path() -> None:
    chunker = HeadingChunker()
    document = fixture_document(with_sections=True)
    config = small_config(ChunkStrategy.HEADING, max_tokens=8)
    chunks = chunker.chunk(document, config=config)
    assert len(chunks) > len(FIXTURE_SECTIONS)
    travel_chunks = [c for c in chunks if c.heading_path == ("Travel Policy",)]
    assert len(travel_chunks) > 1


def test_hybrid_respects_headings_by_default_like_heading_does_for_boundaries() -> None:
    chunker = HybridChunker()
    document = fixture_document(with_sections=True)
    config = small_config(ChunkStrategy.HYBRID, max_tokens=200)
    chunks = chunker.chunk(document, config=config)
    # No chunk mixes heading paths from two different sections.
    heading_paths = {c.heading_path for c in chunks}
    assert heading_paths <= {s.heading_path for s in FIXTURE_SECTIONS}


def test_hybrid_ignores_section_boundaries_when_respect_headings_is_false() -> None:
    chunker = HybridChunker()
    document = fixture_document(with_sections=True)
    config = small_config(ChunkStrategy.HYBRID, max_tokens=200, respect_headings=False)
    chunks = chunker.chunk(document, config=config)
    # With no boundaries, this collapses to whatever RecursiveChunker would do on the same text.
    recursive_chunks = RecursiveChunker().chunk(document, config=small_config(
        ChunkStrategy.RECURSIVE, max_tokens=200
    ))
    assert [c.text for c in chunks] == [c.text for c in recursive_chunks]
    assert all(c.heading_path == () for c in chunks)


def test_token_strategy_ignores_paragraph_boundaries() -> None:
    """TOKEN packs pure word windows; unlike RECURSIVE it does not prefer to break at a blank
    line, so with a wide-enough budget a single chunk can span the paragraph boundary intact."""
    chunker = TokenChunker()
    config = small_config(ChunkStrategy.TOKEN, max_tokens=200, overlap_tokens=0)
    chunks = chunker.chunk(fixture_document(), config=config)
    assert len(chunks) == 1
    assert "\n\n" not in chunks[0].text  # whitespace collapsed by the word-join, not preserved


def test_sliding_window_measures_the_joined_text_not_a_sum_of_per_word_estimates() -> None:
    """Regression: an earlier version summed independent per-word token estimates for both window
    growth and stride advancement, which ignores the joiner characters between words. For this
    fixture the joined-text estimate (5) exceeds the per-word sum (4), so summing produced a chunk
    over budget; measuring the actual joined text at every step (matching ``grow_window``, which
    ``pack_with_overlap`` already relies on) keeps every chunk within budget."""
    from app.ai.core.text import estimate_tokens

    text = "aaaa bbbb cccc dddd"
    assert estimate_tokens(text) > sum(estimate_tokens(w) for w in text.split())  # the trap

    document = ParsedDocument(text=text, checksum="sliding-window-joiner-regression")
    config = ChunkingConfig(
        strategy=ChunkStrategy.SLIDING_WINDOW, max_tokens=4, overlap_tokens=1, min_tokens=1,
        sliding_stride_tokens=1,
    )
    chunks = SlidingWindowChunker().chunk(document, config=config)
    assert chunks
    for c in chunks:
        assert c.token_count <= config.max_tokens


def test_sliding_window_produces_denser_coverage_than_token_for_a_small_stride() -> None:
    document = fixture_document()
    token_chunks = TokenChunker().chunk(
        document, config=small_config(ChunkStrategy.TOKEN, max_tokens=20, overlap_tokens=4)
    )
    sliding_chunks = SlidingWindowChunker().chunk(
        document,
        config=small_config(ChunkStrategy.SLIDING_WINDOW, max_tokens=20, sliding_stride_tokens=4),
    )
    assert len(sliding_chunks) >= len(token_chunks)


def test_semantic_strategy_groups_similar_adjacent_sentences() -> None:
    """A high similarity threshold of 1.0 forces every sentence into its own group (nothing but a
    byte-identical vector clears the bar); a threshold of 0.0 accepts everything into one group."""
    provider = DeterministicEmbeddingProvider(dimensions=32, version="v1")
    chunker = SemanticChunker(embedding_provider=provider)
    document = fixture_document()

    permissive_config = small_config(
        ChunkStrategy.SEMANTIC, max_tokens=500, semantic_threshold=0.0
    )
    strict_config = small_config(ChunkStrategy.SEMANTIC, max_tokens=500, semantic_threshold=1.0)
    permissive = chunker.chunk(document, config=permissive_config)
    strict = chunker.chunk(document, config=strict_config)
    assert len(permissive) <= len(strict)


def test_semantic_strategy_single_sentence_document_yields_one_chunk() -> None:
    provider = DeterministicEmbeddingProvider(dimensions=32, version="v1")
    chunker = SemanticChunker(embedding_provider=provider)
    document = ParsedDocument(text="Just one sentence here.", checksum="single-sentence")
    chunks = chunker.chunk(document, config=small_config(ChunkStrategy.SEMANTIC, max_tokens=50))
    assert len(chunks) == 1


def test_parent_child_rejects_a_parent_max_tokens_not_larger_than_max_tokens() -> None:
    """Regression: nothing in ChunkingConfig cross-validates max_tokens against
    parent_max_tokens. Left unchecked, a parent already within max_tokens would re-pack into a
    single "child" that is a verbatim copy of the parent's full text, contradicting the module's
    own contract that a child never holds a copy of the parent's text."""
    config = small_config(
        ChunkStrategy.PARENT_CHILD, max_tokens=40, parent_max_tokens=40, min_tokens=1,
    )
    with pytest.raises(AIValidationError, match="parent_max_tokens"):
        ParentChildChunker().chunk(fixture_document(), config=config)


def test_parent_child_children_are_smaller_than_their_parent() -> None:
    chunker = ParentChildChunker()
    document = fixture_document()
    config = small_config(ChunkStrategy.PARENT_CHILD, max_tokens=10, parent_max_tokens=60)
    chunks = chunker.chunk(document, config=config)
    by_id = {c.id: c for c in chunks}
    for child in (c for c in chunks if c.is_child):
        parent = by_id[child.parent_id]
        assert child.token_count <= parent.token_count


# ---------------------------------------------------------------------------
# 5. config.py: settings -> ChunkingConfig bridge
# ---------------------------------------------------------------------------


def test_build_chunking_config_uses_settings_defaults() -> None:
    settings = AISettings(
        CHUNK_STRATEGY=ChunkStrategy.HEADING, CHUNK_MAX_TOKENS=333, CHUNK_OVERLAP_TOKENS=11,
        CHUNK_MIN_TOKENS=5,
    )
    config = build_chunking_config(settings)
    assert config.strategy is ChunkStrategy.HEADING
    assert config.max_tokens == 333
    assert config.overlap_tokens == 11
    assert config.min_tokens == 5


def test_build_chunking_config_overrides_win_over_settings() -> None:
    settings = AISettings(CHUNK_STRATEGY=ChunkStrategy.RECURSIVE, CHUNK_MAX_TOKENS=333)
    config = build_chunking_config(
        settings, strategy=ChunkStrategy.TOKEN, max_tokens=64, overlap_tokens=8
    )
    assert config.strategy is ChunkStrategy.TOKEN
    assert config.max_tokens == 64


# ---------------------------------------------------------------------------
# 6. factory.py: registration and resolution
# ---------------------------------------------------------------------------


def test_register_chunkers_is_idempotent() -> None:
    register_chunkers()
    register_chunkers()  # replace=True inside every registration; must not raise
    assert len(chunking_registry.names()) == len(ChunkStrategy)


def test_resolve_chunker_auto_registers_on_first_use() -> None:
    assert chunking_registry.names() == ()
    resolved = resolve_chunker(ChunkStrategy.HEADING)
    assert isinstance(resolved, HeadingChunker)


def test_resolve_chunker_semantic_does_not_require_credentials() -> None:
    """SEMANTIC's factory must resolve through the deterministic embedding fallback offline."""
    resolved = resolve_chunker(ChunkStrategy.SEMANTIC)
    assert resolved.strategy is ChunkStrategy.SEMANTIC


def test_every_chunk_strategy_member_has_a_registered_chunker() -> None:
    register_chunkers()
    registered = set(chunking_registry.names())
    for strategy in ChunkStrategy:
        assert strategy.value.lower() in registered
