"""Domain error hierarchy.

Services and repositories raise these instead of ``HTTPException`` so the domain layer stays
transport-agnostic. ``app.core.errors`` registers the handlers that translate each one into its
HTTP status code, so a route never has to catch them.

Status mapping (see ``app.core.errors.STATUS_BY_ERROR``):

    ValidationError            -> 422 Unprocessable Entity
    NotFoundError              -> 404 Not Found
    ForbiddenError             -> 403 Forbidden
    ConflictError              -> 409 Conflict
      InvalidStateTransition   -> 409
      DuplicateClaimError      -> 409
      ReceiptAlreadyClaimed    -> 409
      ImmutableEntityError     -> 409
      ConcurrentUpdateError    -> 409
"""

from __future__ import annotations

from typing import Any, Optional


class DomainError(Exception):
    """Base class for every expected business-rule failure.

    ``code`` is a stable machine-readable identifier (safe to branch on in clients);
    ``details`` carries structured context that is safe to return to the caller.
    """

    code: str = "domain_error"

    def __init__(self, message: str, *, code: Optional[str] = None,
                 details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"detail": self.message, "code": self.code}
        if self.details:
            payload["context"] = self.details
        return payload

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ValidationError(DomainError):
    """A business invariant on the submitted data is violated."""

    code = "validation_error"


class NotFoundError(DomainError):
    """A referenced entity does not exist (or is not visible to the caller)."""

    code = "not_found"

    def __init__(self, entity: str, identifier: Any) -> None:
        super().__init__(
            f"{entity} not found.",
            details={"entity": entity, "id": str(identifier)},
        )
        self.entity = entity
        self.identifier = identifier


class ForbiddenError(DomainError):
    """The caller is authenticated but not permitted to perform this action."""

    code = "forbidden"


class ConflictError(DomainError):
    """The request conflicts with the current state of the resource."""

    code = "conflict"


class InvalidStateTransitionError(ConflictError):
    """The requested claim lifecycle transition is not legal from the current state."""

    code = "invalid_state_transition"

    def __init__(self, entity: str, current: str, target: str,
                 allowed: Optional[list[str]] = None) -> None:
        super().__init__(
            f"{entity} cannot move from '{current}' to '{target}'.",
            details={
                "entity": entity,
                "currentStatus": current,
                "requestedStatus": target,
                "allowedNextStatuses": allowed or [],
            },
        )
        self.current = current
        self.target = target


class DuplicateClaimError(ConflictError):
    """An equivalent claim already exists for this employee/vendor/date/amount."""

    code = "duplicate_claim"


class ReceiptAlreadyClaimedError(ConflictError):
    """The receipt is already attached to another claim."""

    code = "receipt_already_claimed"


class ImmutableEntityError(ConflictError):
    """The entity has reached a state in which the requested field may no longer change."""

    code = "immutable_entity"


class ConcurrentUpdateError(ConflictError):
    """Optimistic-lock failure: another writer changed the row first. Safe to retry."""

    code = "concurrent_update"

    def __init__(self, entity: str, identifier: Any) -> None:
        super().__init__(
            f"{entity} was modified by another request. Reload and retry.",
            details={"entity": entity, "id": str(identifier)},
        )


__all__ = [
    "ConcurrentUpdateError",
    "ConflictError",
    "DomainError",
    "DuplicateClaimError",
    "ForbiddenError",
    "ImmutableEntityError",
    "InvalidStateTransitionError",
    "NotFoundError",
    "ReceiptAlreadyClaimedError",
    "ValidationError",
]
