"""Database connection layer (PostgreSQL only), with an optional SSH tunnel.

Alembic is the SOLE owner of the schema — this module never calls
``Base.metadata.create_all()`` and never mutates schema at import or startup.

The engine is created lazily so the application can boot even when no ``DATABASE_URL`` is
configured yet (e.g. before the owner supplies credentials). Attempting to open a session
without a configured database raises a clear error.

When ``SSH_TUNNEL_ENABLED`` is set, the database sits behind a bastion host: the tunnel is
opened first (its PEM key pulled from AWS Secrets Manager) and the engine connects through
its local port instead of ``DB_HOST`` directly. Both the app and Alembic (``alembic/env.py``)
go through :func:`get_database_url`, so migrations and the app always resolve the same way.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
import time
from typing import Generator, Optional
from urllib.parse import quote_plus

import boto3
import paramiko
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# sshtunnel 0.4.0 (latest on PyPI) unconditionally references paramiko.DSSKey when building its
# key-type table, but paramiko>=3.0 dropped DSA key support (insecure, deprecated) and removed the
# attribute entirely — so importing sshtunnel crashes before we even get to use our own RSA/Ed25519
# key. We don't use DSA keys, so alias it to something that exists rather than downgrade paramiko.
if not hasattr(paramiko, "DSSKey"):
    paramiko.DSSKey = paramiko.RSAKey

from sshtunnel import BaseSSHTunnelForwarderError, SSHTunnelForwarder  # noqa: E402

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models. Alembic reads ``Base.metadata``."""


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None

# ── SSH tunnel (lazy singleton) ────────────────────────────────────────────────

_tunnel: Optional[SSHTunnelForwarder] = None
_pem_file: Optional[str] = None

_MAX_TUNNEL_RETRIES = 3
_TUNNEL_RETRY_DELAY = 2  # seconds


def _fetch_pem_from_secrets_manager() -> str:
    """Retrieve the bastion's PEM private key from AWS Secrets Manager."""
    secret_name = settings.SSH_SECRET_NAME
    if not secret_name:
        raise RuntimeError("SSH_SECRET_NAME is not set in environment.")
    region = settings.SSH_SECRET_REGION or settings.AWS_REGION

    client = boto3.client("secretsmanager", region_name=region)
    try:
        response = client.get_secret_value(SecretId=secret_name)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to retrieve secret '{secret_name}': {exc}") from exc

    raw = response.get("SecretString") or response.get("SecretBinary", b"").decode()
    try:
        # strict=False: this secret's PEM value contains literal CR/LF bytes inside the JSON
        # string rather than escaped \r\n, which strict-mode json.loads rejects as invalid.
        payload = json.loads(raw, strict=False)
        pem = payload.get("ipd_rds_ca_cert") or payload.get("pem") or ""
    except (json.JSONDecodeError, AttributeError):
        pem = raw  # secret is the raw PEM text

    if not pem.strip():
        raise RuntimeError(
            f"Secret '{secret_name}' exists but contains no PEM key "
            "(expected JSON field 'ipd_rds_ca_cert')."
        )
    # Normalize to LF: paramiko's key parsers expect Unix line endings.
    return pem.replace("\r\n", "\n").replace("\r", "\n")


def _write_pem_to_tempfile(pem: str) -> str:
    """Write PEM text to a tempfile with 0600 permissions; return the path."""
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        os.chmod(path, 0o600)
    except Exception:
        os.unlink(path)
        raise
    return path


def _ensure_tunnel() -> int:
    """Start the SSH tunnel if not already running; return the local port to connect through."""
    global _tunnel, _pem_file

    if _tunnel is not None and _tunnel.is_active:
        return _tunnel.local_bind_port

    ssh_host = settings.SSH_HOST
    if not ssh_host:
        raise RuntimeError("SSH_TUNNEL_ENABLED is set but SSH_HOST is not configured.")
    if not (settings.DB_HOST and settings.DB_PORT):
        raise RuntimeError("SSH_TUNNEL_ENABLED is set but DB_HOST/DB_PORT are not configured.")

    pem = _fetch_pem_from_secrets_manager()
    _pem_file = _write_pem_to_tempfile(pem)

    for attempt in range(1, _MAX_TUNNEL_RETRIES + 1):
        try:
            logger.info(
                "Opening SSH tunnel to %s:%s → %s:%s (attempt %d/%d)",
                ssh_host, settings.SSH_PORT, settings.DB_HOST, settings.DB_PORT,
                attempt, _MAX_TUNNEL_RETRIES,
            )
            tunnel = SSHTunnelForwarder(
                (ssh_host, settings.SSH_PORT),
                ssh_username=settings.SSH_USER,
                ssh_pkey=_pem_file,
                remote_bind_address=(settings.DB_HOST, settings.DB_PORT),
            )
            tunnel.start()
            _tunnel = tunnel
            logger.info("SSH tunnel established on 127.0.0.1:%s", tunnel.local_bind_port)
            return tunnel.local_bind_port
        except BaseSSHTunnelForwarderError as exc:
            logger.warning("Tunnel attempt %d failed: %s", attempt, exc)
            if attempt < _MAX_TUNNEL_RETRIES:
                time.sleep(_TUNNEL_RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"SSH tunnel could not be established after {_MAX_TUNNEL_RETRIES} attempts."
                ) from exc

    raise RuntimeError("Unreachable")  # pragma: no cover


