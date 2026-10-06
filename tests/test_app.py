"""Application startup, health and data model basics."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

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
    assert Settings().max_extracted_bytes == 500 * 1024 * 1024


def test_settings_read_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MERIDIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MERIDIAN_MAX_UPLOAD_MB", "5")
    settings = load_settings()
    assert settings.data_dir == tmp_path
    assert settings.max_upload_bytes == 5 * 1024 * 1024


# Wiring (docs/DECISIONS.md D28) and startup recovery (D17)


def test_upload_is_processed_by_the_real_processor(e2e_client):
    body = e2e_client.post(
        "/api/files/",
        files={"file": ("survey.kml", b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>')},
    ).json()
    # TestClient ran the background task before returning, so the file is finished.
    listed = e2e_client.get("/api/files/").json()["results"]
    assert [(item["id"], item["status"], item["feature_count"]) for item in listed] == [
        (body["id"], "COMPLETED", 0)
    ]


def test_startup_fails_files_left_unfinished_by_a_previous_process(app, settings):
    with TestClient(app):  # first start creates the tables
        pass
    with Session(app.state.engine) as session:
        session.add(File(filename="stuck.kml", format=FileFormat.KML, status=FileStatus.PROCESSING))
        session.commit()

    with TestClient(app) as restarted:
        item = restarted.get("/api/files/").json()["results"][0]
    assert item["status"] == "FAILED"
