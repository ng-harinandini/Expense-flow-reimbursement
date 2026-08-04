"""``PersistedFeatureFlagStore`` — the audited, durable layer above
:data:`app.ai.registry.flags.feature_flags`'s in-process-only override store.

``FeatureFlags.set_override`` is deliberately in-process only — its own docstring: "an emergency AI
kill switch must take effect without a database write succeeding first." This module does not
change that fast path; it adds a second, audited write *in front of* it, so a deliberate (not
emergency) flag change is durable and attributable, and every override survives a process restart
via :meth:`PersistedFeatureFlagStore.load_overrides`.

Flag validation is delegated entirely to ``FeatureFlags.set_override`` itself (it raises
``ValueError`` on an unknown flag name) — called *before* any database write, so an invalid flag
name never leaves an orphaned, never-applied row in ``ai_flag_overrides``.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.ai.registry.flags import FeatureFlags
from app.ai.registry.flags import feature_flags as _default_flags
from app.ai.repositories.governance_repository import FlagOverrideRepository
from app.domain.actor import Actor
from app.services.audit_service import AuditService

_ENTITY_TYPE = "FeatureFlag"


class PersistedFeatureFlagStore:
    def __init__(
        self,
        session: Session,
        *,
        audit_service: AuditService,
        flags: Optional[FeatureFlags] = None,
    ) -> None:
        self._repository = FlagOverrideRepository(session)
        self._audit = audit_service
        self._flags = flags or _default_flags

    def set(self, flag_name: str, enabled: bool, *, actor: Actor) -> None:
        """Apply the override in-process immediately, then persist and audit it."""
        self._flags.set_override(flag_name, enabled)  # validates; raises before any DB write
        self._repository.upsert(flag_name, enabled=enabled, updated_by_sub=actor.sub)
        self._audit.record(
            actor=actor, action="AI_FLAG_OVERRIDDEN", entity_type=_ENTITY_TYPE,
            entity_id=flag_name, details=f"Set '{flag_name}' override to {enabled}.",
            after={"flag": flag_name, "enabled": enabled},
        )

    def clear(self, flag_name: str, *, actor: Actor) -> None:
        """Revert ``flag_name`` to its configuration default, in-process and durably."""
        self._flags.set_override(flag_name, None)  # validates; raises before any DB write
        removed = self._repository.clear(flag_name)
        if removed:
            self._audit.record(
                actor=actor, action="AI_FLAG_OVERRIDE_CLEARED", entity_type=_ENTITY_TYPE,
                entity_id=flag_name,
                details=f"Cleared override for '{flag_name}', reverting to its configured default.",
            )

    def load_overrides(self) -> int:
        """Rehydrate the in-process store from every persisted override.

        Call once per process at startup (or once per request if a shared in-process store is not
        used) — a restart must not silently lose a durably-set override.
        """
        overrides = self._repository.list_all()
        for override in overrides:
            self._flags.set_override(override.flag_name, override.enabled)
        return len(overrides)


__all__ = ["PersistedFeatureFlagStore"]
