"""FastAPI application factory and composition root.

Run with: uvicorn app.main:app

This is the one place that wires the routes to the processor (docs/DECISIONS.md D28), so
it is also the one place outside app/services that pulls in the geospatial libraries.
"""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial

import anyio
from fastapi import FastAPI

from app.api import files
from app.api.upload_limit import UploadSizeLimitMiddleware
from app.core.config import Settings, load_settings
from app.db.session import init_db, make_engine
from app.services.processor import process_file, recover_interrupted


def in_processing_threads(
    process: Callable[[uuid.UUID], None], threads: int
) -> Callable[[uuid.UUID], Awaitable[None]]:
    """Run process in a thread pool of its own, at most `threads` files at a time.

    Starlette would otherwise run a plain-function background task in the same 40-thread
    pool that serves every plain-def route. A few slow files could then take every
    thread, and the API, /health included, would stop answering until they finished.
    With a separate limit, slow files only delay other files (docs/DECISIONS.md D32).
    """
    limiter: anyio.CapacityLimiter | None = None

    async def run(file_id: uuid.UUID) -> None:
        nonlocal limiter
        # Created on first use, inside the running event loop, which anyio requires.
        if limiter is None:
            limiter = anyio.CapacityLimiter(threads)
        await anyio.to_thread.run_sync(process, file_id, limiter=limiter)

    return run


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own Settings; the server uses the environment."""
    settings = settings or load_settings()
    # create_engine does not open the database, so building the app has no side effects.
    # Folders and tables are created at startup, in lifespan.
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        init_db(engine)
        recover_interrupted(engine, settings)
        yield
        engine.dispose()

    app = FastAPI(
        title="Meridian",
        summary="Area and length measurements for Shapefile and KML features.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.processor = in_processing_threads(
        partial(process_file, engine=engine, settings=settings), settings.processing_threads
    )
    app.add_middleware(
        UploadSizeLimitMiddleware,
        max_body_bytes=settings.max_body_bytes,
        limit_label=settings.max_upload_label,
    )
    app.include_router(files.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness check. Answers as long as the process is serving requests."""
        return {"status": "ok"}

    return app


app = create_app()
