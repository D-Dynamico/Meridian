"""GET /api/files/{id}/measurements/ (docs/API.md §8.4, docs/TEST_PLAN.md §16)."""

import datetime
import uuid

import geopandas as gpd
import pytest
from shapely.geometry import Point
from sqlalchemy import text

from tests.builders import ground_square, plots, shapefile_parts, survey_kml, without, write_zip

ITEM_KEYS = {
    "index", "layer", "geometry_type", "source_crs", "measurement_method", "measurement_crs",
    "measurement", "geodesic_value", "repaired", "note", "properties", "geometry",
}


def upload(client, name, content) -> str:
    return client.post("/api/files/", files={"file": (name, content)}).json()["id"]


def measurements(client, file_id, **params):
    return client.get(f"/api/files/{file_id}/measurements/", params=params)


@pytest.fixture
def survey(e2e_client):
    return upload(e2e_client, "survey.kml", survey_kml())


# Shape of the response


def test_every_result_carries_every_field(e2e_client, survey):
    body = measurements(e2e_client, survey).json()
    assert body["file_id"] == survey
    assert (body["count"], body["limit"], body["offset"]) == (4, 100, 0)
    for item in body["results"]:
        assert item.keys() == ITEM_KEYS


def test_polygon_line_point_and_missing_geometry(e2e_client, survey):
    polygon, line, point, nothing = measurements(e2e_client, survey).json()["results"]

    assert [r["index"] for r in (polygon, line, point, nothing)] == [0, 1, 2, 3]
    assert (polygon["layer"], line["layer"]) == ("Parcels", "Roads")

    assert polygon["measurement"].keys() == {"type", "value", "unit", "hectares"}
    assert polygon["measurement"]["type"] == "area"
    assert polygon["measurement"]["unit"] == "m2"
    assert polygon["measurement"]["value"] == pytest.approx(1_000_000, rel=0.005)
    assert polygon["measurement"]["hectares"] == pytest.approx(100, rel=0.005)
    assert polygon["measurement_method"] == "projected"
    assert polygon["measurement_crs"] == "EPSG:32643"
    assert polygon["source_crs"] == "EPSG:4326"
    assert polygon["geodesic_value"] == pytest.approx(polygon["measurement"]["value"], rel=0.0025)
    assert polygon["properties"] == {"Name": "Plot 12", "owner": "Asha"}
    assert polygon["geometry"]["type"] == "Polygon"

    assert line["measurement"].keys() == {"type", "value", "unit", "km"}
    assert line["measurement"]["km"] == pytest.approx(1, rel=0.005)

    assert point["measurement"] is None
    assert point["measurement_method"] is None
    assert point["geodesic_value"] is None
    assert point["note"] == "No measurement for point geometries"
    assert point["geometry"]["type"] == "Point"

    assert nothing["geometry_type"] is None
    assert nothing["geometry"] is None
    assert nothing["note"] == "Empty geometry"


def test_include_geometry_false_drops_only_geometry(e2e_client, survey):
    for item in measurements(e2e_client, survey, include_geometry=False).json()["results"]:
        assert item.keys() == ITEM_KEYS - {"geometry"}


def test_geometry_type_filter(e2e_client, survey):
    body = measurements(e2e_client, survey, geometry_type="Polygon").json()
    assert body["count"] == 1
    assert [r["geometry_type"] for r in body["results"]] == ["Polygon"]


def test_pagination_returns_the_slice_and_the_total(e2e_client, survey):
    body = measurements(e2e_client, survey, limit=2, offset=1).json()
    assert body["count"] == 4
    assert [r["index"] for r in body["results"]] == [1, 2]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
def test_out_of_range_paging_is_rejected(e2e_client, survey, params):
    assert measurements(e2e_client, survey, **params).status_code == 422


def test_slashless_alias(e2e_client, survey):
    response = e2e_client.get(f"/api/files/{survey}/measurements", follow_redirects=False)
    assert response.status_code == 200


# Not ready, failed, unknown


def test_unknown_id_returns_404(client):
    response = measurements(client, uuid.uuid4())
    assert response.status_code == 404


def test_pending_file_returns_409(client):
    file_id = upload(client, "survey.kml", survey_kml())  # recorder: stays PENDING
    response = measurements(client, file_id)
    assert response.status_code == 409
    assert response.json() == {"detail": "File is still PENDING"}


def test_failed_file_returns_409_with_the_reason(e2e_client, tmp_path):
    content = write_zip(tmp_path / "x.zip", without(shapefile_parts(plots()), ".dbf")).read_bytes()
    response = measurements(e2e_client, upload(e2e_client, "parcels.zip", content))
    assert response.status_code == 409
    assert response.json() == {"detail": "File processing FAILED: Shapefile 'parcels' is missing .dbf"}


# JSON-safe properties, end to end (docs/DECISIONS.md D29)


def test_null_properties_round_trip_as_json_nulls(e2e_client, tmp_path):
    """Missing values must be stored and served as JSON null, never NaN (D29).

    The response alone cannot prove that. Through a response_model, Pydantic serializes
    NaN as null, so this endpoint would return 200 with nulls even if NaN had been
    stored (verified: removing to_json_safe left the response check green). The stored
    text is therefore checked too, with SQLite's own json_valid, which rejects NaN.
    """
    frame = gpd.GeoDataFrame(
        {
            "name": ["Plot 12", None],
            "area_ha": [1.5, None],
            "surveyed": [datetime.date(2026, 1, 15), None],
            "plots": [3, 4],
        },
        geometry=[Point(75.8, 26.9), Point(75.81, 26.91)],
        crs="EPSG:4326",
    )
    content = write_zip(tmp_path / "x.zip", shapefile_parts(frame)).read_bytes()
    file_id = upload(e2e_client, "plots.zip", content)
    response = measurements(e2e_client, file_id)

    assert response.status_code == 200
    assert "NaN" not in response.text
    complete, empty = (r["properties"] for r in response.json()["results"])
    assert complete == {"name": "Plot 12", "area_ha": 1.5, "surveyed": "2026-01-15", "plots": 3}
    assert empty == {"name": None, "area_ha": None, "surveyed": None, "plots": 4}

    with e2e_client.app.state.engine.connect() as connection:
        stored = connection.execute(
            text("SELECT properties, json_valid(properties) FROM feature WHERE file_id = :id"),
            {"id": uuid.UUID(file_id).hex},
        ).all()
    assert len(stored) == 2
    assert all(valid == 1 for _, valid in stored), stored


def test_shapefile_end_to_end(e2e_client, tmp_path):
    # A Geod.fwd square near Jaipur, written in UTM 43N: measured in place, no transform.
    square = gpd.GeoDataFrame({"plot": ["A-12"]}, geometry=[ground_square(75.79, 26.91)], crs="EPSG:4326")
    content = write_zip(tmp_path / "x.zip", shapefile_parts(square.to_crs("EPSG:32643"))).read_bytes()
    file_id = upload(e2e_client, "parcels.zip", content)

    detail = e2e_client.get(f"/api/files/{file_id}/").json()
    assert (detail["status"], detail["format"], detail["crs"], detail["feature_count"]) == (
        "COMPLETED", "SHAPEFILE", "EPSG:32643", 1,
    )
    (item,) = measurements(e2e_client, file_id).json()["results"]
    assert item["layer"] == "parcels"
    assert item["source_crs"] == item["measurement_crs"] == "EPSG:32643"
    assert item["measurement"]["value"] == pytest.approx(1_000_000, rel=0.005)
    assert item["properties"] == {"plot": "A-12"}
