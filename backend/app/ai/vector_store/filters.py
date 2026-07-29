"""Store-agnostic metadata filtering — one DSL, translated per engine.

A :class:`~app.ai.core.types.MetadataFilter` is deliberately neither SQL nor a vendor filter object.
It is a declarative predicate that every adapter translates into its own dialect, which is what
lets the identical query run against pgvector, Qdrant or OpenSearch and mean the same thing. This
module owns three things:

1. **The vocabulary** — which fields are filterable and which operators exist. Both are closed sets.
   An unknown field or operator raises rather than being ignored, because a filter that is silently
   dropped *widens* the result set: a query intended to return only Germany's policy quietly returns
   every country's, and nothing about the response says so.
2. **The semantics** — precisely what each operator means, including how it treats missing values.
   These are defined once here rather than per adapter, and pinned by the shared contract suite, so
   two stores cannot disagree about what ``ne`` means.
3. **Two reference translations** — to SQLAlchemy predicates (used by both PostgreSQL-backed stores)
   and to a Python predicate (used by the in-memory store and by any adapter whose engine cannot
   express a clause natively). External adapters translate to their own dialect and are held to the
   same semantics by the contract suite.

**Missing values.** ``eq``/``in``/comparisons never match a row whose value is absent, as in
SQL. ``ne``/``nin`` *do* match absent values, unlike SQL. That asymmetry is deliberate: in SQL
``country <> 'US'`` evaluates to NULL for a chunk with no country, so the row drops out — meaning
``eq 'US'`` and ``ne 'US'`` together would not cover the corpus, and a filter written to exclude one
country would silently also discard every unlabelled chunk. Callers who want "has a country, and it
is not US" write two filters.

**Conjunction only.** A sequence of filters is ANDed. Per-field disjunction uses ``in``.
There is no OR/NOT tree, because nothing in the platform needs one yet and every adapter would have
to grow a translator for it; the seam to add one later is this module, not the adapters, since they
all consume :func:`compile_to_sql` or :func:`matches`.

**Scope boundary.** Only fields physically present on ``knowledge_chunks`` are filterable here.
Document-level predicates (status, version, supersession) and effective-dating are *policy*, not
storage: they belong to ``app/ai/retrieval/filters.py`` (M7), which composes them from the
repository's ``_effective_on`` helper and a document-scoped subquery. Keeping them out of the DSL
keeps the external adapters honest — they store chunk metadata, not the document table.

``tenant_id`` is intentionally **not** filterable. Tenancy is a mandatory separate argument on every
store method; exposing it as a filter field would let ``MetadataFilter("tenant_id", "ne", "acme")``
broaden a search across tenants, which is the worst failure this layer can produce.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import ColumnElement, and_, or_

from app.ai.core.errors import AIValidationError
from app.ai.core.types import Chunk, MetadataFilter
from app.ai.models.knowledge import KnowledgeChunk
from app.models.enums import _WireEnum

# Prefix for addressing a key inside the ``chunk_metadata`` JSONB blob, e.g. ``extra.cost_centre``.
# Explicit rather than "any unknown name falls through to JSONB", so a typo in a promoted field name
# is an error instead of a filter that matches nothing.
EXTRA_PREFIX = "extra."


class FilterOp(_WireEnum):
    """The closed operator vocabulary. Values match ``MetadataFilter.op`` spellings."""

    EQ = "eq"
    NE = "ne"
    IN = "in"
    NIN = "nin"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    EXISTS = "exists"


class FieldKind(_WireEnum):
    """What a field holds. Decides coercion, and what ``contains`` means for it."""

    TEXT = "text"
    INT = "int"
    DATE = "date"
    UUID = "uuid"
    ARRAY = "array"


@dataclass(frozen=True, slots=True)
class FilterField:
    """One filterable field: its storage column and its in-memory accessor.

    Both are recorded together so the SQL and Python translations cannot drift apart — a field added
    to one and forgotten in the other is a store that returns different results for the same filter.
    """

    name: str
    kind: FieldKind
    column: str
    # : Attribute path on :class:`~app.ai.core.types.Chunk`, dotted. ``metadata.country`` reads
    # : ``chunk.metadata.country``; ``index`` reads ``chunk.index``.
    path: str
    # : Operators that make sense for the field. ``contains`` on an INT, for instance, does not.
    ops: frozenset[FilterOp] = frozenset()


_SCALAR_OPS = frozenset({
    FilterOp.EQ, FilterOp.NE, FilterOp.IN, FilterOp.NIN, FilterOp.EXISTS,
})
_ORDERED_OPS = _SCALAR_OPS | frozenset({FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE})
_TEXT_OPS = _SCALAR_OPS | frozenset({FilterOp.CONTAINS})
_ARRAY_OPS = frozenset({FilterOp.CONTAINS, FilterOp.EXISTS})


def _field(name: str, kind: FieldKind, column: str, path: str, ops: frozenset[FilterOp]):
    return FilterField(name=name, kind=kind, column=column, path=path, ops=ops)


# The filterable surface. Names mirror ``ChunkMetadata`` attribute names so the DSL, the API and the
# value type all use one vocabulary, rather than three that need mapping tables between them.
FIELDS: Mapping[str, FilterField] = {
    f.name: f
    for f in (
        _field("document_id", FieldKind.UUID, "document_id", "metadata.document_id", _SCALAR_OPS),
        _field("parent_id", FieldKind.UUID, "parent_chunk_id", "parent_id", _SCALAR_OPS),
        _field("index", FieldKind.INT, "chunk_index", "index", _ORDERED_OPS),
        _field("token_count", FieldKind.INT, "content_tokens", "token_count", _ORDERED_OPS),
        _field("page_number", FieldKind.INT, "page_number", "metadata.page_number", _ORDERED_OPS),
        _field("strategy", FieldKind.TEXT, "strategy", "strategy", _SCALAR_OPS),
        _field("checksum", FieldKind.TEXT, "checksum_sha256", "metadata.checksum", _SCALAR_OPS),
        _field("content", FieldKind.TEXT, "content", "text", _TEXT_OPS),
        _field("section", FieldKind.TEXT, "section", "metadata.section", _TEXT_OPS),
        _field("owner", FieldKind.TEXT, "owner", "metadata.owner", _TEXT_OPS),
        _field("department", FieldKind.TEXT, "department", "metadata.department", _SCALAR_OPS),
        _field("country", FieldKind.TEXT, "country", "metadata.country", _SCALAR_OPS),
        _field("currency", FieldKind.TEXT, "currency", "metadata.currency", _SCALAR_OPS),
        _field("category", FieldKind.TEXT, "category", "metadata.category", _SCALAR_OPS),
        _field("language", FieldKind.TEXT, "language", "metadata.language", _SCALAR_OPS),
        _field("source_type", FieldKind.TEXT, "source_type", "metadata.source_type", _SCALAR_OPS),
        _field(
            "policy_version", FieldKind.TEXT, "policy_version", "metadata.policy_version",
            _SCALAR_OPS,
        ),
        _field(
            "effective_date", FieldKind.DATE, "effective_date", "metadata.effective_date",
            _ORDERED_OPS,
        ),
        _field("expiry_date", FieldKind.DATE, "expiry_date", "metadata.expiry_date", _ORDERED_OPS),
        _field("tags", FieldKind.ARRAY, "tags", "metadata.tags", _ARRAY_OPS),
    )
}

# Named so the error message can point at the mistake rather than just refusing.
FILTERABLE_FIELDS: tuple[str, ...] = tuple(sorted(FIELDS))

# Never filterable, with a specific reason — a pointed error beats "unknown field" when someone
# reaches for one of these.
_REFUSED_FIELDS: Mapping[str, str] = {
    "tenant_id": (
        "Tenancy is a mandatory argument on every store method, not a filter. A filter could be "
        "written to widen the search across tenants."
    ),
    "spec_key": (
        "The embedding version is taken from the query vector, so a search can never compare "
        "vectors produced by two different models."
    ),
    "embedding": "Vectors are matched by similarity, not by equality.",
    "status": (
        "Document status is a document-level predicate. Use the retrieval layer's document "
        "scoping (app.ai.retrieval.filters), which joins knowledge_documents."
    ),
    "document_version": (
        "Document version lives on knowledge_documents, not on the chunk. See the retrieval "
        "layer's document scoping."
    ),
}


class FilterError(AIValidationError):
    """An unusable filter: unknown field, wrong operator, or a value that will not coerce.

    A subclass of ``AIValidationError`` so it reaches the API as a 422 naming the offending field,
    rather than a 500 from somewhere inside a translation.
    """

    code = "filter_invalid"


# ---------------------------------------------------------------------------
# validation and coercion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NormalizedFilter:
    """A validated filter: known field, known operator, coerced value.

    Adapters consume this rather than the raw ``MetadataFilter`` so that each one does not repeat
    validation — and, more importantly, cannot skip it.
    """

    field: FilterField
    op: FilterOp
    value: Any
    # : For ``extra.*`` filters, the dotted key path inside the JSONB blob. Empty for real columns.
    json_path: tuple[str, ...] = ()

    @property
    def is_json(self) -> bool:
        return bool(self.json_path)

    def describe(self) -> str:
        """Human-readable form, for telemetry and the retrieval explanation payload."""
        name = "extra." + ".".join(self.json_path) if self.is_json else self.field.name
        return f"{name} {self.op.value} {self.value!r}"


def normalize_filters(filters: Sequence[MetadataFilter]) -> tuple[NormalizedFilter, ...]:
    """Validate and coerce every filter, or raise.

    All-or-nothing on purpose: applying the valid subset of a filter list and discarding the rest
    would answer a question nobody asked, with no indication in the response that it happened.
    """
    return tuple(normalize_filter(f) for f in filters or ())


def normalize_filter(spec: MetadataFilter) -> NormalizedFilter:
    """Validate one filter and coerce its value to the field's type."""
    raw_name = (spec.field_name or "").strip()
    if not raw_name:
        raise FilterError("A filter must name a field.")

    try:
        op = FilterOp.coerce(spec.op)
    except ValueError as exc:
        raise FilterError(str(exc)) from exc

    if raw_name.startswith(EXTRA_PREFIX):
        return _normalize_json_filter(raw_name, op, spec.value)

    reason = _REFUSED_FIELDS.get(raw_name)
    if reason is not None:
        raise FilterError(f"'{raw_name}' is not filterable. {reason}")

    field = FIELDS.get(raw_name)
    if field is None:
        raise FilterError(
            f"Unknown filter field '{raw_name}'. Filterable fields: "
            f"{', '.join(FILTERABLE_FIELDS)}. Use the '{EXTRA_PREFIX}' prefix for a key inside "
            "the chunk's metadata blob."
        )
    if op not in field.ops:
        raise FilterError(
            f"Operator '{op.value}' does not apply to '{field.name}' "
            f"({field.kind.value}). Supported: {', '.join(sorted(o.value for o in field.ops))}."
        )

    return NormalizedFilter(field=field, op=op, value=_coerce(field, op, spec.value))


