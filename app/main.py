"""FastAPI application factory.

Run with: uvicorn app.main:app
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import files
from app.api.upload_limit import UploadSizeLimitMiddleware
from app.core.config import Settings, load_settings
from app.db.session import init_db, make_engine


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
