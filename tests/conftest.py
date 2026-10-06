import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

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
def client(app):
    # The context manager runs lifespan, which creates the folders and tables.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session(app, client):
    """A database session on the same engine the app uses. Depends on client so the
    tables exist."""
    with Session(app.state.engine) as db_session:
        yield db_session
