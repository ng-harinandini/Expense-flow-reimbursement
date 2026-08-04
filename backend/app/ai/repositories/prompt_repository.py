"""Prompt template persistence.

Mirrors ``app.repositories.policy_rule_repository.PolicyRuleRepository`` exactly, with one
addition: :meth:`PromptTemplateRepository.rollback`, which this macro's own spec calls for and the
``PolicyRule`` precedent does not have. Rows are never edited in place and never deleted —
:meth:`publish_version` retires the current ``PUBLISHED`` version and inserts the next; ``rollback``
re-points which version is ``PUBLISHED`` without deleting or renumbering anything.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select

from app.ai.core.enums import PromptStatus
from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.models.prompt import PromptTemplate
from app.repositories.base import BaseRepository


class PromptTemplateRepository(BaseRepository[PromptTemplate]):
    model = PromptTemplate

    # --- reads -----------------------------------------------------------------

    def get_active(self, code: str) -> Optional[PromptTemplate]:
        """The currently ``PUBLISHED`` version of ``code``, or ``None``."""
        return self._one_or_none(
            select(PromptTemplate).where(
                PromptTemplate.code == code, PromptTemplate.status == PromptStatus.PUBLISHED
            )
        )

    def get_by_code(self, code: str, *, version: Optional[int] = None) -> Optional[PromptTemplate]:
        stmt = select(PromptTemplate).where(PromptTemplate.code == code)
        if version is not None:
            stmt = stmt.where(PromptTemplate.version == version)
        else:
            stmt = stmt.order_by(PromptTemplate.version.desc())
        return self._one_or_none(stmt)

    def list_versions(self, code: str) -> Sequence[PromptTemplate]:
        """Every version of one prompt, newest first — the change history."""
        return self._all(
            select(PromptTemplate)
            .where(PromptTemplate.code == code)
            .order_by(PromptTemplate.version.desc())
        )

    def latest_version_number(self, code: str) -> int:
        """Highest existing version for ``code``, or ``0`` when the prompt is new."""
        return int(
            self.session.execute(
                select(func.coalesce(func.max(PromptTemplate.version), 0)).where(
                    PromptTemplate.code == code
                )
            ).scalar_one()
        )

    # --- writes ------------------------------------------------------------------

    def publish_version(
        self,
        *,
        code: str,
        name: str,
        template_text: str,
        variables: Optional[list[str]] = None,
        description: Optional[str] = None,
        created_by_sub: Optional[str] = None,
    ) -> PromptTemplate:
        """Publish the next version of ``code``, retiring the current ``PUBLISHED`` one.

        Existing rows are never mutated beyond their ``status``, so superseded prompt text stays
        queryable via :meth:`list_versions`.
        """
        next_version = self.latest_version_number(code) + 1

        current = self.get_active(code)
        if current is not None:
            current.status = PromptStatus.RETIRED

        prompt = PromptTemplate(
            code=code,
            version=next_version,
            status=PromptStatus.PUBLISHED,
            name=name,
            description=description,
            template_text=template_text,
            variables=list(variables or []),
            created_by_sub=created_by_sub,
        )
        self.session.add(prompt)
        self.session.flush()
        return prompt

    def rollback(self, code: str, *, to_version: int) -> PromptTemplate:
        """Re-point the active pointer to ``to_version``, retiring whichever version is currently
        ``PUBLISHED`` (if different). Never deletes or renumbers anything — the demoted version
        stays retrievable at its own version number, marked ``RETIRED``.

        Raises :class:`~app.ai.core.errors.KnowledgeNotFoundError` if ``to_version`` does not
        exist for ``code``.
        """
        target = self.get_by_code(code, version=to_version)
        if target is None:
            raise KnowledgeNotFoundError("PromptTemplate", f"{code}@v{to_version}")

        if target.status != PromptStatus.PUBLISHED:
            current = self.get_active(code)
            if current is not None and current.id != target.id:
                current.status = PromptStatus.RETIRED
            target.status = PromptStatus.PUBLISHED
            self.session.flush()
        return target


__all__ = ["PromptTemplateRepository"]
