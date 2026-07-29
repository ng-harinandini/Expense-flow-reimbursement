"""Embedding provider adapters (Task 4) and their registration.

Registration order in :func:`register_embedding_providers` is what implements the
graceful-degradation path decided in the plan: ``bge_m3_onnx`` is the configured default, and the
deterministic provider is the fallback preference so CI stays green without the 2.3 GB weights.
"""

from app.ai.interfaces.embeddings import EmbeddingProvider
from app.ai.providers.embeddings.bge_m3_onnx import BgeM3OnnxEmbeddingProvider
from app.ai.providers.embeddings.cloud import (
    BedrockTitanEmbeddingProvider,
    CohereEmbeddingProvider,
    OpenAIEmbeddingProvider,
    TeiHttpEmbeddingProvider,
    VoyageEmbeddingProvider,
)
from app.ai.providers.embeddings.deterministic import DeterministicEmbeddingProvider

# Preference order used by ``ComponentRegistry.resolve_with_fallback``. The configured provider is
# tried first; the deterministic one can always serve.
FALLBACK_PREFERENCE = ("deterministic",)


def register_embedding_providers() -> None:
    """Register every embedding adapter. Called once by the composition root.

    Every factory is lazy, so this succeeds on a machine with no credentials, no ``boto3``, no
    ``httpx`` and no model weights — the adapters exist to be *selectable*, not preloaded.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.registry.registry import embedding_registry as reg

    reg.register(
        "bge_m3_onnx",
        lambda: BgeM3OnnxEmbeddingProvider(
            version=s.EMBEDDING_VERSION,
            max_input_tokens=s.EMBEDDING_MAX_INPUT_TOKENS,
            cache_dir=s.EMBEDDING_MODEL_CACHE_DIR,
            onnx_threads=s.EMBEDDING_ONNX_THREADS,
        ),
        protocol=EmbeddingProvider,
        description="BAAI/bge-m3 via onnxruntime. 1024d, 8192 ctx, multilingual, CPU, no torch.",
        metadata={"dimensions": 1024, "semantic": True, "local": True},
        replace=True,
    )
    reg.register(
        "deterministic",
        lambda: DeterministicEmbeddingProvider(
            dimensions=s.EMBEDDING_DIMENSIONS, version=s.EMBEDDING_VERSION
        ),
        protocol=EmbeddingProvider,
        description="Hash n-gram stand-in. NO semantic similarity; offline/CI fallback only.",
        metadata={"dimensions": s.EMBEDDING_DIMENSIONS, "semantic": False, "local": True},
        replace=True,
    )
    reg.register(
        "bedrock_titan",
        lambda: BedrockTitanEmbeddingProvider(
            model=(
                s.EMBEDDING_MODEL
                if "titan" in s.EMBEDDING_MODEL.lower()
                else "amazon.titan-embed-text-v2:0"
            ),
            dimensions=s.EMBEDDING_DIMENSIONS,
            version=s.EMBEDDING_VERSION,
            region=s.BEDROCK_REGION,
            timeout_seconds=s.EMBEDDING_TIMEOUT_SECONDS,
        ),
        protocol=EmbeddingProvider,
        description="Amazon Titan Text Embeddings via Bedrock. One text per call (no batch API).",
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )
    reg.register(
        "openai",
        lambda: OpenAIEmbeddingProvider(
            api_key=s.OPENAI_API_KEY,
            model=(
                s.EMBEDDING_MODEL
                if "text-embedding" in s.EMBEDDING_MODEL
                else "text-embedding-3-large"
            ),
            dimensions=s.EMBEDDING_DIMENSIONS,
            version=s.EMBEDDING_VERSION,
            timeout_seconds=s.EMBEDDING_TIMEOUT_SECONDS,
            cost_per_million_tokens_usd=0.13,
        ),
        protocol=EmbeddingProvider,
        description="OpenAI text-embedding-3. Symmetric; supports native dimension truncation.",
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )
    reg.register(
        "cohere",
        lambda: CohereEmbeddingProvider(
            api_key=s.COHERE_API_KEY,
            model=s.EMBEDDING_MODEL if "embed" in s.EMBEDDING_MODEL else "embed-v4.0",
            dimensions=s.EMBEDDING_DIMENSIONS,
            version=s.EMBEDDING_VERSION,
            timeout_seconds=s.EMBEDDING_TIMEOUT_SECONDS,
            cost_per_million_tokens_usd=0.12,
        ),
        protocol=EmbeddingProvider,
        description="Cohere embed. Asymmetric: input_type differs for query vs document.",
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )
    reg.register(
        "voyage",
        lambda: VoyageEmbeddingProvider(
            api_key=s.VOYAGE_API_KEY,
            model="voyage-3-large",
            dimensions=s.EMBEDDING_DIMENSIONS,
            version=s.EMBEDDING_VERSION,
            timeout_seconds=s.EMBEDDING_TIMEOUT_SECONDS,
            cost_per_million_tokens_usd=0.18,
        ),
        protocol=EmbeddingProvider,
        description="Voyage AI embeddings. Asymmetric, like Cohere.",
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )
    reg.register(
        "tei_http",
        lambda: TeiHttpEmbeddingProvider(
            base_url=s.TEI_BASE_URL,
            model=s.EMBEDDING_MODEL,
            dimensions=s.EMBEDDING_DIMENSIONS,
            version=s.EMBEDDING_VERSION,
            timeout_seconds=s.EMBEDDING_TIMEOUT_SECONDS,
            query_prefix=s.TEI_QUERY_PREFIX,
            passage_prefix=s.TEI_PASSAGE_PREFIX,
        ),
        protocol=EmbeddingProvider,
        description="Self-hosted HuggingFace TEI endpoint (BGE / E5 / Instructor).",
        metadata={"semantic": True, "local": False, "requiresEndpoint": True},
        replace=True,
    )


def resolve_embedding_provider():
    """The active provider, degrading to the deterministic one when the configured one is unusable.

    One place decides, and a fallback is logged loudly by the registry — a silent downgrade to
    non-semantic vectors is the worst outcome here, which is also why the choice is reported through
    ``EmbeddingService.is_semantic`` all the way out to API metadata.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.registry.registry import embedding_registry as reg

    if not reg.names():
        register_embedding_providers()

    preferences = [s.EMBEDDING_PROVIDER]
    if s.EMBEDDING_FALLBACK_TO_DETERMINISTIC:
        preferences.extend(p for p in FALLBACK_PREFERENCE if p != s.EMBEDDING_PROVIDER)
    return reg.resolve_with_fallback(preferences)


__all__ = [
    "FALLBACK_PREFERENCE",
    "BedrockTitanEmbeddingProvider",
    "BgeM3OnnxEmbeddingProvider",
    "CohereEmbeddingProvider",
    "DeterministicEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "TeiHttpEmbeddingProvider",
    "VoyageEmbeddingProvider",
    "register_embedding_providers",
    "resolve_embedding_provider",
]
