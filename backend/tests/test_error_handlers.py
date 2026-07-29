"""The centralized error handlers and the request-context middleware.

These are the safety net: if they misbehave, a domain failure becomes a 500 and an unexpected
exception leaks internals. Handlers are invoked directly (they are ``async``, so each test drives
them with ``asyncio.run``) plus one end-to-end pass through the real middleware stack.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.requests import Request

from app.core import errors as error_module
from app.core.context import build_context, reset_context, set_context
from app.core.errors import register_exception_handlers
from app.core.middleware import RequestContextMiddleware, _client_ip
from app.domain.errors import DuplicateClaimError, NotFoundError, ValidationError


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _body(response) -> dict:
    return json.loads(bytes(response.body).decode())


# --- handlers ----------------------------------------------------------------


def test_domain_error_handler_maps_status_code_and_code():
    response = asyncio.run(
        error_module.domain_error_handler(
            _request(), DuplicateClaimError("dupe", details={"existingClaimNumber": "EXP-1"})
        )
    )
    assert response.status_code == 409
    payload = _body(response)
    assert payload["code"] == "duplicate_claim"
    assert payload["context"]["existingClaimNumber"] == "EXP-1"


def test_domain_error_handler_includes_the_request_id_when_available():
    token = set_context(build_context(request_id="req-77"))
    try:
        response = asyncio.run(
            error_module.domain_error_handler(_request(), NotFoundError("Claim", "x"))
        )
    finally:
        reset_context(token)
    assert _body(response)["requestId"] == "req-77"


def test_domain_error_handler_omits_the_request_id_outside_a_request():
    response = asyncio.run(
        error_module.domain_error_handler(_request(), ValidationError("bad"))
    )
    assert "requestId" not in _body(response)


def test_stale_data_handler_returns_409_concurrent_update():
    response = asyncio.run(
        error_module.stale_data_handler(_request(), StaleDataError("lost race"))
    )
    assert response.status_code == 409
    assert _body(response)["code"] == "concurrent_update"


def test_integrity_error_handler_returns_409_without_leaking_sql():
    exc = IntegrityError(
        "INSERT INTO claims ...", {}, Exception('duplicate key value violates "uq_secret"')
    )
    response = asyncio.run(error_module.integrity_error_handler(_request(), exc))

    assert response.status_code == 409
    payload = _body(response)
    assert payload["code"] == "integrity_violation"
    # The constraint name is diagnostic detail for the logs, not for the caller.
    assert "uq_secret" not in json.dumps(payload)


def test_unhandled_error_handler_returns_an_opaque_500():
    response = asyncio.run(
        error_module.unhandled_error_handler(_request(), RuntimeError("secret internals"))
    )
    assert response.status_code == 500
    payload = _body(response)
    assert payload == {"detail": "Internal server error.", "code": "internal_error"} or (
        payload["detail"] == "Internal server error."
    )
    assert "secret internals" not in json.dumps(payload)


# --- middleware --------------------------------------------------------------


@pytest.fixture
def probe_app() -> TestClient:
    """A tiny app with the real middleware and handlers, plus routes that fail on purpose."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/ok")
    def ok():
        from app.core.context import current_correlation_id, current_request_id

        return {"requestId": current_request_id(), "correlationId": current_correlation_id()}

    @app.get("/domain-error")
    def domain_error():
        raise ValidationError("amount must be positive", details={"field": "amount"})

    @app.get("/boom")
    def boom():
        raise RuntimeError("unexpected")

    return TestClient(app, raise_server_exceptions=False)


def test_middleware_binds_ids_and_echoes_them(probe_app):
    response = probe_app.get("/ok")
    body = response.json()

    assert response.status_code == 200
    assert body["requestId"] == response.headers["X-Request-Id"]
    assert body["correlationId"] == response.headers["X-Correlation-Id"]


def test_middleware_preserves_an_inbound_correlation_id(probe_app):
    response = probe_app.get("/ok", headers={"X-Correlation-Id": "trace-abc"})
    assert response.json()["correlationId"] == "trace-abc"
    assert response.headers["X-Correlation-Id"] == "trace-abc"
    # The per-hop request id is still distinct.
    assert response.json()["requestId"] != "trace-abc"


def test_middleware_preserves_an_inbound_request_id(probe_app):
    response = probe_app.get("/ok", headers={"X-Request-Id": "req-inbound"})
    assert response.json()["requestId"] == "req-inbound"


def test_domain_error_raised_in_a_route_becomes_the_mapped_status(probe_app):
    response = probe_app.get("/domain-error")
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "validation_error"
    assert payload["context"]["field"] == "amount"
    assert payload["requestId"]


def test_unexpected_route_exception_becomes_a_500(probe_app):
    response = probe_app.get("/boom")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "unexpected" not in response.text


def test_client_ip_prefers_the_leftmost_forwarded_entry():
    forwarded = Request(
        {
            "type": "http", "method": "GET", "path": "/", "client": ("10.0.0.1", 1234),
            "headers": [(b"x-forwarded-for", b"203.0.113.9, 70.41.3.18")],
        }
    )
    assert _client_ip(forwarded) == "203.0.113.9"


def test_client_ip_falls_back_to_the_socket_peer():
    direct = Request(
        {"type": "http", "method": "GET", "path": "/", "client": ("10.0.0.1", 1234),
         "headers": []}
    )
    assert _client_ip(direct) == "10.0.0.1"


def test_client_ip_is_none_when_unknown():
    unknown = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert _client_ip(unknown) is None