def _cleanup_tunnel() -> None:
    """Stop the tunnel and delete the temp PEM file — registered with atexit."""
    global _tunnel, _pem_file
    if _tunnel is not None:
        try:
            _tunnel.stop()
            logger.info("SSH tunnel closed.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error stopping SSH tunnel: %s", exc)
        _tunnel = None

    if _pem_file and os.path.exists(_pem_file):
        try:
            os.unlink(_pem_file)
        except OSError as exc:
            logger.warning("Could not remove temp PEM file %s: %s", _pem_file, exc)
        _pem_file = None


atexit.register(_cleanup_tunnel)


def get_database_url() -> str:
    """Resolve the connection URL, opening the SSH tunnel first if enabled.

    Used by both the app (:func:`_build_engine`) and Alembic (``alembic/env.py``), so migrations
    and the running app always reach the database the same way.
    """
    if settings.SSH_TUNNEL_ENABLED:
        local_port = _ensure_tunnel()
        if not (settings.DB_USER and settings.DB_NAME):
            raise RuntimeError(
                "SSH_TUNNEL_ENABLED is set but DB_USER/DB_NAME are not configured."
            )
        user = quote_plus(settings.DB_USER)
        password = f":{quote_plus(settings.DB_PASSWORD)}" if settings.DB_PASSWORD else ""
        return f"postgresql+psycopg://{user}{password}@127.0.0.1:{local_port}/{settings.DB_NAME}"

    url = settings.database_url
    if not url:
        raise RuntimeError(
            "No database configured. Set DATABASE_URL (or DB_USER/DB_HOST/DB_NAME "
            "and DB_PASSWORD), or SSH_TUNNEL_ENABLED with SSH_HOST, before using the database."
        )
    return url


def get_connect_args() -> dict:
    """``connect_args`` that pin ``search_path``, shared by the app engine and Alembic.

    Alembic builds its own engine (see ``alembic/env.py``) rather than calling
    :func:`_build_engine`, so this is exposed separately to avoid the two connecting to
    different schemas.
    """
    if settings.DB_SCHEMA:
        return {"options": f"-c search_path={settings.DB_SCHEMA}"}
    return {}


def _build_engine() -> Engine:
    url = get_database_url()
    # Never log the URL — it may contain the password.
    logger.info("Initializing PostgreSQL engine (pool_pre_ping=on, recycle=%ss).",
                settings.DB_POOL_RECYCLE_SECONDS)
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        connect_args=get_connect_args(),
        future=True,
    )

    if settings.DB_SCHEMA:
        # Belt-and-suspenders: also set search_path via event listener so that connections
        # recycled from the pool always land in the correct schema.
        @event.listens_for(engine, "connect")
        def _set_search_path(dbapi_conn, _connection_record):
            with dbapi_conn.cursor() as cursor:
                cursor.execute(f"SET search_path TO {settings.DB_SCHEMA}")

    return engine


def get_engine() -> Engine:
    """Return the process-wide engine, building it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields the request-scoped session.

    Rolls back on an unhandled exception so a failed request can never leave a half-applied
    transaction behind, and always closes. It deliberately does **not** commit: committing is the
    route's explicit act (see :class:`app.core.unit_of_work.UnitOfWork`), so a commit failure is
    reported inside the response rather than after it has been sent.

    Every repository and service in one request receives this same session, which is what makes a
    business change and its audit record atomic.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Backwards/ergonomic aliases some call sites may expect.
def SessionLocal() -> Session:  # noqa: N802 (kept capitalized to mirror common convention)
    """Create a new Session. Prefer ``get_db`` as a FastAPI dependency."""
    return get_session_factory()()
