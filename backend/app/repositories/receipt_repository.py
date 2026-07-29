"""Receipt persistence.

The receipts feature (T001) previously ran its queries inline in the router. Those queries move
here so the route keeps no SQL, and so the claim service can ask the storage layer questions like
"is this receipt already spoken for?".
"""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select

from app.models.claim import Claim
from app.models.receipt import ExtractionStatus, Receipt
from app.repositories.base import BaseRepository


class ReceiptRepository(BaseRepository[Receipt]):
    model = Receipt

    def list_for_employee_code(
        self, employee_code: Optional[str], *, limit: Optional[int] = None, offset: int = 0
    ) -> Sequence[Receipt]:
        """Receipts newest-first, optionally scoped to one employee's external code."""
        stmt = select(Receipt).order_by(Receipt.created_at.desc())
        if employee_code:
            stmt = stmt.where(Receipt.employee_id == employee_code)
        return self._all(self._paginate(stmt, limit=limit, offset=offset))

    def list_by_status(
        self, status: ExtractionStatus, *, limit: Optional[int] = None
    ) -> Sequence[Receipt]:
        return self._all(
            self._paginate(
                select(Receipt)
                .where(Receipt.extraction_status == status)
                .order_by(Receipt.created_at.desc()),
                limit=limit,
            )
        )

    def is_claimed(self, receipt_id: uuid.UUID) -> bool:
        """Whether any claim already references this receipt."""
        return (
            self.session.execute(
                select(Claim.id).where(Claim.receipt_id == receipt_id).limit(1)
            ).first()
            is not None
        )

    def claim_using(self, receipt_id: uuid.UUID) -> Optional[Claim]:
        """The claim that owns this receipt, if any."""
        return self.session.execute(
            select(Claim).where(Claim.receipt_id == receipt_id)
        ).scalars().first()

    def link_employee(self, receipt: Receipt, employee_id: Optional[uuid.UUID]) -> Receipt:
        """Attach the resolved employee UUID alongside the external code."""
        receipt.employee_ref_id = employee_id
        self.session.flush()
        return receipt
