"""Concrete vendor adapters.

Every module here is a leaf: it implements one ``app.ai.interfaces`` protocol against one external
system, and nothing in the platform imports it directly — the registry resolves it by name.

**All third-party imports in this package are guarded** (inside ``__init__`` or a function, wrapped
to
raise ``ProviderNotConfiguredError``). The platform ships adapters for services this deployment does
not use, so a missing driver must never break import of the package that merely mentions it.
"""
