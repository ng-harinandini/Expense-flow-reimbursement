"""``PromptRegistry`` — the one place a prompt is published, rolled back, retrieved or rendered.

Publishing validates a template's declared ``variables`` against its actual ``{{...}}`` placeholders
before writing a row at all (see ``app.ai.prompts.rendering.validate_declared_variables`), and every
publish or rollback is audited through the existing ``AuditService`` — mirrors how
``PolicyRuleService.replace_ruleset`` audits every ruleset change, applied to prompts instead of
policy rules.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.models.prompt import PromptTemplate
from app.ai.prompts.rendering import render as render_template
from app.ai.prompts.rendering import validate_declared_variables
from app.ai.repositories.prompt_repository import PromptTemplateRepository
from app.domain.actor import Actor
from app.services.audit_service import AuditService

_ENTITY_TYPE = "PromptTemplate"


class PromptRegistry:
    def __init__(self, session: Session, *, audit_service: AuditService) -> None:
        self._repository = PromptTemplateRepository(session)
        self._audit = audit_service

    def publish(
        self,
        code: str,
        *,
        name: str,
        template_text: str,
        variables: list[str],
        actor: Actor,
        description: Optional[str] = None,
    ) -> PromptTemplate:
        """Publish the next version of ``code``. Raises
        :class:`~app.ai.core.errors.PromptRenderError` if ``variables`` does not exactly match the
        placeholders present in ``template_text``."""
        validate_declared_variables(code, template_text, variables)
        prompt = self._repository.publish_version(
            code=code, name=name, template_text=template_text, variables=variables,
            description=description, created_by_sub=actor.sub,
        )
        self._audit.record(
            actor=actor, action="AI_PROMPT_PUBLISHED", entity_type=_ENTITY_TYPE, entity_id=code,
            details=f"Published prompt '{code}' v{prompt.version} ('{name}').",
            after={"code": code, "version": prompt.version, "name": name},
        )
        return prompt

    def rollback(self, code: str, *, to_version: int, actor: Actor) -> PromptTemplate:
        """Re-point ``code``'s active version to ``to_version`` without deleting the version it
        demotes. Raises :class:`~app.ai.core.errors.KnowledgeNotFoundError` if ``to_version``
        does not exist."""
        prompt = self._repository.rollback(code, to_version=to_version)
        self._audit.record(
            actor=actor, action="AI_PROMPT_ROLLED_BACK", entity_type=_ENTITY_TYPE, entity_id=code,
            details=f"Rolled prompt '{code}' back to v{to_version}.",
            after={"code": code, "activeVersion": to_version},
        )
        return prompt

    def get_active(self, code: str) -> PromptTemplate:
        prompt = self._repository.get_active(code)
        if prompt is None:
            raise KnowledgeNotFoundError(_ENTITY_TYPE, code)
        return prompt

    def list_versions(self, code: str) -> Sequence[PromptTemplate]:
        return self._repository.list_versions(code)

    def render(self, code: str, **values: object) -> str:
        """Render ``code``'s currently active version with ``values``."""
        prompt = self.get_active(code)
        return render_template(code, prompt.template_text, prompt.variables, **values)


__all__ = ["PromptRegistry"]
