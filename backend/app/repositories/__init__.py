"""Repository layer — the only code that talks to the database.

One repository per aggregate. Rules that hold everywhere in this package:

* A repository receives an already-open :class:`~sqlalchemy.orm.Session` (constructor injection);
  it never creates, commits, or rolls back one. **Transaction boundaries belong to the caller** —
  in HTTP terms, to the service invoked by the route — so a business action and its audit record
  commit or fail together.
* Reads return ORM objects or ``None``; they never raise for "absent". Deciding that absence is an
  error is the service's job (it has the caller context needed for a good message).
* No HTTP types, no ``HTTPException``, no serialization. Repositories raise
  :mod:`app.domain.errors` only where an invariant is genuinely storage-level (an illegal
  lifecycle transition, a lost optimistic lock).
* Business orchestration lives in services, not here. The one deliberate exception is
  ``ClaimRepository.transition_status``, which is the *sole* writer of ``Claim.status`` precisely so
  the state machine, history row, and timestamps cannot be bypassed.
"""

from app.repositories.ai_inference_repository import AIInferenceRepository  # noqa: F401
from app.repositories.audit_repository import AuditLogRepository  # noqa: F401
from app.repositories.base import BaseRepository  # noqa: F401
from app.repositories.claim_repository import ClaimQuery, ClaimRepository  # noqa: F401
from app.repositories.employee_repository import (  # noqa: F401
    DepartmentRepository,
    EmployeeRepository,
)
from app.repositories.fraud_repository import FraudResultRepository  # noqa: F401
from app.repositories.policy_rule_repository import PolicyRuleRepository  # noqa: F401
from app.repositories.receipt_repository import ReceiptRepository  # noqa: F401
from app.repositories.workflow_repository import ApprovalWorkflowRepository  # noqa: F401

__all__ = [
    "AIInferenceRepository",
    "ApprovalWorkflowRepository",
    "AuditLogRepository",
    "BaseRepository",
    "ClaimQuery",
    "ClaimRepository",
    "DepartmentRepository",
    "EmployeeRepository",
    "FraudResultRepository",
    "PolicyRuleRepository",
    "ReceiptRepository",
]
