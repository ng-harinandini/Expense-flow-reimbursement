"""AI platform error hierarchy.

Every class here descends from T003's :class:`app.domain.errors.DomainError`, so the AI layer
inherits the existing handler's stable body shape (``detail``/``code``/``context``/``requestId``)
and its logging.

Most of the hierarchy needs **no** wiring at all: ``app.core.errors.STATUS_BY_ERROR`` resolves by
``isinstance`` against an ordered table, so subclassing the right base is enough.

    AIValidationError          -> 422   (subclass of ValidationError)
    KnowledgeNotFoundError     -> 404   (subclass of NotFoundError)
    AIConflictError            -> 409   (subclass of ConflictError)
      DocumentAlreadyIndexed   -> 409
      EmbeddingVersionConflict -> 409
    ToolPermissionError        -> 403   (subclass of ForbiddenError)

The provider-failure family is the exception, because none of those four bases means "upstream is
down" and the table's fallback is **400** — which would report an outage as a client mistake. Those
classes therefore declare a class-level ``http_status``, which ``status_for`` honours ahead of the
table:

    AIUnavailableError         -> 503   capability requested but unusable
      ProviderNotConfigured    -> 503     no credentials / missing dependency
      FeatureDisabled          -> 503     switched off by a flag
    ProviderError              -> 502   the upstream provider failed
      ProviderTimeout          -> 504

Declaring the status here rather than registering it in ``app.core.errors`` keeps the dependency
pointing one way: ``app.core`` never imports ``app.ai``.

That 503-vs-500 distinction is load-bearing. A missing provider is **not** a bug and must never
surface as a 500: it is a configuration state the platform is designed to degrade through, so it
maps to a retryable 5xx whose message names exactly what to configure.
"""

from __future__ import annotations

from typing import Any, Optional

from app.domain.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)


class AIError(DomainError):
    """Base class for every AI-platform failure.

    ``http_status`` is the hook :func:`app.core.errors.status_for` consults before its class table.
    ``None`` here means "fall through to the table", which is what the 4xx subclasses want.
    """

    code = "ai_error"
    http_status: Optional[int] = None


class AIValidationError(ValidationError):
    """Input to an AI operation violates a stated invariant (bad dimensions, empty query, …)."""

    code = "ai_validation_error"


class KnowledgeNotFoundError(NotFoundError):
    """A knowledge document, chunk, embedding, prompt or vendor profile does not exist."""


class AIConflictError(ConflictError):
    """The request conflicts with the current state of a knowledge artefact."""

    code = "ai_conflict"


class DocumentAlreadyIndexedError(AIConflictError):
    """Identical bytes are already indexed under this tenant.

    Carries the existing document id so the caller can link to it rather than retry: re-ingesting
    the same bytes is a no-op by design (idempotency is keyed on the content checksum).
    """

    code = "document_already_indexed"

    def __init__(self, checksum: str, existing_document_id: Any) -> None:
        super().__init__(
            "A document with identical content is already indexed.",
            details={"checksum": checksum, "existingDocumentId": str(existing_document_id)},
        )
        self.checksum = checksum
        self.existing_document_id = existing_document_id


class EmbeddingVersionConflictError(AIConflictError):
    """Vectors of two different embedding versions would be compared.

    Cosine similarity between vectors from different models is meaningless, so this is a hard error
    rather than a warning — it is the failure mode most likely to silently destroy retrieval
    quality.
    """

    code = "embedding_version_conflict"

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"Embedding version mismatch: index holds '{expected}', query produced '{actual}'. "
            "Re-index before querying, or pin the query to the indexed version.",
            details={"expectedVersion": expected, "actualVersion": actual},
        )
        self.expected = expected
        self.actual = actual


class DimensionMismatchError(AIValidationError):
    """A vector's length does not match the store's configured dimensionality."""

    code = "dimension_mismatch"

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Expected a {expected}-dimensional vector, received {actual}.",
            details={"expectedDimensions": expected, "actualDimensions": actual},
        )


class AIUnavailableError(AIError):
    """An AI capability exists but is currently unusable. Maps to **503**.

    Separate from :class:`ProviderError` because nothing failed — the platform is operating exactly
    as configured, and the fix is an operator action rather than a retry.
    """

    code = "ai_unavailable"
    http_status = 503