def _normalize_json_filter(raw_name: str, op: FilterOp, value: Any) -> NormalizedFilter:
    """A filter addressing ``chunk_metadata`` JSONB, e.g. ``extra.cost_centre``.

    Values are compared as text, because JSONB has no schema to coerce against: guessing that
    ``"12"`` means the number 12 would make the same filter behave differently depending on how the
    ingestion happened to write the key.
    """
    path = tuple(part for part in raw_name[len(EXTRA_PREFIX):].split(".") if part)
    if not path:
        raise FilterError(
            f"'{raw_name}' names no key. Use '{EXTRA_PREFIX}<key>' or "
            f"'{EXTRA_PREFIX}<key>.<subkey>'."
        )
    if op is FilterOp.CONTAINS:
        raise FilterError(
            "'contains' does not apply to a metadata blob key: its element type is unknown. "
            "Promote the field to a column, or filter with 'eq'."
        )
    pseudo = FilterField(name=raw_name, kind=FieldKind.TEXT, column="chunk_metadata", path="")
    return NormalizedFilter(
        field=pseudo, op=op, value=_coerce(pseudo, op, value), json_path=path
    )


def _coerce(field: FilterField, op: FilterOp, value: Any) -> Any:
    """Convert an incoming value to the field's Python type.

    Filters arrive from JSON as often as from Python, so ``"2026-01-01"`` must become a ``date`` and
    a UUID string a ``UUID`` — otherwise the comparison happens between a string and a typed column
    and quietly matches nothing.
    """
    if op is FilterOp.EXISTS:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
            raise FilterError(f"'exists' expects a boolean, received {value!r}.")
        return bool(value)

    if op in (FilterOp.IN, FilterOp.NIN):
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise FilterError(
                f"Operator '{op.value}' on '{field.name}' expects a list of values, "
                f"received {type(value).__name__}."
            )
        items = [_coerce_scalar(field, item) for item in value]
        if not items:
            # An empty IN matches nothing in SQL but is almost always a caller bug (an unfiltered
            # id list that came back empty). Refusing it surfaces that instead of returning zero
            # results that look like "no matching documents".
            raise FilterError(
                f"Operator '{op.value}' on '{field.name}' was given an empty list. An empty list "
                "cannot match anything; omit the filter instead."
            )
        return tuple(items)

    return _coerce_scalar(field, value)


