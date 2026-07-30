"""AI governance (Task 11): a versioned, auditable record of which model/provider/embedding
configuration is currently policy, what it costs, and durable feature-flag overrides.

``version_registry.RegistryService`` is the one shared versioning primitive;
``model_registry``/``provider_registry``/``embedding_registry`` are kind-scoped views over it;
``cost_registry`` estimates spend from a governed entry's rate; ``feature_flags`` is the durable
layer above ``app.ai.registry.flags``'s in-process-only override store.
"""

from app.ai.governance.feature_flags import PersistedFeatureFlagStore  # noqa: F401
from app.ai.governance.version_registry import RegistryService  # noqa: F401

__all__ = ["PersistedFeatureFlagStore", "RegistryService"]
