"""Request-scoped dependencies.

Settings and the engine live on app.state, set by create_app. Reading them from the
request, rather than from module globals, lets each test build its own app against its
own temporary database.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from app.core.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


# Shorthand for route signatures: "session: SessionDep" instead of repeating Depends.
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