def _coerce_scalar(field: FilterField, value: Any) -> Any:
    if value is None:
        raise FilterError(
            f"Filter on '{field.name}' has no value. Use the 'exists' operator to test for absence."
        )

    kind = field.kind
    try:
        if kind is FieldKind.UUID:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        if kind is FieldKind.INT:
            if isinstance(value, bool):
                raise ValueError("a boolean is not an integer")
            return int(value)
        if kind is FieldKind.DATE:
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise FilterError(
            f"Filter on '{field.name}' expects {kind.value}, received {value!r}: {exc}"
        ) from exc

    # TEXT and ARRAY element values. Enum members are unwrapped so a caller may pass
    # ``KnowledgeSourceType.POLICY`` where the column holds its wire value.
    if isinstance(value, _WireEnum):
        return value.value
    return str(value)


# ---------------------------------------------------------------------------
# translation 1: SQLAlchemy
# ---------------------------------------------------------------------------


def compile_to_sql(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    model: type = KnowledgeChunk,
) -> list[ColumnElement[bool]]:
    """Translate filters into predicates to AND into a ``WHERE`` clause.

    Returned as a list rather than a single clause so the caller can add them alongside its own
    conditions in one ``WHERE`` — which is what keeps filtering *before* scoring instead of after.
    """
    return [_sql_clause(f, model) for f in _as_normalized(filters)]


