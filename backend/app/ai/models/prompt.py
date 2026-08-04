"""ORM model for the prompt registry: versioned, immutable, auditable prompt templates.

Mirrors ``app/models/policy.py``'s ``PolicyRule`` pattern (Alembic sole schema owner; ADR-001) —
publishing a change inserts a new version rather than editing a row in place — with one deliberate
difference: ``PolicyRule`` has no rollback (a reverted rule is republished forward as a new
version), but this macro's own spec explicitly calls for one, so ``PromptStatus``
(``DRAFT``/``PUBLISHED``/``RETIRED``, built in M1 anticipating exactly this table) is the
discriminator instead of a bare ``is_active`` boolean — rollback re-points which version is
``PUBLISHED`` without deleting, renumbering, or losing the version it demotes.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.ai.core.enums import PromptStatus, prompt_status_enum
from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PromptTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One version of one prompt. Immutable once published — see
    ``app.ai.prompts.registry.PromptRegistry.publish``/``rollback``."""

    __tablename__ = "ai_prompt_templates"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_ai_prompt_templates_code_version"),
        CheckConstraint("version > 0", name="ck_ai_prompt_templates_version_positive"),
        CheckConstraint(
            "length(btrim(template_text)) > 0", name="ck_ai_prompt_templates_text_not_blank"
        ),
        Index("ix_ai_prompt_templates_code_status", "code", "status"),
    )

    # Stable identity of the prompt across versions (e.g. "POLICY_EXPLANATION").
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[PromptStatus] = mapped_column(
        prompt_status_enum, nullable=False, server_default=PromptStatus.PUBLISHED.value
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Declared variables the template requires. Rendering against this list is strict — a missing
    # or an extra variable is a hard error (app.ai.prompts.rendering.PromptRenderError), never a
    # silent blank substitution.
    variables: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PromptTemplate {self.code} v{self.version} {self.status.value}>"


__all__ = ["PromptTemplate"]
