"""The startup schema check, in every state a database can be in.

This is the guard that replaced create_all-on-startup. Its whole value is
refusing to run against a database it does not recognise, so each refusal path
needs to be exercised — a check that only ever succeeds proves nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.schema import (
    SchemaNotReady,
    current_revision,
    head_revision,
    stamp_head,
    upgrade_to_head,
    verify_schema,
)
from app.models.base import Base

pytestmark = pytest.mark.integration


def _admin_engine():
    url = make_url(get_settings().postgres_dsn).set(database="postgres")
    return create_engine(url, isolation_level="AUTOCOMMIT")


@pytest.fixture
def scratch_db():
    """A throwaway database, dropped however the test ends."""
    name = "schema_state_probe"
    admin = _admin_engine()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(get_settings().postgres_dsn).set(database=name)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_head_revision_is_a_single_revision():
    revision = head_revision()
    assert revision and isinstance(revision, str)


def test_an_empty_database_is_reported_as_never_migrated(scratch_db):
    assert current_revision(scratch_db) is None
    with pytest.raises(SchemaNotReady) as excinfo:
        verify_schema(scratch_db)

    message = str(excinfo.value)
    assert "never been migrated" in message
    assert "empty" in message
    assert head_revision() in message, "say which revision is expected"


def test_a_create_all_database_is_told_to_stamp_not_upgrade(scratch_db):
    """The state every existing dev stack is in.

    `upgrade head` fails on such a database because the tables already exist, so
    telling someone to run the migration would send them into a confusing
    failure.
    """
    Base.metadata.create_all(bind=scratch_db)
    with pytest.raises(SchemaNotReady) as excinfo:
        verify_schema(scratch_db)

    message = str(excinfo.value)
    assert "no migration history" in message
    assert "stamp head" in message
    assert "Do not run the migration" in message


def test_a_migrated_database_is_accepted(scratch_db):
    upgrade_to_head(scratch_db)
    assert verify_schema(scratch_db) == head_revision()
    assert current_revision(scratch_db) == head_revision()


def test_a_stamped_database_is_accepted(scratch_db):
    Base.metadata.create_all(bind=scratch_db)
    stamp_head(scratch_db)
    assert verify_schema(scratch_db) == head_revision()


def test_an_unknown_revision_says_the_build_is_behind(scratch_db):
    """A rolled-back deploy must not be mistaken for a database needing upgrade."""
    upgrade_to_head(scratch_db)
    with scratch_db.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num='ffffffffffff'")
        )

    with pytest.raises(SchemaNotReady) as excinfo:
        verify_schema(scratch_db)

    message = str(excinfo.value)
    assert "does not contain" in message
    assert "roll the application forward" in message


def test_migrations_and_create_all_agree(scratch_db):
    """The drift guard, as a test rather than only as a CI script."""
    from sqlalchemy import inspect

    upgrade_to_head(scratch_db)
    migrated = set(inspect(scratch_db).get_table_names()) - {"alembic_version"}
    assert migrated == set(
        Base.metadata.tables
    ), "the migration builds a different set of tables than the models define"
    assert (
        len(migrated) >= 7
    ), "guard: too few tables for this comparison to mean anything"
