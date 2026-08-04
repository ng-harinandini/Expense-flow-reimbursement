"""AI governance tests (T004-M11): model/provider/embedding registry, cost estimation, and
durable feature-flag overrides.

Database-backed, exactly like ``test_ai_prompts.py``. Every ``FeatureFlags`` test constructs its
own fresh instance (rather than using the process-wide ``app.ai.registry.flags.feature_flags``
singleton) so an override set by one test can never leak into another running in the same session.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.governance.cost_registry import estimate_cost
from app.ai.governance.embedding_registry import (
    active_embedding_spec,
    list_active_embedding_specs,
    register_embedding_spec,
)
from app.ai.governance.feature_flags import PersistedFeatureFlagStore
from app.ai.governance.model_registry import active_model, list_active_models, register_model
from app.ai.governance.provider_registry import active_providers_by_kind, describe
from app.ai.governance.version_registry import RegistryService
from app.ai.models.governance import RegistryEntry
from app.ai.registry.flags import FeatureFlags
from app.ai.repositories.governance_repository import (
    FlagOverrideRepository,
    RegistryEntryRepository,
)
from app.domain.actor import Actor


def _name() -> str:
    return f"test-entry-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 1. cost_registry.py — pure Python
# ---------------------------------------------------------------------------


def test_estimate_cost_multiplies_quantity_by_rate() -> None:
    entry = RegistryEntry(
        kind="EMBEDDING", name="x", cost_per_unit_usd=Decimal("0.0000002"), cost_unit="token",
    )
    assert estimate_cost(entry, 1_000_000) == Decimal("0.2000000")


def test_estimate_cost_is_none_when_unpriced() -> None:
    entry = RegistryEntry(kind="EMBEDDING", name="x", cost_per_unit_usd=None)
    assert estimate_cost(entry, 1_000) is None


# ---------------------------------------------------------------------------
# 1b. Schema constraints — database-backed
# ---------------------------------------------------------------------------


def test_negative_cost_is_rejected(db_session: Session) -> None:
    db_session.add(
        RegistryEntry(kind="LLM", name=_name(), version=1, is_active=True, config={},
                      cost_per_unit_usd=Decimal("-1"))
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_non_positive_version_is_rejected(db_session: Session) -> None:
    db_session.add(
        RegistryEntry(kind="LLM", name=_name(), version=0, is_active=True, config={})
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# 2. RegistryEntryRepository — database-backed
# ---------------------------------------------------------------------------


def test_publish_version_creates_version_1_active(db_session: Session) -> None:
    repo = RegistryEntryRepository(db_session)
    entry = repo.publish_version(kind="EMBEDDING", name=_name(), config={"model": "bge-m3"})
    assert entry.version == 1
    assert entry.is_active is True


def test_publish_version_deactivates_the_previous_version(db_session: Session) -> None:
    """T004-M11 Done Check: every model/provider/embedding used is resolved through a versioned
    registry — publishing a new version must not leave two active rows for the same (kind, name)."""
    repo = RegistryEntryRepository(db_session)
    name = _name()
    v1 = repo.publish_version(kind="LLM", name=name, config={"a": 1})
    v2 = repo.publish_version(kind="LLM", name=name, config={"a": 2})

    assert v2.version == 2
    assert v2.is_active is True

    reloaded_v1 = next(e for e in repo.list_versions("LLM", name) if e.version == 1)
    assert reloaded_v1.id == v1.id
    assert reloaded_v1.is_active is False
    assert reloaded_v1.config == {"a": 1}  # never mutated


def test_rollback_restores_an_earlier_version_without_deleting_the_newer_one(
    db_session: Session,
) -> None:
    repo = RegistryEntryRepository(db_session)
    name = _name()
    v1 = repo.publish_version(kind="RERANK", name=name, config={"a": 1})
    repo.publish_version(kind="RERANK", name=name, config={"a": 2})

    restored = repo.rollback("RERANK", name, to_version=1)
    assert restored.id == v1.id
    assert restored.is_active is True

    reloaded_v2 = next(e for e in repo.list_versions("RERANK", name) if e.version == 2)
    assert reloaded_v2.is_active is False  # demoted, not deleted


def test_rollback_raises_for_a_version_that_does_not_exist(db_session: Session) -> None:
    repo = RegistryEntryRepository(db_session)
    name = _name()
    repo.publish_version(kind="OCR", name=name, config={})
    with pytest.raises(KnowledgeNotFoundError):
        repo.rollback("OCR", name, to_version=99)


def test_list_active_scopes_to_kind(db_session: Session) -> None:
    repo = RegistryEntryRepository(db_session)
    embedding_name, llm_name = _name(), _name()
    repo.publish_version(kind="EMBEDDING", name=embedding_name, config={})
    repo.publish_version(kind="LLM", name=llm_name, config={})

    embedding_only = repo.list_active("EMBEDDING")
    names = {e.name for e in embedding_only}
    assert embedding_name in names
    assert llm_name not in names


# ---------------------------------------------------------------------------
# 3. RegistryService — audited service facade
# ---------------------------------------------------------------------------


def test_registry_service_publish_writes_an_audit_record(
    db_session: Session, repositories: dict, audit_service, admin_actor: Actor,
) -> None:
    service = RegistryService(db_session, audit_service=audit_service)
    name = _name()
    service.publish("EMBEDDING", name, actor=admin_actor, config={"model": "bge-m3"})

    logs = repositories["audit"].list_for_entity(
        entity_type="RegistryEntry", entity_id=f"EMBEDDING/{name}"
    )
    assert any(log.action == "AI_REGISTRY_ENTRY_PUBLISHED" for log in logs)


def test_registry_service_rollback_writes_an_audit_record(
    db_session: Session, repositories: dict, audit_service, admin_actor: Actor,
) -> None:
    service = RegistryService(db_session, audit_service=audit_service)
    name = _name()
    service.publish("EMBEDDING", name, actor=admin_actor, config={"a": 1})
    service.publish("EMBEDDING", name, actor=admin_actor, config={"a": 2})
    service.rollback("EMBEDDING", name, to_version=1, actor=admin_actor)

    logs = repositories["audit"].list_for_entity(
        entity_type="RegistryEntry", entity_id=f"EMBEDDING/{name}"
    )
    assert any(log.action == "AI_REGISTRY_ENTRY_ROLLED_BACK" for log in logs)


def test_registry_service_get_active_raises_for_an_unknown_entry(
    db_session: Session, audit_service,
) -> None:
    service = RegistryService(db_session, audit_service=audit_service)
    with pytest.raises(KnowledgeNotFoundError):
        service.get_active("EMBEDDING", _name())


# ---------------------------------------------------------------------------
# 4. Kind-scoped wrappers (model_registry / embedding_registry / provider_registry)
# ---------------------------------------------------------------------------


def test_model_registry_scopes_to_llm_kind(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    service = RegistryService(db_session, audit_service=audit_service)
    name = _name()
    register_model(service, name, actor=admin_actor, config={"model": "claude"})
    entry = active_model(service, name)
    assert entry.kind == "LLM"
    assert entry.name == name
    assert any(e.name == name for e in list_active_models(service))


def test_embedding_registry_scopes_to_embedding_kind(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    service = RegistryService(db_session, audit_service=audit_service)
    name = _name()
    register_embedding_spec(
        service, name, actor=admin_actor, config={"model": "bge-m3"},
        cost_per_unit_usd=Decimal("0.0000001"), cost_unit="token",
    )
    entry = active_embedding_spec(service, name)
    assert entry.kind == "EMBEDDING"
    assert any(e.name == name for e in list_active_embedding_specs(service))


def test_provider_registry_groups_active_entries_by_kind(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    service = RegistryService(db_session, audit_service=audit_service)
    embedding_name, llm_name = _name(), _name()
    register_embedding_spec(service, embedding_name, actor=admin_actor, config={})
    register_model(service, llm_name, actor=admin_actor, config={})

    grouped = active_providers_by_kind(service)
    assert embedding_name in {e.name for e in grouped["EMBEDDING"]}
    assert llm_name in {e.name for e in grouped["LLM"]}


def test_provider_registry_describe_is_json_serializable(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    import json

    service = RegistryService(db_session, audit_service=audit_service)
    register_embedding_spec(
        service, _name(), actor=admin_actor, config={},
        cost_per_unit_usd=Decimal("0.0000001"), cost_unit="token",
    )
    json.dumps(describe(service))  # must not raise


# ---------------------------------------------------------------------------
# 5. FlagOverrideRepository — database-backed
# ---------------------------------------------------------------------------


def test_flag_override_upsert_creates_then_updates(db_session: Session) -> None:
    repo = FlagOverrideRepository(db_session)
    flag_name = f"test.flag.{uuid.uuid4().hex[:8]}"
    first = repo.upsert(flag_name, enabled=True, updated_by_sub="admin-sub")
    assert first.enabled is True

    second = repo.upsert(flag_name, enabled=False, updated_by_sub="admin-sub")
    assert second.id == first.id
    assert second.enabled is False


def test_flag_override_clear_removes_the_row(db_session: Session) -> None:
    repo = FlagOverrideRepository(db_session)
    flag_name = f"test.flag.{uuid.uuid4().hex[:8]}"
    repo.upsert(flag_name, enabled=True)
    assert repo.clear(flag_name) is True
    assert repo.get(flag_name) is None
    assert repo.clear(flag_name) is False  # already gone: no-op, not an error


# ---------------------------------------------------------------------------
# 6. PersistedFeatureFlagStore — audited, durable layer above FeatureFlags
# ---------------------------------------------------------------------------


def test_set_applies_in_process_immediately(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    flags = FeatureFlags()
    store = PersistedFeatureFlagStore(db_session, audit_service=audit_service, flags=flags)
    assert flags.is_enabled("ai.rerank") is True  # default on

    store.set("ai.rerank", False, actor=admin_actor)
    assert flags.is_enabled("ai.rerank") is False


def test_set_rejects_an_unknown_flag_without_writing_to_the_database(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    flags = FeatureFlags()
    store = PersistedFeatureFlagStore(db_session, audit_service=audit_service, flags=flags)
    repo = FlagOverrideRepository(db_session)
    bogus = f"not.a.real.flag.{uuid.uuid4().hex[:8]}"

    with pytest.raises(ValueError):
        store.set(bogus, True, actor=admin_actor)
    assert repo.get(bogus) is None  # the failed in-process validation left no orphaned row


def test_set_writes_an_audit_record(
    db_session: Session, repositories: dict, audit_service, admin_actor: Actor,
) -> None:
    flags = FeatureFlags()
    store = PersistedFeatureFlagStore(db_session, audit_service=audit_service, flags=flags)
    store.set("ai.rerank", False, actor=admin_actor)

    logs = repositories["audit"].list_for_entity(entity_type="FeatureFlag", entity_id="ai.rerank")
    assert any(log.action == "AI_FLAG_OVERRIDDEN" for log in logs)


def test_clear_reverts_to_the_configuration_default(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    flags = FeatureFlags()
    store = PersistedFeatureFlagStore(db_session, audit_service=audit_service, flags=flags)
    store.set("ai.rerank", False, actor=admin_actor)
    assert flags.is_enabled("ai.rerank") is False

    store.clear("ai.rerank", actor=admin_actor)
    assert flags.is_enabled("ai.rerank") is True  # back to the AISettings default


def test_load_overrides_rehydrates_a_fresh_flags_instance(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    """The whole point of persisting overrides: a restart (a fresh ``FeatureFlags()``) must not
    silently lose a durably-set override."""
    writer_flags = FeatureFlags()
    writer_store = PersistedFeatureFlagStore(
        db_session, audit_service=audit_service, flags=writer_flags
    )
    writer_store.set("ai.rerank", False, actor=admin_actor)

    fresh_flags = FeatureFlags()
    assert fresh_flags.is_enabled("ai.rerank") is True  # not yet loaded

    reader_store = PersistedFeatureFlagStore(
        db_session, audit_service=audit_service, flags=fresh_flags
    )
    loaded_count = reader_store.load_overrides()

    assert loaded_count >= 1
    assert fresh_flags.is_enabled("ai.rerank") is False
