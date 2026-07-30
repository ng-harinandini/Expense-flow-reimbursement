"""Bridge between global :class:`AISettings` and a per-call :class:`ChunkingConfig`.

``ChunkingConfig`` is deliberately not read from settings directly (see its docstring: "one
ingestion profile can chunk travel policies differently from receipts within the same process").
:func:`build_chunking_config` is the one place that turns the process-wide defaults into a starting
point, with per-call overrides layered on top for exactly that per-source customisation.
"""

from __future__ import annotations

from app.ai.core.config import AISettings, ai_settings
from app.ai.core.enums import ChunkStrategy
from app.ai.interfaces.chunker import ChunkingConfig


def build_chunking_config(
    settings: AISettings | None = None,
    *,
    strategy: ChunkStrategy | None = None,
    **overrides: object,
) -> ChunkingConfig:
    """``ChunkingConfig`` seeded from ``settings`` (default: the process singleton).

    ``strategy`` and any other :class:`ChunkingConfig` field name may be overridden per call, e.g.
    ``build_chunking_config(strategy=ChunkStrategy.HEADING, max_tokens=256)`` for a source that
    needs tighter chunks than the global default.
    """
    s = settings or ai_settings
    fields = {
        "strategy": strategy if strategy is not None else s.CHUNK_STRATEGY,
        "max_tokens": s.CHUNK_MAX_TOKENS,
        "overlap_tokens": s.CHUNK_OVERLAP_TOKENS,
        "min_tokens": s.CHUNK_MIN_TOKENS,
        "parent_max_tokens": s.CHUNK_PARENT_MAX_TOKENS,
        "semantic_threshold": s.CHUNK_SEMANTIC_THRESHOLD,
        "sliding_stride_tokens": s.CHUNK_SLIDING_STRIDE_TOKENS,
    }
    fields.update(overrides)
    return ChunkingConfig(**fields)


__all__ = ["build_chunking_config"]
