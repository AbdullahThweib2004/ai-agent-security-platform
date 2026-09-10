"""Alembic environment.

Two things this deliberately does not do:

* It does not carry its own connection string. ``sqlalchemy.url`` is absent from
  alembic.ini and the URL is read from ``app.config`` instead, so migrations and
  the application can never disagree about which database they mean.
* It does not import models one by one. ``app.models`` registers every model on
  ``Base.metadata``, so autogenerate sees a new table because the package sees
  it — the same single-registration point that keeps ``create_all`` honest.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import get_settings

# Importing the package registers every model on Base.metadata.
import app.models  # noqa: F401  isort:skip
from app.models.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which silently switches off
    # every logger configured before this point. When migrations run in-process
    # — as the test suite does when it stamps — that kills the application's own
    # logging for the rest of the session, and the only symptom is that nothing
    # is logged any more.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().postgres_dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without connecting — used for review and for `alembic upgrade --sql`."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection.

    An existing connection may be supplied through ``config.attributes`` — the
    test suite and `stamp_head` do this so they can act inside a transaction
    they already hold rather than opening a second one.
    """
    supplied = config.attributes.get("connection")
    if supplied is not None:
        context.configure(
            connection=supplied,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Both on, because the drift check is only as good as what it
            # compares: without these, a changed column type or a changed server
            # default would pass silently.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
