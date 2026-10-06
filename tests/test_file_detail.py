"""GET /api/files/{id}/ (docs/API.md §8.3)."""

import uuid

import pytest
from sqlmodel import Session, select

from app.db.models import Feature, MeasurementType
from tests.builders import plots, shapefile_parts, survey_kml, without, write_zip

BRIEF_FIELDS = {"id", "filename", "feature_count", "crs", "status"}


def upload(client, name, content):
    return client.post("/api/files/", files={"file": (name, content)}).json()["id"]


def test_completed_kml_has_details_and_a_summary(e2e_client):
    file_id = upload(e2e_client, "survey.kml", survey_kml())
    body = e2e_client.get(f"/api/files/{file_id}/").json()

    assert BRIEF_FIELDS <= body.keys()
    assert body["status"] == "COMPLETED"
    assert body["format"] == "KML"
    assert body["feature_count"] == 4
    assert body["crs"] == "EPSG:4326"
    assert body["crs_assumed"] is False
    assert body["error"] is None
    assert body["completed_at"].endswith("Z")

    summary = body["summary"]
    assert summary["by_type"] == {"LineString": 1, "Point": 1, "Polygon": 1, "No geometry": 1}
    assert summary["total_area_m2"] == pytest.approx(1_000_000, rel=0.005)
    assert summary["total_area_ha"] == pytest.approx(100, rel=0.005)
    assert summary["total_length_m"] == pytest.approx(1000, rel=0.005)
    assert summary["total_length_km"] == pytest.approx(1, rel=0.005)
    # Rounded on the way out: 2 decimals for metres, 4 for hectares and kilometres.
    assert summary["total_area_m2"] == round(summary["total_area_m2"], 2)
    assert summary["total_area_ha"] == round(summary["total_area_ha"], 4)

    # Exactly the stored values, not just near 1 km2: the 0.5 percent band above would
    # also accept a total that wrongly added the 1000 m line to the area.
    with Session(e2e_client.app.state.engine) as session:
        values = dict(
            session.exec(
                select(Feature.measurement_type, Feature.value).where(
                    Feature.file_id == uuid.UUID(file_id), Feature.value.is_not(None)
                )
            ).all()
        )
    assert summary["total_area_m2"] == round(values[MeasurementType.AREA], 2)
    assert summary["total_length_m"] == round(values[MeasurementType.LENGTH], 2)


def test_pending_file_has_no_summary(client):
    file_id = upload(client, "survey.kml", survey_kml())  # recorder processor: stays PENDING
    body = client.get(f"/api/files/{file_id}/").json()
    assert BRIEF_FIELDS <= body.keys()
    assert body["status"] == "PENDING"
    assert body["feature_count"] is None
    assert body["summary"] is None


def test_failed_file_reports_its_reason(e2e_client, tmp_path):
    content = write_zip(tmp_path / "x.zip", without(shapefile_parts(plots()), ".dbf")).read_bytes()
    body = e2e_client.get(f"/api/files/{upload(e2e_client, 'parcels.zip', content)}/").json()
    assert body["status"] == "FAILED"
    assert body["error"] == "Shapefile 'parcels' is missing .dbf"
    assert body["summary"] is None


def test_assumed_crs_is_reported(e2e_client, tmp_path):
    content = write_zip(tmp_path / "x.zip", without(shapefile_parts(plots()), ".prj")).read_bytes()
    body = e2e_client.get(f"/api/files/{upload(e2e_client, 'parcels.zip', content)}/").json()
    assert body["crs"] == "EPSG:4326"
    assert body["crs_assumed"] is True


def test_unknown_id_returns_404(client):
    for path in (f"/api/files/{uuid.uuid4()}/", f"/api/files/{uuid.uuid4()}"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "File not found"}


def test_malformed_id_is_a_validation_error(client):
    assert client.get("/api/files/not-a-uuid/").status_code == 422


def test_slashless_alias(client):
    file_id = upload(client, "survey.kml", survey_kml())
    response = client.get(f"/api/files/{file_id}", follow_redirects=False)
    assert response.status_code == 200
