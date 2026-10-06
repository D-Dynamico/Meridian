"""GET /api/files/: newest first, paginated (docs/API.md §8.6)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import File, FileFormat

BASE_TIME = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def three_files(session):
    """Inserted oldest first, so newest-first ordering has to come from the query."""
    files = [
        File(filename=f"file{i}.kml", format=FileFormat.KML,
             created_at=BASE_TIME + timedelta(minutes=i))
        for i in range(3)
    ]
    session.add_all(files)
    session.commit()
    return files


def names(response):
    return [item["filename"] for item in response.json()["results"]]


def test_empty_list(client):
    response = client.get("/api/files/")
    assert response.status_code == 200
    assert response.json() == {"count": 0, "limit": 100, "offset": 0, "results": []}


def test_newest_first(client, three_files):
    assert names(client.get("/api/files/")) == ["file2.kml", "file1.kml", "file0.kml"]


def test_pagination_returns_the_slice_and_the_total(client, three_files):
    body = client.get("/api/files/", params={"limit": 1, "offset": 1}).json()
    assert body["count"] == 3
    assert (body["limit"], body["offset"]) == (1, 1)
    assert [item["filename"] for item in body["results"]] == ["file1.kml"]


def test_item_fields_and_utc_timestamps(client, three_files):
    item = client.get("/api/files/").json()["results"][0]
    assert item["format"] == "KML"
    assert item["status"] == "PENDING"
    assert item["feature_count"] is None
    assert item["completed_at"] is None
    # SQLite stores no timezone. SQLModel's UTCDateTime column type puts UTC back on
    # read; this pins that, so an upgrade that changes it cannot drop the "Z" silently.
    assert item["created_at"] == "2026-10-07T10:02:00Z"


def test_uploaded_file_appears_in_the_list(client):
    client.post("/api/files/", files={"file": ("survey.kml", b"<kml/>")})
    assert names(client.get("/api/files/")) == ["survey.kml"]


def test_slashless_alias(client, three_files):
    response = client.get("/api/files", follow_redirects=False)
    assert response.status_code == 200
    assert response.json()["count"] == 3


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
def test_out_of_range_paging_is_rejected(client, params):
    assert client.get("/api/files/", params=params).status_code == 422
