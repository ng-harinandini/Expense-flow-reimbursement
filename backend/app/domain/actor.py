"""The identity performing an action, as the domain sees it.

Services take an :class:`Actor` rather than the transport's ``CurrentUser`` so the domain and
service layers stay free of FastAPI and of Cognito specifics. Routes convert once, at the edge,
via :meth:`Actor.from_current_user`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.domain.claim_state_machine import SYSTEM_ROLE


@dataclass(frozen=True)
class Actor:
    """Who is acting, and under which application role."""

    role: str
    sub: Optional[str] = None
    email: Optional[str] = None
    employee_code: Optional[str] = None
    display_name: Optional[str] = None

    @property
    def name(self) -> str:
        """Best available human label for audit and history rows."""
        return self.display_name or self.email or self.sub or self.role

    @property
    def is_system(self) -> bool:
        return self.role == SYSTEM_ROLE

    @classmethod
    def from_current_user(cls, user: Any) -> "Actor":
        """Build from an authenticated caller (``app.core.deps.CurrentUser``).

        Duck-typed on purpose: the domain does not import the auth module.
        """
        return cls(
            role=getattr(user, "role", None) or "",
            sub=getattr(user, "sub", None),
            email=getattr(user, "email", None),
            employee_code=getattr(user, "employee_id", None),
            display_name=getattr(user, "display_name", None),
        )

    @classmethod
    def system(cls, name: str = "ExpenseFlow Workflow Engine") -> "Actor":
        """The actor used for machine-driven lifecycle steps (routing, screening).

        Never constructed from a token: ``SYSTEM_ROLE`` is not a Cognito role, so a caller cannot
        impersonate it.
        """
        return cls(role=SYSTEM_ROLE, display_name=name)

    def with_name(self, display_name: str) -> "Actor":
        """Copy carrying a resolved display name (e.g. the employee's full name)."""
        return Actor(
            role=self.role,
            sub=self.sub,
            email=self.email,
            employee_code=self.employee_code,
            display_name=display_name,
        )
