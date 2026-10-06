import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_processor
from app.core.config import Settings
from app.main import create_app

# Small enough that oversize tests need only a few kilobytes, not 50 MB.
TEST_MAX_UPLOAD_BYTES = 1000


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", max_upload_bytes=TEST_MAX_UPLOAD_BYTES)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def scheduled() -> list:
    """File ids the upload route handed to the processor, in order."""
    return []


@pytest.fixture
def client(app, scheduled):
    """A client whose processor only records the file id (docs/DECISIONS.md D28).

    Uploads stay PENDING with their file on disk, so upload and status tests see exactly
    what the route did, with no background work racing them.
    """
    app.dependency_overrides[get_processor] = lambda: scheduled.append
    # The context manager runs lifespan, which creates the folders and tables.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def e2e_client(tmp_path):
    """A client with the real processor. TestClient runs background tasks before the
    request call returns, so processing is synchronous: no polling, no race."""
    settings = Settings(data_dir=tmp_path / "e2e")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def session(app, client):
    """A database session on the same engine the app uses. Depends on client so the
    tables exist."""
    with Session(app.state.engine) as db_session:
        yield db_session
