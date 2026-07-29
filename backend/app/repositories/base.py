"""Generic repository base.

Holds the CRUD mechanics every aggregate shares so concrete repositories contain only their own
query logic (Liskov-safe: a subclass never weakens these contracts).
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, Iterable, Optional, Sequence, Type, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.logging import get_logger
from app.domain.errors import NotFoundError

TModel = TypeVar("TModel", bound=Base)

logger = get_logger(__name__)


class BaseRepository(Generic[TModel]):
    """CRUD primitives for one mapped class.

    Subclasses set :attr:`model` and add query methods. ``flush`` (not ``commit``) is used
    throughout so ids and defaults are available immediately while the caller keeps control of
    the transaction.
    """

    model: Type[TModel]

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- reads ---------------------------------------------------------------

    def get(self, entity_id: uuid.UUID | str) -> Optional[TModel]:
        """Primary-key lookup. Returns ``None`` for a missing row or a malformed id."""
        key = self._coerce_uuid(entity_id)
        return self.session.get(self.model, key) if key is not None else None

    def get_or_raise(self, entity_id: uuid.UUID | str) -> TModel:
        """Primary-key lookup that raises :class:`NotFoundError` (→ HTTP 404) when absent."""
        entity = self.get(entity_id)
        if entity is None:
            raise NotFoundError(self.model.__name__, entity_id)
        return entity

    def list(self, *, limit: Optional[int] = None, offset: int = 0) -> Sequence[TModel]:
        return self._all(self._paginate(select(self.model), limit=limit, offset=offset))

    def count(self) -> int:
        return int(
            self.session.execute(
                select(func.count()).select_from(self.model)
            ).scalar_one()
        )

    def exists(self, entity_id: uuid.UUID | str) -> bool:
        return self.get(entity_id) is not None

    # --- writes --------------------------------------------------------------

    def add(self, entity: TModel) -> TModel:
        """Stage a new row and flush so its id/server defaults are populated."""
        self.session.add(entity)
        self.session.flush()
        return entity

    def add_all(self, entities: Iterable[TModel]) -> list[TModel]:
        items = list(entities)
        self.session.add_all(items)
        self.session.flush()
        return items

    def update(self, entity: TModel, **changes: Any) -> TModel:
        """Assign ``changes`` to ``entity`` and flush.

        Unknown attribute names raise ``AttributeError`` rather than being silently dropped, so a
        typo in a service surfaces as a bug instead of a missing update.
        """
        for field, value in changes.items():
            if not hasattr(entity, field):
                raise AttributeError(
                    f"{self.model.__name__} has no attribute '{field}'."
                )
            setattr(entity, field, value)
        self.session.flush()
        return entity

    def delete(self, entity: TModel) -> None:
        """Hard delete. Not used for auditable business entities — see ``audit_logs``."""
        self.session.delete(entity)
        self.session.flush()

    def refresh(self, entity: TModel) -> TModel:
        """Flush pending writes and expire ``entity`` so it re-reads on next access.

        Needed after a multi-step write: child rows inserted during the operation are not appended
        to already-loaded relationship collections, so serializing the aggregate without this would
        omit them (or list them out of their declared order). Expiring defers a single reload to
        the next attribute access, in the ordering the mapper declares.
        """
        self.session.flush()
        self.session.expire(entity)
        return entity

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _coerce_uuid(value: uuid.UUID | str | None) -> Optional[uuid.UUID]:
        """Best-effort UUID parse. ``None`` for anything unparseable, so callers can 404."""
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _paginate(stmt: Select, *, limit: Optional[int], offset: int = 0) -> Select:
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return stmt

    def _all(self, stmt: Select) -> Sequence[TModel]:
        return self.session.execute(stmt).unique().scalars().all()

    def _one_or_none(self, stmt: Select) -> Optional[TModel]:
        return self.session.execute(stmt).unique().scalars().first()