def _sql_clause(spec: NormalizedFilter, model: type) -> ColumnElement[bool]:
    column = _sql_target(spec, model)
    op = spec.op
    value = spec.value

    if op is FilterOp.EQ:
        return column == value
    if op is FilterOp.NE:
        # See the module docstring: absent values count as "not equal".
        return or_(column.is_(None), column != value)
    if op is FilterOp.IN:
        return column.in_(list(value))
    if op is FilterOp.NIN:
        return or_(column.is_(None), column.notin_(list(value)))
    if op is FilterOp.GT:
        return column > value
    if op is FilterOp.GTE:
        return column >= value
    if op is FilterOp.LT:
        return column < value
    if op is FilterOp.LTE:
        return column <= value
    if op is FilterOp.EXISTS:
        return column.isnot(None) if spec.value else column.is_(None)
    if op is FilterOp.CONTAINS:
        return _sql_contains(spec, model)
    raise FilterError(f"No SQL translation for operator '{op.value}'.")  # pragma: no cover


def _sql_target(spec: NormalizedFilter, model: type):
    """The column (or JSONB path expression) a clause compares against."""
    if spec.is_json:
        target = _column(model, "chunk_metadata", spec.field.name)
        for key in spec.json_path:
            target = target[key]
        # ``astext`` rather than the JSON value: comparing a JSONB scalar to a Python string
        # requires the string be JSON-encoded first, which is a detail no caller should carry.
        return target.astext
    return _column(model, spec.field.column, spec.field.name)


def _column(model: type, attribute: str, field_name: str):
    """Look the column up by name, failing loudly if the model does not have it.

    ``FIELDS`` maps DSL names to column names as strings, so a rename on the model would otherwise
    surface as a filter that silently stops being applied.
    """
    column = getattr(model, attribute, None)
    if column is None:
        raise FilterError(  # pragma: no cover - guards a FIELDS/model mismatch
            f"'{field_name}' maps to column '{attribute}', which {model.__name__} does not have."
        )
    return column


