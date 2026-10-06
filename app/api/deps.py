"""Request-scoped dependencies.

Settings and the engine live on app.state, set by create_app. Reading them from the
request, rather than from module globals, lets each test build its own app against its
own temporary database.
"""

import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from app.core.config import Settings

# Whatever processes an uploaded file, given its id. The routes only know this shape;
# main.py binds the real processor (docs/DECISIONS.md D28), and tests swap in their own
# through app.dependency_overrides. It may be async: the real one is, so it can run in
# its own thread pool (D32).
Processor = Callable[[uuid.UUID], Awaitable[None] | None]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


def get_processor(request: Request) -> Processor:
    return request.app.state.processor


# Shorthand for route signatures: "session: SessionDep" instead of repeating Depends.
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
ProcessorDep = Annotated[Processor, Depends(get_processor)]
