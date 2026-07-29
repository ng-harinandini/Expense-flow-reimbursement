"""Vector store abstraction (Task 5).

The only package permitted to import a vector database driver or to define a vector column type —
``tests/test_ai_architecture.py`` enforces that. Adapters are registered by name and resolved
through
``app.ai.registry``; business code never names a store.
"""

from app.ai.vector_store.pg_types import Vector, as_vector_param, vector_literal

__all__ = ["Vector", "as_vector_param", "vector_literal"]
