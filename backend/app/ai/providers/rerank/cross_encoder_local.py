"""A local cross-encoder via onnxruntime — no torch, mirroring ``bge_m3_onnx.py``'s approach.

Same reasoning as the default embedding provider: ``sentence-transformers`` would pull ~2.5 GB of
torch for a model that only ever runs forward on CPU, so the ONNX graph is driven directly with
``onnxruntime`` + ``tokenizers``, both already dependencies of this platform.

A cross-encoder scores a **pair** — query and passage encoded as one sequence
(``[CLS] query [SEP] passage [SEP]``), not two separate embeddings compared afterward, which is
what lets it capture interactions a bi-encoder's dot product cannot.
``tokenizers.Tokenizer.encode_batch`` accepts pairs natively via ``(text, pair)`` tuples, so
batching the whole candidate set against one query is a single tokenizer call.

**Unlike ``bge_m3_onnx.py``, this file's file-layout assumption is not empirically verified
against downloaded weights.** ``Xenova/ms-marco-MiniLM-L-6-v2`` is used because Xenova's ONNX
mirrors are the conventional source for exactly this runtime (onnxruntime/transformers.js, no
PyTorch), and they consistently publish an ``onnx/model.onnx``. That has not been confirmed by
loading it in this environment. Treat this provider as unverified debt until it has been.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any, Optional, Sequence

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import RetrievedChunk
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_NAME = "cross_encoder_local"
DEFAULT_MODEL_REPO = "Xenova/ms-marco-MiniLM-L-6-v2"

_REQUIRED_FILES = ("onnx/model.onnx", "tokenizer.json")


class CrossEncoderLocalReranker:
    """A local ONNX cross-encoder. Implements the ``Reranker`` protocol.

    ``session``/``tokenizer`` are injectable so the request-shaping and score-pooling logic below
    can be exercised by a test double without downloading real weights — the same seam M6's external
    vector-store adapters use for their clients.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model_repo: str = DEFAULT_MODEL_REPO,
        cache_dir: Optional[str] = None,
        onnx_threads: int = 0,
        max_length: int = 512,
        session: Any = None,
        tokenizer: Any = None,
    ) -> None:
        self._model_repo = model_repo
        self._cache_dir = cache_dir
        self._onnx_threads = onnx_threads
        self._max_length = max_length
        self._session: Any = session
        self._tokenizer: Any = tokenizer
        # ``_ensure_loaded`` short-circuits when a session is already present (an injected test
        # double, in practice — this is the seam M6's external vector-store clients use the same
        # way), so it never runs the code that would otherwise derive this from the session. It has
        # to be computed here too, or a rerank() call would feed nothing to an injected session's
        # ``run()`` and every input lookup below would find an empty set.
        self._input_names: frozenset[str] = (
            frozenset(i.name for i in session.get_inputs()) if session is not None else frozenset()
        )
        self._lock = threading.RLock()
        self._load_error: Optional[str] = None

    def is_available(self) -> bool:
        """Whether the runtime is usable, without loading the model. Never raises."""
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
        return True

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            try:
                from huggingface_hub import hf_hub_download
                from tokenizers import Tokenizer
                import onnxruntime as ort
            except ImportError as exc:
                self._load_error = str(exc)
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME, kind="RERANK",
                    remedy=(
                        "Install the local reranking dependencies: pip install onnxruntime "
                        "tokenizers huggingface-hub (already installed for bge-m3, if configured)."
                    ),
                ) from exc

            try:
                paths = {
                    name: hf_hub_download(self._model_repo, name, cache_dir=self._cache_dir)
                    for name in _REQUIRED_FILES
                }
            except Exception as exc:
                self._load_error = str(exc)
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME, kind="RERANK",
                    remedy=(
                        f"Could not obtain '{self._model_repo}' from HuggingFace Hub. Check "
                        f"network access, or set a different AI_RERANK_MODEL. "
                        f"Cause: {type(exc).__name__}: {exc}"
                    ),
                ) from exc

            try:
                options = ort.SessionOptions()
                if self._onnx_threads > 0:
                    options.intra_op_num_threads = self._onnx_threads
                started = time.perf_counter()
                session = ort.InferenceSession(
                    paths["onnx/model.onnx"], sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                tokenizer = Tokenizer.from_file(paths["tokenizer.json"])
                tokenizer.enable_truncation(max_length=self._max_length)
                tokenizer.enable_padding()
            except Exception as exc:
                self._load_error = str(exc)
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME, kind="RERANK",
                    remedy=f"onnxruntime failed to load the model: {type(exc).__name__}: {exc}",
                ) from exc

            self._session = session
            self._tokenizer = tokenizer
            self._input_names = frozenset(i.name for i in session.get_inputs())
            logger.info(
                "ai.rerank.model_loaded",
                extra={
                    "provider": PROVIDER_NAME, "model": self._model_repo,
                    "loadSeconds": round(time.perf_counter() - started, 2),
                },
            )

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_n: Optional[int] = None,
    ) -> list[RetrievedChunk]:
        """Reorder every candidate given. ``top_n`` is accepted for protocol compliance and ignored
        — see :meth:`app.ai.reranking.heuristic.HeuristicReranker.rerank` for why: truncation is the
        caller's decision, and one local ONNX call has no response payload to shrink by cutting it
        early.
        """
        if not candidates:
            return []
        self._ensure_loaded()
        scores = self._score_pairs(query, [c.text for c in candidates])
        if len(scores) != len(candidates):  # pragma: no cover - guards a tokenizer/model mismatch
            raise ProviderError(
                PROVIDER_NAME, f"scored {len(scores)} pairs for {len(candidates)} candidates"
            )
        scored = [
            replace(candidate, score=score, rerank_score=score)
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        scored.sort(key=lambda c: (-c.score, str(c.id)))
        return scored

    def _score_pairs(self, query: str, passages: Sequence[str]) -> list[float]:
        import numpy as np

        pairs = [(query, passage) for passage in passages]
        encodings = self._tokenizer.encode_batch(pairs)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feed: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.array(
                [e.type_ids for e in encodings], dtype=np.int64
            )

        try:
            outputs = self._session.run(None, feed)
        except Exception as exc:
            raise ProviderError(
                PROVIDER_NAME, f"ONNX inference failed: {type(exc).__name__}: {exc}"
            ) from exc

        logits = np.asarray(outputs[0])
        # A single-logit regression head (shape [batch, 1] or [batch]) is the common export for a
        # cross-encoder trained on MS MARCO; a two-class head (shape [batch, 2]) is scored by its
        # positive-class probability instead. Squashed through a sigmoid either way, since a raw
        # logit is unbounded and the Reranker contract requires [0, 1].
        if logits.ndim == 2 and logits.shape[1] == 2:
            positive = logits[:, 1] - logits[:, 0]
            bounded = 1.0 / (1.0 + np.exp(-positive))
        else:
            flat = logits.reshape(-1)
            bounded = 1.0 / (1.0 + np.exp(-flat))
        return [float(v) for v in bounded]


__all__ = ["DEFAULT_MODEL_REPO", "PROVIDER_NAME", "CrossEncoderLocalReranker"]