def _sql_contains(spec: NormalizedFilter, model: type) -> ColumnElement[bool]:
    """``contains`` in SQL: JSONB array membership, or a case-insensitive substring.

    Which one is decided by the *field's declared kind*, never by the runtime type of the value, so
    the same filter cannot mean two different things on two different rows.
    """
    column = _column(model, spec.field.column, spec.field.name)
    if spec.field.kind is FieldKind.ARRAY:
        # JSONB containment: ``tags @> '["urgent"]'``. Uses the GIN index on ``tags``.
        return column.contains([spec.value])
    # Escaped, so a value containing ``%`` or ``_`` is matched literally rather than acting as a
    # wildcard. Not cast to varchar: every field accepting ``contains`` is a text column, and
    # a cast would stop ``content`` from using its trigram index.
    escaped = str(spec.value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def sql_where(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    model: type = KnowledgeChunk,
) -> Optional[ColumnElement[bool]]:
    """The filters as one ANDed clause, or ``None`` when there are none."""
    clauses = compile_to_sql(filters, model=model)
    if not clauses:
        return None
    return and_(*clauses)


# ---------------------------------------------------------------------------
# translation 2: Python
# ---------------------------------------------------------------------------


def matches(chunk: Chunk, filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter]) -> bool:
    """Whether ``chunk`` satisfies every filter.

    The memory store's translation, and the reference the SQL one is checked against: the contract
    suite runs identical filters through both and requires the same chunks back.
    """
    return all(_python_clause(f, chunk) for f in _as_normalized(filters))


def _python_clause(spec: NormalizedFilter, chunk: Chunk) -> bool:
    actual = _python_value(spec, chunk)
    op = spec.op
    expected = spec.value

    if op is FilterOp.EXISTS:
        return (actual is not None) if expected else (actual is None)
    if op is FilterOp.NE:
        return actual is None or actual != expected
    if op is FilterOp.NIN:
        return actual is None or actual not in expected
    if actual is None:
        # Every remaining operator requires a value to compare, matching SQL's NULL semantics.
        return False
    if op is FilterOp.EQ:
        return actual == expected
    if op is FilterOp.IN:
        return actual in expected
    if op is FilterOp.CONTAINS:
        if spec.field.kind is FieldKind.ARRAY:
            return expected in (actual or ())
        return str(expected).casefold() in str(actual).casefold()
    try:
        if op is FilterOp.GT:
            return actual > expected
        if op is FilterOp.GTE:
            return actual >= expected
        if op is FilterOp.LT:
            return actual < expected
        if op is FilterOp.LTE:
            return actual <= expected
    except TypeError:  # pragma: no cover - coercion should have prevented this
        return False
    raise FilterError(f"No Python translation for operator '{op.value}'.")  # pragma: no cover


def _python_value(spec: NormalizedFilter, chunk: Chunk) -> Any:
    """Read the field off a :class:`Chunk`, mirroring what the SQL side reads off the row."""
    if spec.is_json:
        current: Any = chunk.metadata.extra or {}
        for key in spec.json_path:
            if not isinstance(current, Mapping) or key not in current:
                return None
            current = current[key]
        if current is None or isinstance(current, (list, tuple, dict)):
            # ``astext`` on the SQL side yields NULL for a JSON null, and a serialized blob for a
            # container. Neither is comparable as a scalar, so both read as absent here too.
            return None
        # Match ``astext``: JSON booleans render lowercase, numbers as their JSON text.
        return "true" if current is True else "false" if current is False else str(current)

    current = chunk
    for part in spec.field.path.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    if isinstance(current, _WireEnum):
        return current.value
    if spec.field.kind is FieldKind.ARRAY:
        return tuple(current)
    if spec.field.kind is FieldKind.TEXT and not isinstance(current, str):
        return str(current)
    return current


def _as_normalized(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
) -> tuple[NormalizedFilter, ...]:
    """Accept either raw or already-validated filters, normalizing at most once."""
    out: list[NormalizedFilter] = []
    for item in filters or ():
        out.append(item if isinstance(item, NormalizedFilter) else normalize_filter(item))
    return tuple(out)


def describe_filters(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
) -> list[str]:
    """The applied filters, for telemetry and the retrieval explanation.

    Every retrieval response reports which filters ran. A result set that looks too small is
    otherwise indistinguishable from a corpus that lacks the answer.
    """
    return [f.describe() for f in _as_normalized(filters)]


__all__ = [
    "EXTRA_PREFIX",
    "FIELDS",
    "FILTERABLE_FIELDS",
    "FieldKind",
    "FilterError",
    "FilterField",
    "FilterOp",
    "NormalizedFilter",
    "compile_to_sql",
    "describe_filters",
    "matches",
    "normalize_filter",
    "normalize_filters",
    "sql_where",
]
