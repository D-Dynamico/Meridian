"""services/processor (docs/TEST_PLAN.md §16). Runs the processor directly, no HTTP."""

import dataclasses
import json

import pytest
from sqlmodel import Session, select

from app.core.config import Settings
from app.db.models import Feature, File, FileFormat, FileStatus
from app.db.session import init_db, make_engine
from app.services import processor as processor_module
from app.services.processor import INTERRUPTED, process_file, recover_interrupted
from tests.builders import (
    folder,
    ground_line,
    ground_square,
    kml,
    kml_line,
    kml_point,
    kml_polygon,
    placemark,
    plots,
    shapefile_parts,
    without,
    write_zip,
)

JAIPUR = (75.79, 26.91)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def engine(settings):
    settings.uploads_dir.mkdir(parents=True)
    engine = make_engine(settings.database_url)
    init_db(engine)
    yield engine
    engine.dispose()


def add_upload(engine, settings, file_format: FileFormat, content: bytes, status=FileStatus.PENDING):
    with Session(engine) as session:
        file = File(filename=f"upload.{file_format.value.lower()}", format=file_format, status=status)
        session.add(file)
        session.commit()
        session.refresh(file)
        settings.upload_path(file.id, file.format.value).write_bytes(content)
        return file.id


def run(engine, settings, file_id):
    process_file(file_id, engine=engine, settings=settings)
    with Session(engine) as session:
        file = session.get(File, file_id)
        features = session.exec(
            select(Feature).where(Feature.file_id == file_id).order_by(Feature.feature_index)
        ).all()
        session.expunge_all()
        return file, features


def zip_bytes_of(tmp_path, entries) -> bytes:
    return write_zip(tmp_path / "build.zip", entries).read_bytes()


SURVEY_KML = kml(
    folder("Parcels", placemark("Plot 12", kml_polygon(ground_square(*JAIPUR)), {"owner": "Asha"}))
    + folder("Roads", placemark("Access road", kml_line(ground_line(*JAIPUR))), placemark("Gate", kml_point()))
).encode()


# Happy path


def test_kml_is_processed_to_completed(engine, settings):
    file_id = add_upload(engine, settings, FileFormat.KML, SURVEY_KML)
    file, features = run(engine, settings, file_id)

    assert file.status == FileStatus.COMPLETED
    assert file.error is None
    assert file.completed_at is not None
    assert file.feature_count == 3
    assert file.crs == "EPSG:4326"
    assert file.crs_assumed is False

    assert [(f.feature_index, f.layer, f.geometry_type) for f in features] == [
        (0, "Parcels", "Polygon"), (1, "Roads", "LineString"), (2, "Roads", "Point"),
    ]
    polygon, line, point = features
    assert polygon.value == pytest.approx(1_000_000, rel=0.005)
    assert polygon.unit == "m2" and polygon.measurement_crs == "EPSG:32643"
    assert polygon.properties == {"Name": "Plot 12", "owner": "Asha"}
    assert line.value == pytest.approx(1000, rel=0.005)
    assert point.value is None and point.note == "No measurement for point geometries"
    assert json.loads(polygon.geometry)["type"] == "Polygon"


def test_shapefile_is_processed_with_its_crs(engine, settings, tmp_path):
    content = zip_bytes_of(tmp_path, shapefile_parts(plots(crs="EPSG:4326", count=2)))
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.SHAPEFILE, content))
    assert file.status == FileStatus.COMPLETED
    assert file.feature_count == 2
    assert file.crs == "EPSG:4326"
    assert [f.source_crs for f in features] == ["EPSG:4326", "EPSG:4326"]


def test_missing_prj_is_assumed_and_flagged(engine, settings, tmp_path):
    content = zip_bytes_of(tmp_path, without(shapefile_parts(plots()), ".prj"))
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.SHAPEFILE, content))
    assert file.crs == "EPSG:4326"
    assert file.crs_assumed is True
    assert features[0].note.startswith("CRS assumed EPSG:4326 (no .prj)")


def test_layers_in_different_crss_make_the_file_mixed(engine, settings, tmp_path):
    entries = {
        **shapefile_parts(plots(crs="EPSG:4326"), stem="geographic"),
        **shapefile_parts(plots(crs="EPSG:4326").to_crs("EPSG:32643"), stem="projected"),
    }
    file, features = run(
        engine, settings, add_upload(engine, settings, FileFormat.SHAPEFILE, zip_bytes_of(tmp_path, entries))
    )
    assert file.crs == "MIXED"
    assert sorted(f.source_crs for f in features) == ["EPSG:32643", "EPSG:4326"]


def test_layer_cap_comes_from_settings(engine, settings):
    settings = dataclasses.replace(settings, max_layers=1)
    document = kml(folder("A", placemark("a", kml_point())) + folder("B", placemark("b", kml_point())))
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.KML, document.encode()))
    assert (file.status, file.error) == (FileStatus.FAILED, "The KML has 2 folders; at most 1 are supported")
    assert features == []


