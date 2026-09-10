#!/usr/bin/env python3
"""Fail if the migrations no longer build what the models describe.

This is the check whose absence let six schema changes accumulate. The test
suite builds its schema with ``create_all``, so it passes whether or not a
migration exists — which is exactly what kept the debt invisible. This compares
the two paths directly and fails loudly when they disagree.

Three comparisons, because each catches something the others miss:

1. **Catalog** — every column, type, nullability, default, constraint and index,
   from a database built by migrations against one built by ``create_all``.
   Catches a missing index or a dropped constraint that a table-level diff
   would not.
2. **Autogenerate** — Alembic's own opinion of the migrated database. Catches
   anything the catalog query does not think to look at.
3. **Size guard** — the catalog must be substantial. Comparing two empty
   results succeeds and proves nothing; that false pass happened while this
   check was being written.

    python scripts/check_schema_drift.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402

MIGRATED_DB = "drift_check_migrated"
MODELS_DB = "drift_check_models"

# A real schema has far more than this. Anything smaller means the query failed
# or the database is empty, and comparing two empty results is a false pass.
MIN_CATALOG_ENTRIES = 100

CATALOG_SQL = text(
    """
    SELECT 'COLUMN  ' || table_name || '.' || column_name || ' :: ' || data_type
           || ' null=' || is_nullable
           || ' default=' || coalesce(column_default, '-')
           || ' len=' || coalesce(character_maximum_length::text, '-')
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name <> 'alembic_version'
    UNION ALL
    SELECT 'CONSTR  ' || rel.relname || ' ' || con.contype::text || ' '
           || con.conname || ' ' || pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = rel.relnamespace
    WHERE n.nspname = 'public' AND rel.relname <> 'alembic_version'
    UNION ALL
    SELECT 'INDEX   ' || tablename || ' ' || indexname || ' ' || indexdef
    FROM pg_indexes
    WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    ORDER BY 1
    """
)


def _admin_url():
    return make_url(get_settings().postgres_dsn).set(database="postgres")


def _db_url(name: str) -> str:
    return (
        make_url(get_settings().postgres_dsn)
        .set(database=name)
        .render_as_string(hide_password=False)
    )


def recreate_database(name: str) -> None:
    engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


def drop_database(name: str) -> None:
    engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    engine.dispose()


def catalog_of(url: str) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return sorted(row[0] for row in connection.execute(CATALOG_SQL))
    finally:
        engine.dispose()


def build_by_migration(url: str) -> None:
    from alembic import command
    from app.db.schema import alembic_config

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            config = alembic_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def build_by_models(url: str) -> None:
    import app.models  # noqa: F401  (registers every model)
    from app.models.base import Base

    engine = create_engine(url)
    try:
        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()


def autogenerate_diff(url: str) -> list:
    """What Alembic still wants to change about an already-migrated database."""
    import app.models  # noqa: F401
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.models.base import Base

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            return compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()


def main() -> int:
    print("checking that migrations still build what the models describe")
    recreate_database(MIGRATED_DB)
    recreate_database(MODELS_DB)
    try:
        build_by_migration(_db_url(MIGRATED_DB))
        build_by_models(_db_url(MODELS_DB))

        migrated = catalog_of(_db_url(MIGRATED_DB))
        from_models = catalog_of(_db_url(MODELS_DB))

        print(
            f"  catalog entries: migrations={len(migrated)} models={len(from_models)}"
        )
        if len(migrated) < MIN_CATALOG_ENTRIES:
            print(
                f"\nFAIL: only {len(migrated)} catalog entries, expected at least "
                f"{MIN_CATALOG_ENTRIES}. The comparison did not run properly — "
                "treat this as a broken check, not as agreement."
            )
            return 1

        if migrated != from_models:
            only_migrated = [e for e in migrated if e not in set(from_models)]
            only_models = [e for e in from_models if e not in set(migrated)]
            print("\nFAIL: the migrations and the models describe different schemas.")
            print(
                "\nA model was almost certainly changed without generating a "
                "migration. Generate one and commit it:\n"
                "    docker compose run --rm migrate-revision "
                '"describe your change"\n'
            )
            for entry in only_models:
                print(f"  models have, migrations do not:  {entry}")
            for entry in only_migrated:
                print(f"  migrations have, models do not:  {entry}")
            return 1

        print(f"  catalogs identical across {len(migrated)} entries")

        diff = autogenerate_diff(_db_url(MIGRATED_DB))
        if diff:
            print("\nFAIL: alembic still finds changes against the migrated schema:")
            for entry in diff:
                print(f"  {entry}")
            print(
                "\nGenerate a migration for these and commit it:\n"
                '    docker compose run --rm migrate-revision "describe your change"'
            )
            return 1

        print("  alembic finds nothing further to change")
        print("\nOK: migrations and models agree")
        return 0
    finally:
        drop_database(MIGRATED_DB)
        drop_database(MODELS_DB)


if __name__ == "__main__":
    raise SystemExit(main())
