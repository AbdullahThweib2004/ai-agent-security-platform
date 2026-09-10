"""PostgreSQL engine, session factory, and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.schema import verify_schema
from app.models.base import Base

settings = get_settings()

engine = create_engine(
    settings.postgres_dsn,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> str:
    """Verify the database is at the revision this build expects.

    This used to call ``create_all``. It no longer creates anything: migrations
    are the only path to schema now, and an application that quietly builds
    tables on startup is how a schema and its migration history drift apart
    without anyone noticing — which is precisely how this codebase accumulated
    six unmigrated changes.

    Returns the verified revision, or raises ``SchemaNotReady``.
    """
    return verify_schema(engine)


def create_all_for_tests() -> None:
    """Build the schema directly from the models, bypassing migrations.

    Test-only, and named so it cannot be mistaken for application code. Using
    this in the suite is what allowed schema and migrations to diverge
    unnoticed, so anything relying on it is trading that risk for speed
    deliberately rather than by accident.
    """
    import app.models  # noqa: F401  (registers every model on Base.metadata)

    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone transactional scope for scripts and background use."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
