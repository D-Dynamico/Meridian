"""Upload route: type and size validation (docs/TEST_PLAN.md §16, api)."""

import asyncio
import uuid

import pytest
from sqlmodel import select

from app.api.upload_limit import UploadSizeLimitMiddleware
from app.db.models import File, FileFormat, FileStatus

KML_BYTES = b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"></kml>'


def upload(client, filename, content=KML_BYTES, path="/api/files/", **kwargs):
    return client.post(path, files={"file": (filename, content)}, **kwargs)


def saved_uploads(settings):
    return sorted(p.name for p in settings.uploads_dir.iterdir())


# Accepted uploads


def test_kml_upload_returns_202_and_creates_a_pending_row(client, session, settings):
    response = upload(client, "survey.kml")

    assert response.status_code == 202
    body = response.json()
    assert body["filename"] == "survey.kml"
    assert body["status"] == "PENDING"

    record = session.get(File, uuid.UUID(body["id"]))
    assert record.format == FileFormat.KML
    assert record.status == FileStatus.PENDING
    assert record.feature_count is None


def test_upload_is_saved_byte_for_byte_under_its_id(client, settings):
    body = upload(client, "survey.kml").json()
    saved = settings.uploads_dir / f"{body['id']}.kml"
    assert saved.read_bytes() == KML_BYTES


def test_zip_upload_is_recorded_as_shapefile(client, session):
    body = upload(client, "parcels.zip", b"PK\x03\x04 not checked until Stage 2").json()
    assert session.get(File, uuid.UUID(body["id"])).format == FileFormat.SHAPEFILE


def test_extension_check_ignores_case(client):
    assert upload(client, "SURVEY.KML").status_code == 202


@pytest.mark.parametrize(
    "sent_name", ["../../evil/survey.kml", "..\\..\\survey.kml", "/etc/survey.kml"]
)
def test_client_path_is_reduced_to_the_base_name(client, settings, sent_name):
    # python-multipart already strips drive-letter paths such as C:\...\survey.kml, so
    # those are not tested here: they never reach the route. These shapes do.
    body = upload(client, sent_name).json()
    assert body["filename"] == "survey.kml"
    # The file on disk is named by id, so the client name never reaches the path.
    assert saved_uploads(settings) == [f"{body['id']}.kml"]


def test_upload_without_trailing_slash_is_served_directly(client):
    response = upload(client, "survey.kml", path="/api/files", follow_redirects=False)
    assert response.status_code == 202


def test_slashless_alias_is_hidden_from_the_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/files/" in paths
    assert "/api/files" not in paths


# Rejected uploads: nothing may be saved


@pytest.mark.parametrize("filename", ["survey.geojson", "survey.kmz", "survey", "kml"])
def test_unsupported_extension_returns_415(client, session, settings, filename):
    response = upload(client, filename)

    assert response.status_code == 415
    assert response.json()["detail"] == "Only .kml and .zip files are supported"
    assert saved_uploads(settings) == []
    assert session.exec(select(File)).all() == []


def test_file_over_the_limit_but_inside_the_body_allowance_returns_413(
    client, session, settings
):
    # Small enough to pass the middleware, which allows for multipart framing, so this
    # reaches the exact file-size check in the route.
    limit = settings.max_upload_bytes
    response = upload(client, "survey.kml", b"x" * (limit + 1))

    assert response.status_code == 413
    assert response.json()["detail"] == f"Maximum upload size is {limit} bytes"
    assert saved_uploads(settings) == []
    assert session.exec(select(File)).all() == []


def test_file_exactly_at_the_limit_is_accepted(client, settings):
    response = upload(client, "survey.kml", b"x" * settings.max_upload_bytes)
    assert response.status_code == 202


def test_oversize_body_returns_413(client, session, settings):
    response = upload(client, "survey.kml", b"x" * (settings.max_body_bytes + 1))

    assert response.status_code == 413
    assert saved_uploads(settings) == []
    assert session.exec(select(File)).all() == []


# The next two tests upload an unsupported extension on purpose. The route would answer
# 415, so a 413 proves the middleware stopped the request before the route ran. With a
# .kml name, the route's own size check would also return 413 and hide a missing or
# broken middleware (found by mutation check: unwiring it left every test green).


def test_oversize_body_is_rejected_before_the_route_runs(client, settings):
    response = upload(client, "survey.geojson", b"x" * (settings.max_body_bytes + 1))
    assert response.status_code == 413


def test_oversize_body_without_content_length_is_counted_and_rejected(client, settings):
    # A generator body is sent chunked, with no Content-Length, so only the byte count in
    # the middleware can catch it.
    boundary = "meridian-test-boundary"
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"survey.geojson\"\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body():
        yield head
        yield b"x" * (settings.max_body_bytes + 1)
        yield tail

    response = client.post(
        "/api/files/",
        content=body(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert saved_uploads(settings) == []


# The Content-Length shortcut, tested on the middleware alone so the test can prove that
# not a single body byte was read.


def test_declared_content_length_over_the_limit_is_rejected_before_reading():
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    async def receive():
        raise AssertionError("the body must not be read")

    sent = []

    async def send(message):
        sent.append(message)

    middleware = UploadSizeLimitMiddleware(app, max_body_bytes=100, limit_label="100 bytes")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/files/",
        "headers": [(b"content-length", b"101")],
    }
    asyncio.run(middleware(scope, receive, send))

    assert not app_called
    assert sent[0]["status"] == 413
    assert b"Maximum upload size is 100 bytes" in sent[1]["body"]


def test_middleware_ignores_other_routes():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = UploadSizeLimitMiddleware(app, max_body_bytes=100, limit_label="100 bytes")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/files/",
        "headers": [(b"content-length", b"999")],
    }
    asyncio.run(middleware(scope, None, None))
    assert called
