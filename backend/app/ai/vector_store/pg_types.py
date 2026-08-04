"""SQLAlchemy column type for pgvector's ``vector`` — hand-rolled, no third-party driver.

Two reasons this is not ``pgvector.sqlalchemy.Vector``:

1. **The architecture rule.** ``tests/test_ai_architecture.py`` forbids anything outside
   ``app/ai/vector_store`` from importing a vector driver. ``app/ai/models/knowledge.py`` has to
   declare the column, so the type must be defined *here* and imported from there — importing
   ``pgvector`` in the models package would be a violation of the platform's own boundary.
2. **No new dependency.** pgvector's wire format is plain text — ``[1.5,2,3]`` — so the whole
   adapter is two processors. Adding a package to emit that would be unjustified weight, and this
   keeps the SQL we generate fully visible.

The type degrades honestly: ``vector`` only exists once ``CREATE EXTENSION vector`` has run, which
migration ``0004`` does. On a database without the extension the DDL fails loudly at migration time
rather than silently storing something else.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import cast, literal
from sqlalchemy.types import Float as FloatType, Text, UserDefinedType


class Vector(UserDefinedType):
    """``vector(n)`` column holding a fixed-width list of float32.

    ``dimensions`` is baked into the DDL because pgvector requires it for an HNSW index, and because
    a mismatched vector must fail at the database rather than be silently padded or truncated.
    Changing the embedding model's dimensionality is therefore a migration — which is correct: the
    existing vectors are not comparable to the new ones anyway
    (:class:`~app.ai.core.errors.EmbeddingVersionConflictError`).
    """

    cache_ok = True

    def __init__(self, dimensions: Optional[int] = None) -> None:
        if dimensions is not None and dimensions < 1:
            raise ValueError("Vector dimensions must be >= 1.")
        self.dimensions = dimensions

    def get_col_spec(self, **_: Any) -> str:
        return "vector" if self.dimensions is None else f"vector({self.dimensions})"

    def bind_processor(self, dialect: Any):
        """Python sequence -> pgvector text literal.

        ``repr`` is deliberately avoided in favour of ``float()`` formatting so a numpy scalar or a
        Decimal cannot leak a non-numeric token into the literal.
        """
        dimensions = self.dimensions

        def process(value: Optional[Sequence[float]]) -> Optional[str]:
            if value is None:
                return None
            values = [float(v) for v in value]
            if dimensions is not None and len(values) != dimensions:
                raise ValueError(
                    f"Expected a {dimensions}-dimensional vector, received {len(values)}."
                )
            return "[" + ",".join(repr(v) for v in values) + "]"

        return process

    def result_processor(self, dialect: Any, coltype: Any):
        """pgvector text literal -> tuple of float.

        A tuple, not a list, so a vector read back out of the database is hashable and immutable in
        exactly the way :class:`app.ai.core.types.EmbeddingVector` requires.
        """

        def process(value: Any) -> Optional[tuple[float, ...]]:
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                return tuple(float(v) for v in value)
            text = str(value).strip()
            if text.startswith("[") and text.endswith("]"):
                text = text[1:-1]
            if not text:
                return ()
            return tuple(float(part) for part in text.split(","))

        return process

    class comparator_factory(UserDefinedType.Comparator):
        """The three pgvector distance operators, as SQLAlchemy expressions.

        Exposed as methods rather than left to raw SQL so the store can build a filtered,
        parameterised ``ORDER BY`` without string interpolation — which is what keeps a
        user-supplied query text from reaching the SQL text at all.
        """

        def cosine_distance(self, other: Any):
            """``<=>`` — 0.0 identical, 1.0 orthogonal, 2.0 opposite."""
            return self.op("<=>", return_type=FloatType)(other)

        def l2_distance(self, other: Any):
            """``<->`` — Euclidean."""
            return self.op("<->", return_type=FloatType)(other)

        def negative_inner_product(self, other: Any):
            """``<#>`` — negated so that smaller is nearer, as with the other two."""
            return self.op("<#>", return_type=FloatType)(other)


def vector_literal(values: Sequence[float]) -> str:
    """Render a vector as the text form pgvector accepts, for raw SQL and for tests."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def as_vector_param(values: Sequence[float], dimensions: int):
    """A bound parameter cast to ``vector(n)``, for the query side of a similarity search.

    Needed because a bare bound array reaches PostgreSQL untyped and ``<=>`` cannot resolve an
    without knowing both sides are vectors. The value travels as a *bound parameter*, never
    interpolated into the SQL text.

    The inner literal is explicitly typed ``Text``. Without that, SQLAlchemy infers the parameter's
    type from the cast's target — ``Vector`` — and hands the already-rendered ``"[1.0,2.0]"`` string
    to this type's own bind processor, which iterates it and tries to read ``"["`` as a float. The
    rendering has happened by then; the parameter must not be processed a second time.
    """
    return cast(literal(vector_literal(values), type_=Text), Vector(dimensions))


__all__ = ["Vector", "as_vector_param", "vector_literal"]
