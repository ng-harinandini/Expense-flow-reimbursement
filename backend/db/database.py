"""Database connection layer — PostgreSQL via optional SSH tunnel.

Connects to an AWS RDS PostgreSQL instance, optionally through an EC2 bastion host SSH tunnel.
The SSH private key (PEM) is retrieved from AWS Secrets Manager at startup.

Configuration (all from environment variables):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SCHEMA
    SSH_TUNNEL_ENABLED, SSH_HOST, SSH_PORT, SSH_USER
    SSH_SECRET_NAME, SSH_SECRET_REGION
    DB_POOL_SIZE, DB_MAX_OVERFLOW, DB_POOL_RECYCLE_SECONDS

The search_path is pinned to DB_SCHEMA on every new connection so all queries
are automatically scoped to the correct schema without any per-query prefix.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
import time
from typing import Generator, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sshtunnel import BaseSSHTunnelForwarderError, SSHTunnelForwarder
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

# ── env helpers ──────────────────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default

def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


# ── SSH tunnel ───────────────────────────────────────────────────────────────

_tunnel: Optional[SSHTunnelForwarder] = None
_pem_file: Optional[str] = None   # path to temp PEM on disk

_MAX_TUNNEL_RETRIES = 3
_TUNNEL_RETRY_DELAY = 2  # seconds


def _fetch_pem_from_secrets_manager() -> str:
    """Retrieve the PEM private key from AWS Secrets Manager."""
    secret_name = _env("SSH_SECRET_NAME")
    region = _env("SSH_SECRET_REGION", "ap-south-1")

    if not secret_name:
        raise RuntimeError("SSH_SECRET_NAME is not set in environment.")

    client = boto3.client("secretsmanager", region_name=region)
    try:
        response = client.get_secret_value(SecretId=secret_name)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to retrieve secret '{secret_name}': {exc}") from exc

    raw = response.get("SecretString") or response.get("SecretBinary", b"").decode()
    try:
        payload = json.loads(raw)
        pem = payload.get("ipd_rds_ca_cert") or payload.get("pem") or ""
    except (json.JSONDecodeError, AttributeError):
        pem = raw  # secret is the raw PEM text

    if not pem.strip():
        raise RuntimeError(
            f"Secret '{secret_name}' exists but contains no PEM key "
            "(expected JSON field 'ipd_rds_ca_cert')."
        )
    return pem


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


def _start_tunnel() -> int:
    """Start the SSH tunnel; return the local port to connect through."""
    global _tunnel, _pem_file

    ssh_host = _env("SSH_HOST")
    ssh_port = _env_int("SSH_PORT", 22)
    ssh_user = _env("SSH_USER", "ec2-user")
    db_host = _env("DB_HOST")
    db_port = _env_int("DB_PORT", 5432)

    pem = _fetch_pem_from_secrets_manager()
    _pem_file = _write_pem_to_tempfile(pem)

    for attempt in range(1, _MAX_TUNNEL_RETRIES + 1):
        try:
            logger.info(
                "Opening SSH tunnel to %s:%s → %s:%s (attempt %d/%d)",
                ssh_host, ssh_port, db_host, db_port, attempt, _MAX_TUNNEL_RETRIES,
            )
            tunnel = SSHTunnelForwarder(
                (ssh_host, ssh_port),
                ssh_username=ssh_user,
                ssh_pkey=_pem_file,
                remote_bind_address=(db_host, db_port),
            )
            tunnel.start()
            _tunnel = tunnel
            local_port = tunnel.local_bind_port
            logger.info("SSH tunnel established on 127.0.0.1:%s", local_port)
            return local_port
        except BaseSSHTunnelForwarderError as exc:
            logger.warning("Tunnel attempt %d failed: %s", attempt, exc)
            if attempt < _MAX_TUNNEL_RETRIES:
                time.sleep(_TUNNEL_RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"SSH tunnel could not be established after {_MAX_TUNNEL_RETRIES} attempts."
                ) from exc

    raise RuntimeError("Unreachable")  # pragma: no cover


def _cleanup() -> None:
    """Stop tunnel and delete the temp PEM file — registered with atexit."""
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
            logger.debug("Temp PEM file removed: %s", _pem_file)
        except OSError as exc:
            logger.warning("Could not remove temp PEM file %s: %s", _pem_file, exc)
        _pem_file = None


atexit.register(_cleanup)


# ── engine factory ───────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _build_engine() -> Engine:
    db_name = _env("DB_NAME")
    db_user = _env("DB_USER")
    db_password = _env("DB_PASSWORD")
    db_schema = _env("DB_SCHEMA", "public")
    pool_size = _env_int("DB_POOL_SIZE", 5)
    max_overflow = _env_int("DB_MAX_OVERFLOW", 10)
    pool_recycle = _env_int("DB_POOL_RECYCLE_SECONDS", 1800)

    if _env_bool("SSH_TUNNEL_ENABLED", False):
        local_port = _start_tunnel()
        db_host = "127.0.0.1"
        db_port = local_port
    else:
        db_host = _env("DB_HOST")
        db_port = _env_int("DB_PORT", 5432)

    url = (
        f"postgresql+psycopg2://{db_user}:{db_password}"
        f"@{db_host}:{db_port}/{db_name}"
    )

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        connect_args={"options": f"-c search_path={db_schema}"},
        future=True,
    )

    # Belt-and-suspenders: also set search_path via event listener so that
    # connections recycled from the pool always land in the correct schema.
    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_conn, _connection_record):
        with dbapi_conn.cursor() as cursor:
            cursor.execute(f"SET search_path TO {db_schema}")

    logger.info(
        "PostgreSQL engine initialised — host=%s port=%s schema=%s tunnel=%s",
        db_host, db_port, db_schema, _env_bool("SSH_TUNNEL_ENABLED", False),
    )
    return engine


def get_engine() -> Engine:
    """Return the process-wide engine, building it on first call."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _SessionLocal


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped SQLAlchemy session.

    Rolls back automatically on unhandled exceptions; always closes on exit.
    Committing is the caller's responsibility (use UnitOfWork or explicit commit).
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── convenience alias ─────────────────────────────────────────────────────────

engine = get_engine
