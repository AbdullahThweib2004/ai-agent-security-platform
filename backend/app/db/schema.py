"""Schema state: what the database is at, and what this build expects.

The application no longer creates schema. Migrations are the only path, so
startup's job is to check rather than to build — and to say precisely what is
wrong when the two disagree, because "relation does not exist" three requests
later is a bad way to learn that nobody ran the migration.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic.config import Config
from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)

# backend/app/db/schema.py -> backend/
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"

UPGRADE_COMMAND = "docker compose run --rm migrate"


class SchemaNotReady(RuntimeError):
    """The database is not at the revision this build expects."""


def head_revision() -> str:
    """The revision this codebase's migrations end at."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    if len(heads) != 1:
        # Branched history. Refusing here is better than picking one and
        # silently validating against half the migrations.
        raise SchemaNotReady(
            f"migration history has {len(heads)} heads ({', '.join(sorted(heads))}); "
            "merge them with `alembic merge` before starting"
        )
    return heads[0]


def current_revision(engine: Engine) -> str | None:
    """The revision the database is at, or None if it has never been migrated."""
    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        return None
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).first()
    return row[0] if row else None


def alembic_config() -> Config:
    """Alembic config pointed at this backend's migrations."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return config


def upgrade_to_head(engine: Engine) -> str:
    """Run the migrations against a live engine, in-process.

    Used by the migration-path test run and by the drift check, both of which
    already hold an engine and should not shell out to the alembic CLI.
    """
    from alembic import command

    with engine.begin() as connection:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return head_revision()


def stamp_head(engine: Engine) -> str:
    """Record a database as already being at head, without running any DDL.

    Correct only for a database whose schema already matches the models — which
    is exactly the case for one built by ``create_all``, since the baseline
    migration was verified identical to what ``create_all`` produces. Running
    ``upgrade head`` on such a database fails on the first CREATE TABLE.
    """
    from alembic import command

    revision = head_revision()
    with engine.begin() as connection:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.stamp(config, revision)
    return revision


def _looks_already_built(engine: Engine) -> bool:
    """Whether the application's tables exist despite there being no history."""
    from app.models.base import Base

    existing = set(inspect(engine).get_table_names())
    expected = set(Base.metadata.tables)
    # Any overlap is enough: a partially-built database is still not something
    # `upgrade head` can run cleanly against.
    return bool(expected & existing)


def verify_schema(engine: Engine) -> str:
    """Refuse to start unless the database matches this build. Returns the revision.

    Three distinguishable failures, because they need three different actions:

    * never migrated and empty — someone has to run the migration
    * built by create_all — has the tables but no history, so it needs stamping,
      not upgrading
    * behind (or ahead of) head — this build and this database disagree
    * unknown revision — the database was migrated by a *different* build, which
      is what a rolled-back deploy looks like and must not be mistaken for
      "needs upgrading"

    Connection failures are deliberately not caught: a database that is down is
    a different problem from a database that is unmigrated, and reporting the
    first as the second sends people to fix the wrong thing.
    """
    expected = head_revision()
    actual = current_revision(engine)

    if actual is None:
        # Two very different situations look the same from the version table's
        # point of view, and they need opposite remedies. An empty database
        # wants the migration run. A database built by the old create_all path
        # already *has* every table, so `upgrade head` fails on the first
        # CREATE TABLE — it needs stamping instead, which is only safe because
        # the baseline was verified identical to what create_all produced.
        if _looks_already_built(engine):
            raise SchemaNotReady(
                "the database has tables but no migration history — it was built "
                "by the old create_all path. Do not run the migration; it will "
                f"fail on the first CREATE TABLE. Record it as already at "
                f"{expected} instead: `alembic stamp head`."
            )
        raise SchemaNotReady(
            "the database has never been migrated and is empty (no "
            f"alembic_version table, no tables). Expected revision {expected}. "
            f"Run: {UPGRADE_COMMAND}"
        )

    if actual == expected:
        logger.info(
            "schema verified",
            extra={"event": "schema.verified", "revision": actual},
        )
        return actual

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    known = {rev.revision for rev in script.walk_revisions()}

    if actual not in known:
        raise SchemaNotReady(
            f"the database is at revision {actual}, which this build does not "
            f"contain (expected {expected}). This usually means the database was "
            "migrated by a newer build than the one now running — roll the "
            "application forward rather than migrating down."
        )

    raise SchemaNotReady(
        f"the database is at revision {actual} but this build expects "
        f"{expected}. Run: {UPGRADE_COMMAND}"
    )
