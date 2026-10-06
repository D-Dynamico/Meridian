"""The committed reviewer samples (docs/WORKFLOWS.md §11) process as the README says.

These are the files a reviewer uploads first, so a change that breaks them, or a README
example that drifts from them, should turn a test red rather than surprise the reviewer.
"""

from pathlib import Path

import pytest

from scripts import make_samples
from tests.builders import utm_area_gap

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def process(client, name: str) -> tuple[dict, list[dict]]:
    content = (SAMPLES / name).read_bytes()
    file_id = client.post("/api/files/", files={"file": (name, content)}).json()["id"]
    detail = client.get(f"/api/files/{file_id}/").json()
    results = client.get(f"/api/files/{file_id}/measurements/").json()["results"]
    return detail, results


def test_committed_kml_matches_its_generator():
    """The zip is not compared byte for byte: the .dbf header records the day it was
    written. Its content is pinned by the end-to-end test below instead."""
    assert (SAMPLES / "survey.kml").read_bytes() == make_samples.survey_kml()


def test_survey_kml_sample(e2e_client):
    detail, results = process(e2e_client, "survey.kml")
    assert (detail["status"], detail["crs"], detail["feature_count"]) == ("COMPLETED", "EPSG:4326", 5)
    assert [(r["layer"], r["geometry_type"]) for r in results] == [
        ("Parcels", "Polygon"),
        ("Parcels", "Polygon"),
        ("Roads", "LineString"),
        ("Roads", "Point"),
        ("Reference", "GeometryCollection"),
    ]
    plot, bowtie, road, gate, collection = results

    for measured in (plot, bowtie, road):
        assert measured["measurement_method"] == "projected"
        assert measured["measurement_crs"] == "EPSG:32643"
    assert plot["geodesic_value"] == pytest.approx(1_000_000, rel=0.0001)
    assert plot["measurement"]["value"] / plot["geodesic_value"] - 1 == pytest.approx(
        utm_area_gap(75.795, 26.915, 43), abs=0.00005
    )
    # Two triangles of a quarter of a 500 m square each.
    assert (bowtie["repaired"], bowtie["note"]) == (True, "Repaired invalid geometry")
    assert bowtie["geodesic_value"] == pytest.approx(125_000, rel=0.0001)
    assert road["geodesic_value"] == pytest.approx(1000, rel=0.0001)

    assert (gate["measurement"], gate["note"]) == (None, "No measurement for point geometries")
    assert collection["measurement"] is None
    assert collection["note"] == "Unsupported geometry type: GeometryCollection"


def test_parcels_zip_sample_is_measured_in_its_own_utm_zone(e2e_client):
    detail, results = process(e2e_client, "parcels.zip")
    assert (detail["status"], detail["crs"], detail["crs_assumed"]) == ("COMPLETED", "EPSG:32643", False)
    assert [r["properties"]["survey_no"] for r in results] == ["41/1", "41/2", "42"]
    for result, hectares in zip(results, (1, 3, 5)):
        assert result["source_crs"] == result["measurement_crs"] == "EPSG:32643"
        assert result["geodesic_value"] == pytest.approx(hectares * 10_000, rel=0.0001)
        # Measured in place, so the value carries UTM's scale error at Bengaluru.
        assert result["measurement"]["value"] / result["geodesic_value"] - 1 == pytest.approx(
            utm_area_gap(77.6, 12.97, 43), abs=0.00005
        )
