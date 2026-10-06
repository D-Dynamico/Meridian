"""Application startup, health and data model basics."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, load_settings
from app.db.models import Feature, File, FileFormat, FileStatus


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_docs_load(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_startup_creates_data_folders_and_database(client, settings):
    assert settings.uploads_dir.is_dir()
    assert (settings.data_dir / "meridian.db").is_file()


def test_new_file_defaults(session):
    file = File(filename="survey.kml", format=FileFormat.KML)
    session.add(file)
    session.commit()
    session.refresh(file)

    assert isinstance(file.id, uuid.UUID)
    assert file.status == FileStatus.PENDING
    assert file.feature_count is None
    assert file.crs_assumed is False
    assert file.created_at is not None


def test_feature_must_belong_to_an_existing_file(session):
    # Fails only if the SQLite foreign key pragma is on (app/db/session.py).
    session.add(Feature(file_id=uuid.uuid4(), feature_index=0, layer="Parcels"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_feature_index_is_unique_within_a_file(session):
    file = File(filename="survey.kml", format=FileFormat.KML)
    session.add(file)
    session.commit()
    session.add(Feature(file_id=file.id, feature_index=0, layer="Parcels"))
    session.add(Feature(file_id=file.id, feature_index=0, layer="Roads"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_feature_properties_round_trip_as_json(session):
    file = File(filename="survey.kml", format=FileFormat.KML)
    session.add(file)
    session.commit()
    feature = Feature(
        file_id=file.id, feature_index=0, layer="Parcels", properties={"name": "Plot 12"}
    )
    session.add(feature)
    session.commit()
    session.expire_all()
    assert session.get(Feature, feature.id).properties == {"name": "Plot 12"}


def test_settings_defaults_need_no_environment(monkeypatch):
    monkeypatch.delenv("MERIDIAN_DATA_DIR", raising=False)
    monkeypatch.delenv("MERIDIAN_MAX_UPLOAD_MB", raising=False)
    assert load_settings() == Settings()
    assert Settings().max_upload_mb == 50


def test_settings_read_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MERIDIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MERIDIAN_MAX_UPLOAD_MB", "5")
    settings = load_settings()
    assert settings.data_dir == tmp_path
    assert settings.max_upload_bytes == 5 * 1024 * 1024