class ProviderNotConfiguredError(AIUnavailableError):
    """A capability was requested but its provider has no credentials or missing dependency.

    ``remedy`` names the concrete thing to install or set, because "provider not configured"
    without that is unactionable for whoever is on call.
    """

    code = "provider_not_configured"

    def __init__(self, provider: str, kind: str, remedy: str) -> None:
        super().__init__(
            f"{kind} provider '{provider}' is not configured. {remedy}",
            details={"provider": provider, "kind": kind, "remedy": remedy},
        )
        self.provider = provider
        self.kind = kind
        self.remedy = remedy


class ProviderError(AIError):
    """An upstream provider (embedding, LLM, rerank, vector store) failed."""

    code = "provider_error"
    http_status = 502

    def __init__(self, provider: str, message: str,
                 *, details: Optional[dict[str, Any]] = None) -> None:
        merged: dict[str, Any] = {"provider": provider}
        merged.update(details or {})
        super().__init__(f"Provider '{provider}' failed: {message}", details=merged)
        self.provider = provider


class ProviderTimeoutError(ProviderError):
    """A provider exceeded its configured deadline."""

    code = "provider_timeout"
    http_status = 504

    def __init__(self, provider: str, timeout_seconds: float) -> None:
        super().__init__(
            provider,
            f"timed out after {timeout_seconds:.1f}s",
            details={"timeoutSeconds": timeout_seconds},
        )


class UnsupportedDocumentError(AIValidationError):
    """No registered parser handles this MIME type / extension."""

    code = "unsupported_document"

    def __init__(self, mime_type: Optional[str], file_name: Optional[str],
                 supported: Optional[list[str]] = None) -> None:
        super().__init__(
            f"No parser is registered for '{mime_type or file_name or 'unknown'}'.",
            details={
                "mimeType": mime_type,
                "fileName": file_name,
                "supportedMimeTypes": sorted(supported or []),
            },
        )


class PIIRejectionError(AIValidationError):
    """The document contains PII of a kind this source is configured to reject."""

    code = "pii_rejected"

    def __init__(self, kinds: list[str]) -> None:
        super().__init__(
            "Document rejected: it contains personal data this knowledge source forbids "
            f"({', '.join(sorted(kinds))}).",
            details={"piiKinds": sorted(kinds)},
        )


class PromptRenderError(AIValidationError):
    """Strict prompt rendering failed — a declared variable was missing or an extra was supplied.

    Deliberately an error rather than a silent empty substitution: a prompt that renders with a
    blank where a policy limit should be is worse than one that fails loudly.
    """

    code = "prompt_render_error"

    def __init__(self, template: str, missing: list[str], unexpected: list[str]) -> None:
        parts = []
        if missing:
            parts.append(f"missing {sorted(missing)}")
        if unexpected:
            parts.append(f"unexpected {sorted(unexpected)}")
        super().__init__(
            f"Cannot render prompt '{template}': {'; '.join(parts) or 'invalid variables'}.",
            details={"template": template, "missing": sorted(missing),
                     "unexpected": sorted(unexpected)},
        )


class FeatureDisabledError(AIUnavailableError):
    """The requested AI capability is switched off by a feature flag."""

    code = "ai_feature_disabled"

    def __init__(self, flag: str) -> None:
        super().__init__(
            f"AI capability '{flag}' is disabled.",
            details={"flag": flag},
        )
        self.flag = flag


class ToolPermissionError(ForbiddenError):
    """A tool was invoked by a role that is not on its allowlist.

    Subclasses :class:`~app.domain.errors.ForbiddenError` so it maps to 403 through the existing
    table with no extra registration.
    """

    code = "tool_forbidden"

    def __init__(self, tool: str, role: str, allowed: list[str]) -> None:
        super().__init__(
            f"Role '{role}' may not invoke tool '{tool}'.",
            details={"tool": tool, "role": role, "allowedRoles": sorted(allowed)},
        )


__all__ = [
    "AIConflictError",
    "AIError",
    "AIUnavailableError",
    "AIValidationError",
    "DimensionMismatchError",
    "DocumentAlreadyIndexedError",
    "EmbeddingVersionConflictError",
    "FeatureDisabledError",
    "KnowledgeNotFoundError",
    "PIIRejectionError",
    "PromptRenderError",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderTimeoutError",
    "ToolPermissionError",
    "UnsupportedDocumentError",
]
