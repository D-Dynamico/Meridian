"""Database engine creation and table setup."""

from sqlalchemy import Engine, event
from sqlmodel import SQLModel, create_engine

# Imported for its side effect: the table classes register themselves on SQLModel.metadata,
# which create_all reads.
from app.db import models  # noqa: F401


def make_engine(database_url: str) -> Engine:
    # SQLite refuses by default to use a connection from a thread other than the one that
    # opened it. FastAPI runs plain "def" handlers and dependencies in a threadpool, so one
    # request can touch the connection from more than one thread. Each request still gets
    # its own session, so turning the check off is safe here.
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    # SQLite ignores foreign keys unless asked, per connection. Without this, a Feature
    # could point at a File that does not exist.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create any missing tables. There are no migrations: the schema is created fresh."""
    SQLModel.metadata.create_all(engine)
