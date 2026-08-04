"""Rerank provider adapters (Task 4's provider pattern applied to reranking) and their registration.

Mirrors ``app/ai/providers/embeddings/__init__.py`` exactly: ``heuristic`` is the always-available
default, every other adapter is registered with a lazy factory so the registry lists all of them on
a host with no credentials and no optional dependencies, and the preference order used by
``resolve_with_fallback`` degrades to ``heuristic`` rather than leaving reranking entirely unusable.
"""

from app.ai.providers.rerank.bedrock import BedrockReranker
from app.ai.providers.rerank.cohere import CohereReranker
from app.ai.providers.rerank.cross_encoder_local import CrossEncoderLocalReranker
from app.ai.reranking.heuristic import HeuristicReranker

FALLBACK_PREFERENCE = ("heuristic",)


def register_rerank_providers() -> None:
    """Register every rerank adapter. Called once by the composition root.

    Every factory is lazy, so this succeeds on a machine with no credentials, no ``boto3``, no
    ``httpx`` and no cross-encoder weights — the adapters exist to be *selectable*, not preloaded.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.interfaces.reranker import Reranker
    from app.ai.registry.registry import rerank_registry as reg

    reg.register(
        "heuristic",
        HeuristicReranker,
        protocol=Reranker,
        description="Deterministic term-coverage + exact-phrase reranker. No model, always on.",
        metadata={"semantic": False, "local": True},
        replace=True,
    )
    reg.register(
        "cross_encoder_local",
        lambda: CrossEncoderLocalReranker(
            model_repo=s.RERANK_MODEL or "Xenova/ms-marco-MiniLM-L-6-v2",
        ),
        protocol=Reranker,
        description="Local cross-encoder via onnxruntime, no torch. Not verified against live "
        "weights.",
        metadata={"semantic": True, "local": True},
        replace=True,
    )
    reg.register(
        "cohere",
        lambda: CohereReranker(
            api_key=s.COHERE_API_KEY,
            model=s.RERANK_MODEL or "rerank-v3.5",
            timeout_seconds=s.RERANK_TIMEOUT_SECONDS,
        ),
        protocol=Reranker,
        description="Cohere Rerank v3.5. Truncates server-side when top_n is given.",
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )
    reg.register(
        "bedrock",
        lambda: BedrockReranker(
            model_arn=s.RERANK_MODEL or "amazon.rerank-v1:0",
            region=s.BEDROCK_REGION,
            timeout_seconds=s.RERANK_TIMEOUT_SECONDS,
        ),
        protocol=Reranker,
        description="Amazon Bedrock rerank (Amazon Rerank or Cohere-on-Bedrock, by model ARN).",
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )


def resolve_rerank_provider():
    """The configured reranker, degrading to the heuristic one when it is unusable.

    Mirrors ``resolve_embedding_provider`` exactly, including the fallback being logged loudly by
    the registry so a degradation is never mistaken for the real thing.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.registry.registry import rerank_registry as reg

    if not reg.names():
        register_rerank_providers()

    preferences = [s.RERANK_PROVIDER]
    preferences.extend(p for p in FALLBACK_PREFERENCE if p != s.RERANK_PROVIDER)
    return reg.resolve_with_fallback(preferences)


__all__ = [
    "FALLBACK_PREFERENCE",
    "BedrockReranker",
    "CohereReranker",
    "CrossEncoderLocalReranker",
    "HeuristicReranker",
    "register_rerank_providers",
    "resolve_rerank_provider",
]
