"""Deterministic identity, checksums and fingerprints.

Everything here is a **pure function of its input** — the same bytes always yield the same id. That
property is what three separate platform guarantees rest on:

* **Idempotent ingestion.** A document's identity is the SHA-256 of its original bytes, so
  re-uploading the same file is a detectable no-op rather than a second copy.
* **Stable chunk ids.** Re-chunking an unchanged document reproduces the same chunk ids, so
  re-indexing updates rows in place instead of orphaning embeddings.
* **Replayable retrieval.** A configuration fingerprint lets an answer recorded months ago be
  matched against the settings that produced it.

The uuid5 namespace approach mirrors the T003 seed migrations, which use deterministic uuid5 ids so
reference data is identical across environments.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable, Mapping

# Fixed application namespace. Derived from a DNS-namespaced uuid5 so it is reproducible from the
# string alone and cannot collide with another project's namespace.
AI_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "ai.expenseflow.knowledge")

_CHECKSUM_PREFIX_LENGTH = 16


def content_checksum(content: bytes) -> str:
    """SHA-256 of raw bytes, lowercase hex.

    Computed over the **original** bytes before any normalization, so a document's identity is
    independent of parser or cleaning changes. If normalization fed the checksum, a parser upgrade
    would silently re-ingest the entire corpus as "new" documents.
    """
    return hashlib.sha256(content).hexdigest()


def text_checksum(text: str) -> str:
    """SHA-256 of text, encoded UTF-8. Used as the embedding cache key component."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_checksum(checksum: str) -> str:
    """First 16 hex chars — for log lines and human-facing labels, never for equality checks."""
    return checksum[:_CHECKSUM_PREFIX_LENGTH]


def deterministic_uuid(*parts: Any) -> uuid.UUID:
    """uuid5 over ``parts`` joined with a separator that cannot appear in a UUID or checksum.

    ``deterministic_uuid("doc", checksum)`` and ``deterministic_uuid("docchecksum")`` must not
    collide, which is why the separator is explicit rather than a bare concatenation.
    """
    name = "\x1f".join("" if p is None else str(p) for p in parts)
    return uuid.uuid5(AI_NAMESPACE, name)


def document_id_for(tenant_id: str, checksum: str) -> uuid.UUID:
    """Identity of a document version: one tenant + one exact byte sequence.

    Tenant-scoped deliberately — two enterprises uploading the same public tax PDF each get their
    own document, so neither can observe the other's corpus or delete the other's row.
    """
    return deterministic_uuid("document", tenant_id, checksum)


def chunk_id_for(document_id: uuid.UUID, index: int, text: str) -> uuid.UUID:
    """Identity of a chunk within a document version.

    Includes the text, not just the index: if a chunking-parameter change shifts boundaries, the
    ids change too, which is correct — a chunk with different content is a different chunk, and
    reusing its id would leave a stale embedding attached to new text.
    """
    return deterministic_uuid("chunk", document_id, index, text_checksum(text))


def embedding_cache_key(text: str, spec_key: str) -> str:
    """Cache key for one embedding: content plus the exact model version.

    The spec key must be part of it. Keying on text alone is how a model upgrade ends up serving
    vectors from the previous model out of cache — a corruption that produces no error, only
    quietly degraded results.
    """
    return f"emb:{spec_key}:{text_checksum(text)}"


def _canonical(value: Any) -> Any:
    """Recursively coerce a value into something ``json.dumps`` orders deterministically."""
    if isinstance(value, Mapping):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Short, order-independent digest of a configuration mapping.

    Dict iteration order and float formatting must not change the digest, or a fingerprint recorded
    today would fail to match the identical configuration tomorrow. Hence canonical sorting and a
    fixed JSON separator set.
    """
    payload = json.dumps(_canonical(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_CHECKSUM_PREFIX_LENGTH]


def retrieval_cache_key(query_text: str, spec_key: str, fingerprint: str,
                        tenant_id: str = "default") -> str:
    """Cache key for a whole retrieval: query, model version, config and tenant.

    Tenant is included so one tenant can never be served another's cached results.
    """
    return f"ret:{tenant_id}:{spec_key}:{fingerprint}:{text_checksum(query_text)}"


def stable_hash_int(text: str, *, buckets: int) -> int:
    """Deterministic bucket index for ``text``.

    Uses BLAKE2b rather than Python's ``hash()``, which is randomized per process by PYTHONHASHSEED
    and would make the deterministic embedding provider non-reproducible across restarts.
    """
    if buckets < 1:
        raise ValueError("buckets must be >= 1.")
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % buckets


def joined_checksum(items: Iterable[str]) -> str:
    """Order-insensitive checksum over a set of strings (used for alias-set identity)."""
    joined = "\x1f".join(sorted(items))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


__all__ = [
    "AI_NAMESPACE",
    "chunk_id_for",
    "config_fingerprint",
    "content_checksum",
    "deterministic_uuid",
    "document_id_for",
    "embedding_cache_key",
    "joined_checksum",
    "retrieval_cache_key",
    "short_checksum",
    "stable_hash_int",
    "text_checksum",
]
