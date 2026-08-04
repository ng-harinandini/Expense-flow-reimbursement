"""BAAI/bge-m3 via onnxruntime — the platform's default embedding provider.

1024 dimensions, 8192-token context, multilingual, CPU-only, **no torch**.

Why the ONNX route rather than ``fastembed`` or ``sentence-transformers``:

* ``fastembed`` does not expose bge-m3 in any of its model classes (checked: ``TextEmbedding``,
  ``SparseTextEmbedding``, ``LateInteractionTextEmbedding`` — it ships bge-base/large/small en+zh
  only).
* ``sentence-transformers`` would work but pulls torch, roughly 2.5 GB, for a model we only ever run
  forward on CPU.
* The bge-m3 repository ships a complete ONNX bundle, and ``onnxruntime`` + ``tokenizers`` are two
  small wheels. So the platform drives the graph directly.

Implementation notes that were established empirically, not assumed:

* The exported graph takes **only** ``input_ids`` and ``attention_mask``. It has no
  ``token_type_ids`` input, and feeding one raises.
* It exposes a ``sentence_embedding`` output directly, so no manual pooling is needed. CLS-pooling
  ``token_embeddings`` is kept as a fallback for a differently-exported graph, since bge-m3's dense
  head is the CLS token.
* Weights live in ``onnx/model.onnx_data`` (~2.3 GB) beside a small ``onnx/model.onnx`` graph.
  onnxruntime loads the external data from the same directory, which is why both files must be
  downloaded before the session is created.

The model is loaded **lazily on first use**, never at import. Constructing this provider must be
free so the registry can list it on a machine that has never downloaded the weights.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional, Sequence

from app.ai.core.config import BGE_M3_DIMENSIONS, BGE_M3_MAX_TOKENS, ai_settings
from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import (
    EmbeddingResult,
    EmbeddingSpec,
    EmbeddingUsage,
    EmbeddingVector,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_NAME = "bge_m3_onnx"
MODEL_REPO = "BAAI/bge-m3"

# Everything onnxruntime needs in one directory. ``model.onnx_data`` holds the weights; the graph
# references it by relative path, so it must be fetched even though nothing opens it directly.
_REQUIRED_FILES = (
    "onnx/model.onnx",
    "onnx/model.onnx_data",
    "onnx/tokenizer.json",
)
_OPTIONAL_FILES = (
    "onnx/config.json",
    "onnx/special_tokens_map.json",
    "onnx/tokenizer_config.json",
    "onnx/Constant_7_attr__value",
)


class BgeM3OnnxEmbeddingProvider:
    """bge-m3 dense embeddings. Implements ``app.ai.interfaces.embeddings.EmbeddingProvider``."""

    name = PROVIDER_NAME
    is_semantic = True

    def __init__(
        self,
        *,
        version: Optional[str] = None,
        max_input_tokens: int = BGE_M3_MAX_TOKENS,
        cache_dir: Optional[str] = None,
        onnx_threads: int = 0,
        allow_download: bool = True,
    ) -> None:
        self._spec = EmbeddingSpec(
            provider=PROVIDER_NAME,
            model=MODEL_REPO,
            dimensions=BGE_M3_DIMENSIONS,
            version=version or ai_settings.EMBEDDING_VERSION,
            normalized=True,
            max_input_tokens=max_input_tokens,
            cost_per_million_tokens_usd=0.0,   # local model: no marginal cost
        )
        self._cache_dir = cache_dir
        self._onnx_threads = onnx_threads
        self._allow_download = allow_download

        # Populated on first use. The lock makes a concurrent first request build one session, not
        # several — each would hold its own ~2.3 GB of weights.
        self._session: Any = None
        self._tokenizer: Any = None
        self._input_names: frozenset[str] = frozenset()
        self._output_names: tuple[str, ...] = ()
        self._lock = threading.RLock()
        self._load_error: Optional[str] = None

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    # --- availability -------------------------------------------------------

    def is_available(self) -> bool:
        """Whether the runtime and weights are usable, without loading the model.

        Must be cheap and must never raise: the registry calls it to decide whether to fall back to
        the deterministic provider, so an exception here would defeat the degradation path.
        """
        if self._session is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            import onnxruntime  # noqa: F401, PLC0415
            import tokenizers  # noqa: F401, PLC0415
            from huggingface_hub import hf_hub_download  # noqa: F401, PLC0415
        except ImportError:
            return False
        if self._allow_download:
            return True
        # Offline mode: only report available if the weights are already in the cache.
        return self._cached_paths() is not None

    def _cached_paths(self) -> Optional[dict[str, str]]:
        """Resolve the required files from the local cache only. ``None`` if any is absent."""
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            return None
        resolved: dict[str, str] = {}
        for name in _REQUIRED_FILES:
            try:
                resolved[name] = hf_hub_download(
                    MODEL_REPO, name, cache_dir=self._cache_dir, local_files_only=True
                )
            except Exception:
                return None
        return resolved

    # --- loading ------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            try:
                from huggingface_hub import hf_hub_download
                from tokenizers import Tokenizer
            except ImportError as exc:
                self._load_error = str(exc)
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME, kind="EMBEDDING",
                    remedy=(
                        "Install the local embedding dependencies: "
                        "pip install fastembed (brings onnxruntime, tokenizers, huggingface-hub)."
                    ),
                ) from exc

            try:
                paths: dict[str, str] = {}
                for name in _REQUIRED_FILES:
                    paths[name] = hf_hub_download(
                        MODEL_REPO, name, cache_dir=self._cache_dir,
                        local_files_only=not self._allow_download,
                    )
                for name in _OPTIONAL_FILES:
                    try:
                        hf_hub_download(
                            MODEL_REPO, name, cache_dir=self._cache_dir,
                            local_files_only=not self._allow_download,
                        )
                    except Exception:
                        # Genuinely optional: the graph and tokenizer alone are enough.
                        pass
            except Exception as exc:
                self._load_error = str(exc)
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME, kind="EMBEDDING",
                    remedy=(
                        f"Could not obtain the {MODEL_REPO} ONNX weights (~2.3 GB). "
                        "Check network access to huggingface.co, or pre-populate the cache and set "
                        f"AI_EMBEDDING_MODEL_CACHE_DIR. Cause: {type(exc).__name__}: {exc}"
                    ),
                ) from exc

            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                if self._onnx_threads > 0:
                    options.intra_op_num_threads = self._onnx_threads
                started = time.perf_counter()
                session = ort.InferenceSession(
                    paths["onnx/model.onnx"],
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                tokenizer = Tokenizer.from_file(paths["onnx/tokenizer.json"])
                tokenizer.enable_truncation(max_length=self._spec.max_input_tokens)
                tokenizer.enable_padding()
            except Exception as exc:
                self._load_error = str(exc)
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME, kind="EMBEDDING",
                    remedy=f"onnxruntime failed to load the model: {type(exc).__name__}: {exc}",
                ) from exc

            self._session = session
            self._tokenizer = tokenizer
            self._input_names = frozenset(i.name for i in session.get_inputs())
            self._output_names = tuple(o.name for o in session.get_outputs())
            logger.info(
                "ai.embedding.model_loaded",
                extra={
                    "provider": PROVIDER_NAME,
                    "model": MODEL_REPO,
                    "dimensions": self._spec.dimensions,
                    "loadSeconds": round(time.perf_counter() - started, 2),
                    "inputs": sorted(self._input_names),
                    "outputs": list(self._output_names),
                },
            )

    # --- inference ----------------------------------------------------------

    def _forward(self, texts: Sequence[str]) -> tuple[tuple[tuple[float, ...], ...], int]:
        """Run the graph. Returns the vectors and the total (real) token count."""
        self._ensure_loaded()
        import numpy as np

        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        # Only feed inputs the graph declares — bge-m3's export has no token_type_ids, and passing
        # an undeclared input raises.
        feed: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        try:
            outputs = self._session.run(None, feed)
        except Exception as exc:
            raise ProviderError(
                PROVIDER_NAME, f"ONNX inference failed: {type(exc).__name__}: {exc}"
            ) from exc

        if "sentence_embedding" in self._output_names:
            pooled = outputs[self._output_names.index("sentence_embedding")]
        else:
            # Fallback for a differently-exported graph: bge-m3's dense head is the CLS token.
            token_embeddings = outputs[0]
            if token_embeddings.ndim != 3:
                raise ProviderError(
                    PROVIDER_NAME,
                    "the graph exposes neither 'sentence_embedding' nor 3-D token embeddings",
                )
            pooled = token_embeddings[:, 0, :]

        if pooled.shape[1] != self._spec.dimensions:
            raise ProviderError(
                PROVIDER_NAME,
                f"model returned {pooled.shape[1]} dimensions, expected {self._spec.dimensions}",
            )

        # Normalize in numpy (cheap and vectorised), then hand out plain tuples so nothing
        # downstream needs numpy.
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = pooled / norms
        vectors = tuple(tuple(float(v) for v in row) for row in unit)
        tokens = int(attention_mask.sum())
        return vectors, tokens

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), spec=self._spec, usage=EmbeddingUsage())

        started = time.perf_counter()
        raw_vectors, tokens = self._forward(texts)
        latency_ms = int((time.perf_counter() - started) * 1000)

        vectors = tuple(
            EmbeddingVector(
                values=values, spec_key=self._spec.key, dimensions=self._spec.dimensions
            )
            for values in raw_vectors
        )
        usage = EmbeddingUsage(
            texts=len(texts),
            tokens=tokens,
            latency_ms=latency_ms,
            cost_usd=0.0,
            provider=PROVIDER_NAME,
            model=MODEL_REPO,
        )
        return EmbeddingResult(vectors=vectors, spec=self._spec, usage=usage)

    def embed_query(self, text: str) -> EmbeddingVector:
        """bge-m3 is symmetric for dense retrieval — no query instruction prefix.

        Unlike E5 or Instructor, bge-m3 is trained so queries and passages share one embedding space
        directly. Adding a prefix here would *reduce* recall.
        """
        vectors, _ = self._forward([text])
        return EmbeddingVector(
            values=vectors[0], spec_key=self._spec.key, dimensions=self._spec.dimensions
        )

    # --- token counting -----------------------------------------------------

    def count_tokens(self, text: str) -> int:
        """Exact token count from the model's own SentencePiece tokenizer.

        Satisfies :class:`app.ai.interfaces.embeddings.TokenCounter`, which is what lets chunking
        budgets be exact rather than estimated — the heuristic in ``app.ai.core.text`` deliberately
        errs high, and over-conservative chunks waste context.
        """
        self._ensure_loaded()
        return len(self._tokenizer.encode(text).ids)

    def as_token_counter(self) -> "BgeM3TokenCounter":
        return BgeM3TokenCounter(self)


class BgeM3TokenCounter:
    """Adapts the provider's tokenizer to the ``TokenCounter`` protocol."""

    def __init__(self, provider: BgeM3OnnxEmbeddingProvider) -> None:
        self._provider = provider

    def count(self, text: str) -> int:
        return self._provider.count_tokens(text)


__all__ = [
    "MODEL_REPO",
    "PROVIDER_NAME",
    "BgeM3OnnxEmbeddingProvider",
    "BgeM3TokenCounter",
]