def test_file_with_no_features_completes_with_zero(engine, settings):
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.KML, kml("").encode()))
    assert file.status == FileStatus.COMPLETED
    assert file.feature_count == 0
    assert features == []


def test_status_is_processing_while_the_loader_runs(engine, settings, monkeypatch):
    file_id = add_upload(engine, settings, FileFormat.KML, SURVEY_KML)
    seen = []
    real_load = processor_module.load

    def watching_load(*args, **kwargs):
        with Session(engine) as session:
            seen.append(session.get(File, file_id).status)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(processor_module, "load", watching_load)
    file, _ = run(engine, settings, file_id)
    assert seen == [FileStatus.PROCESSING]
    assert file.status == FileStatus.COMPLETED


# Failure handling


def test_one_feature_that_raises_is_recorded_and_the_rest_complete(engine, settings, monkeypatch):
    real_measure = processor_module.measure
    calls = []

    def flaky_measure(geometry, *args):
        calls.append(geometry)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real_measure(geometry, *args)

    monkeypatch.setattr(processor_module, "measure", flaky_measure)
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.KML, SURVEY_KML))

    assert file.status == FileStatus.COMPLETED
    assert file.feature_count == 3
    assert features[1].value is None
    assert features[1].note == "Measurement failed because of an unexpected server error"
    assert features[0].value is not None  # before the failure
    assert features[2].note == "No measurement for point geometries"  # after it


def test_file_level_problem_fails_the_file_with_a_reason(engine, settings, tmp_path):
    content = zip_bytes_of(tmp_path, without(shapefile_parts(plots()), ".dbf"))
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.SHAPEFILE, content))
    assert file.status == FileStatus.FAILED
    assert file.error == "Shapefile 'parcels' is missing .dbf"
    assert file.completed_at is not None
    assert features == []


def test_unexpected_error_fails_the_file_without_leaking_internals(engine, settings, monkeypatch):
    def broken_load(*args, **kwargs):
        raise KeyError("internal detail")

    monkeypatch.setattr(processor_module, "load", broken_load)
    file, _ = run(engine, settings, add_upload(engine, settings, FileFormat.KML, SURVEY_KML))
    assert file.status == FileStatus.FAILED
    assert "internal detail" not in file.error
    assert "unexpected server error" in file.error


def test_failure_after_some_rows_were_added_leaves_no_rows(engine, settings, monkeypatch):
    # A crash after rows were added but before the commit: none of them may survive.
    real_record = processor_module._record

    def record_then_crash(session, file, loaded):
        real_record(session, file, loaded)
        raise MemoryError("ran out of memory after recording")

    monkeypatch.setattr(processor_module, "_record", record_then_crash)
    file, features = run(engine, settings, add_upload(engine, settings, FileFormat.KML, SURVEY_KML))
    assert file.status == FileStatus.FAILED
    assert file.feature_count is None
    assert features == []


# Files and statuses


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_upload_is_deleted_after_processing(engine, settings, tmp_path, outcome):
    if outcome == "success":
        file_id = add_upload(engine, settings, FileFormat.KML, SURVEY_KML)
    else:
        file_id = add_upload(engine, settings, FileFormat.KML, b"not xml")
    run(engine, settings, file_id)
    assert list(settings.uploads_dir.iterdir()) == []
    assert not settings.work_dir.exists() or list(settings.work_dir.iterdir()) == []


@pytest.mark.parametrize("status", [FileStatus.PROCESSING, FileStatus.COMPLETED, FileStatus.FAILED])
def test_only_pending_files_are_processed(engine, settings, status):
    file_id = add_upload(engine, settings, FileFormat.KML, SURVEY_KML, status=status)
    file, features = run(engine, settings, file_id)
    assert file.status == status
    assert features == []


def test_recovery_fails_interrupted_files_and_leaves_finished_ones(engine, settings):
    pending = add_upload(engine, settings, FileFormat.KML, SURVEY_KML, FileStatus.PENDING)
    processing = add_upload(engine, settings, FileFormat.KML, SURVEY_KML, FileStatus.PROCESSING)
    done = add_upload(engine, settings, FileFormat.KML, SURVEY_KML, FileStatus.COMPLETED)

    assert recover_interrupted(engine, settings) == 2

    with Session(engine) as session:
        for file_id in (pending, processing):
            file = session.get(File, file_id)
            assert file.status == FileStatus.FAILED
            assert file.error == INTERRUPTED
            assert file.completed_at is not None
        assert session.get(File, done).status == FileStatus.COMPLETED
    remaining = [p.name for p in settings.uploads_dir.iterdir()]
    assert remaining == [f"{done}.kml"]
