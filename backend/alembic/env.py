"""Alembic environment.

Reuses the application settings for the database URL and the models' ``Base.metadata`` as the
autogenerate target. Alembic is the SOLE owner of the schema.

DB SAFETY (critical): this environment does not DROP/TRUNCATE/reset or auto-``stamp`` anything.
If a migration cannot be applied because the actual schema disagrees with the migration state,
STOP and report expected-vs-actual plus the safest strategy — never mutate to "fix" it.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the backend package importable (alembic may run from the backend dir).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models  # noqa: E402,F401  (registers all models on Base.metadata)
import app.ai.models  # noqa: E402,F401  (registers the AI platform tables too)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _require_url() -> str:
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "No database configured. Set DATABASE_URL (or DB_USER/DB_HOST/DB_NAME "
            "and DB_PASSWORD) before running Alembic."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to the script output without a live DB connection."""
    context.configure(
        url=_require_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _require_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
