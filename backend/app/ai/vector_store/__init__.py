"""Vector store abstraction (Task 5).

The only package permitted to import a vector database driver or to define a vector column type —
``tests/test_ai_architecture.py`` enforces that. Adapters are registered by name and resolved
through ``app.ai.registry``; business code never names a store.

Eight adapters behind one contract, all held to it by a single suite
(``tests/test_ai_vector_store.py``). What each one can and cannot do is declared in
:mod:`app.ai.vector_store.capabilities` — read that before choosing one.

Layout:

* ``base`` — the invariants every adapter inherits: dimension checks, embedding-version
  homogeneity, score normalization, paging limits.
* ``filters`` — the store-agnostic filter DSL and its two reference translations (SQL, Python).
* ``capabilities`` — the capability matrix.
* ``pg_types`` — the ``vector`` column type.
* ``postgres`` — what the two PostgreSQL-backed stores share. ``pgvector`` and ``postgres_native``
  differ only in where the similarity arithmetic runs.
* ``external`` — what the five service-backed adapters share: payload mapping and point ids.
* ``factory`` — registration and resolution by name.

**This module deliberately re-exports almost nothing.** ``app/ai/models/knowledge.py`` has to import
``pg_types`` to declare its vector column, which executes this package's ``__init__``. If that
``__init__`` imported ``filters`` (which imports the models to compile predicates against them), the
models module would be asked for ``KnowledgeChunk`` while still halfway through defining it.
Importing
from the specific module — ``from app.ai.vector_store.factory import resolve_vector_store`` — keeps
that cycle impossible rather than merely unlikely.
"""

from app.ai.vector_store.pg_types import Vector, as_vector_param, vector_literal

__all__ = ["Vector", "as_vector_param", "vector_literal"]
