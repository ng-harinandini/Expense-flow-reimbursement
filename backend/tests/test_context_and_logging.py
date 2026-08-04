"""Request context, structured logging, and the domain-error → HTTP mapping (pure unit tests)."""

from __future__ import annotations

import json
import logging

import pytest

from app.core import context as ctx
from app.core.errors import status_for
from app.core.logging import JsonFormatter
from app.domain.actor import Actor
from app.domain.claim_state_machine import SYSTEM_ROLE
from app.domain.errors import (
    ConcurrentUpdateError,
    ConflictError,
    DomainError,
    DuplicateClaimError,
    ForbiddenError,
    ImmutableEntityError,
    InvalidStateTransitionError,
    NotFoundError,
    ValidationError,
)


# --- context -----------------------------------------------------------------


def test_build_context_generates_missing_ids():
    context = ctx.build_context()
    assert context.request_id
    # With no inbound correlation id, the request id is reused so the field is never empty.
    assert context.correlation_id == context.request_id


def test_build_context_keeps_a_supplied_correlation_id():
    context = ctx.build_context(request_id="req-1", correlation_id="corr-9")
    assert context.request_id == "req-1"
    assert context.correlation_id == "corr-9"


def test_blank_incoming_ids_are_replaced():
    context = ctx.build_context(request_id="   ", correlation_id="")
    assert context.request_id.strip()
    assert context.correlation_id == context.request_id


def test_context_is_none_outside_a_request():
    assert ctx.get_context() is None
    assert ctx.current_request_id() is None
    assert ctx.current_correlation_id() is None


def test_set_and_reset_context():
    token = ctx.set_context(ctx.build_context(request_id="r-1", client_ip="10.0.0.1"))
    try:
        assert ctx.current_request_id() == "r-1"
        assert ctx.current_client_ip() == "10.0.0.1"
    finally:
        ctx.reset_context(token)
    assert ctx.get_context() is None


def test_log_fields_include_the_correlation_pair():
    fields = ctx.build_context(
        request_id="r", correlation_id="c", method="GET", path="/api/claims"
    ).as_log_fields()
    assert fields == {
        "requestId": "r", "correlationId": "c", "method": "GET", "path": "/api/claims"
    }


# --- structured logging ------------------------------------------------------


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="claim.submitted", args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_one_json_object_per_line():
    payload = json.loads(JsonFormatter().format(_record(claimId="c-1")))
    assert payload["message"] == "claim.submitted"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["claimId"] == "c-1"
    assert "timestamp" in payload


def test_formatter_injects_the_active_correlation_ids():
    token = ctx.set_context(ctx.build_context(request_id="r-7", correlation_id="c-7"))
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        ctx.reset_context(token)
    assert payload["requestId"] == "r-7"
    assert payload["correlationId"] == "c-7"


@pytest.mark.parametrize(
    "key", ["password", "idToken", "refresh_token", "clientSecret", "databaseUrl", "apiKey"]
)
def test_formatter_redacts_sensitive_keys(key):
    """Defence in depth: a careless ``extra`` must not leak a secret into the logs."""
    payload = json.loads(JsonFormatter().format(_record(**{key: "super-secret"})))
    assert payload[key] == "***redacted***"
    assert "super-secret" not in json.dumps(payload)


def test_formatter_serializes_exception_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


# --- error mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    "error, expected",
    [
        (NotFoundError("Claim", "x"), 404),
        (ForbiddenError("nope"), 403),
        (ConflictError("clash"), 409),
        (InvalidStateTransitionError("Claim", "Draft", "Approved"), 409),
        (DuplicateClaimError("dupe"), 409),
        (ImmutableEntityError("frozen"), 409),
        (ConcurrentUpdateError("Claim", "x"), 409),
        (ValidationError("bad"), 422),
        (DomainError("generic"), 400),
    ],
)
def test_domain_errors_map_to_status_codes(error, expected):
    assert status_for(error) == expected


def test_error_payload_carries_a_stable_code_and_context():
    payload = DuplicateClaimError("dupe", details={"existingClaimNumber": "EXP-1"}).to_payload()
    assert payload["code"] == "duplicate_claim"
    assert payload["detail"] == "dupe"
    assert payload["context"]["existingClaimNumber"] == "EXP-1"


def test_error_payload_omits_empty_context():
    assert "context" not in ValidationError("bad").to_payload()


def test_not_found_message_does_not_leak_the_entity_id_into_the_detail():
    """The id is structured context, so log scrapers can filter it out of user-facing text."""
    error = NotFoundError("Claim", "EXP-SECRET")
    assert error.message == "Claim not found."
    assert error.details["id"] == "EXP-SECRET"


# --- actor -------------------------------------------------------------------


def test_actor_from_current_user():
    class FakeUser:
        role = "manager"
        sub = "sub-1"
        email = "m@corp.com"
        employee_id = "emp-100"

    actor = Actor.from_current_user(FakeUser())
    assert (actor.role, actor.sub, actor.email, actor.employee_code) == (
        "manager", "sub-1", "m@corp.com", "emp-100"
    )
    assert actor.name == "m@corp.com"
    assert actor.is_system is False


def test_actor_name_prefers_a_display_name():
    actor = Actor(role="employee", email="e@corp.com").with_name("Sarah Jenkins")
    assert actor.name == "Sarah Jenkins"
    assert actor.email == "e@corp.com"  # other fields preserved


def test_system_actor_is_marked_and_never_token_derived():
    system = Actor.system()
    assert system.is_system is True
    assert system.role == SYSTEM_ROLE
    assert system.sub is None
