"""
SQL Server connection manager for QualMaggie.

Provides:
  - get_db()     context manager / FastAPI dependency that yields a Session
  - get_engine() public accessor for callers that need the raw Engine

Engine and SessionLocal are created lazily on first use so that importing
this module never probes the database — safe in CI and test environments
where SQL Server may be unavailable.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config.settings import get_settings

logger = logging.getLogger(__name__)

# Module-level cache — None until first call to _get_engine()
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _get_engine() -> Engine:
    """
    Return the module-level SQLAlchemy Engine, creating it on first call.

    Thread-safe for CPython: module-level assignment is atomic under the GIL.
    pool_pre_ping=True silently replaces stale connections — important for a
    trading system with long idle gaps between scheduled jobs.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        logger.debug("Creating SQLAlchemy engine")
        _engine = create_engine(
            settings.db_connection_string,
            # Connection pool — sized for concurrent FastAPI requests + backtester
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,       # silently replace stale connections
            pool_recycle=3600,        # recycle connections after 1 hour
            echo=False,
        )

        # Enable pyodbc fast_executemany for bulk INSERT performance.
        # This sets cursor.fast_executemany = True before any executemany call,
        # giving 10-50× speedup on large batch inserts into SQL Server.
        @event.listens_for(_engine, "before_cursor_execute")
        def _enable_fast_executemany(
            conn, cursor, statement, params, context, executemany
        ):
            if executemany:
                cursor.fast_executemany = True

    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    """
    Return the module-level sessionmaker, creating it on first call.

    expire_on_commit=False prevents DetachedInstanceError when ORM objects
    are accessed after session.commit() in FastAPI background tasks or
    APScheduler jobs where the original session context is gone.
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=_get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy Session.  Commits on clean exit; rolls back on any
    exception; always closes the session.

    Usage as a context manager (scripts / services):
        with get_db() as session:
            session.add(obj)

    Usage as a FastAPI dependency:
        def my_route(session: Session = Depends(get_db)):
            ...
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine() -> Engine:
    """
    Return the SQLAlchemy Engine.

    Exposed for callers that need direct engine access — e.g. pd.read_sql(),
    Alembic env.py, or raw DDL execution outside the ORM.
    """
    return _get_engine()
