"""Run one uploaded file through loader, CRS and measure services (docs/ARCHITECTURE.md §5).

The routes never import this module (docs/DECISIONS.md D28). main.py, the composition
root, binds process_file to the engine and settings and hands the result to the upload
route through Depends(get_processor).
"""

import logging
import uuid
import shapely
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.config import Settings
from app.db.models import (
    Feature,
    File,
    FileFormat,
    FileStatus,
    MeasurementMethod,
    MeasurementType,
    utcnow,
)
from app.services import crs as crs_service
from app.services.loader import LoadedFile, LoaderError, load
from app.services.measure import Measurement, measure

logger = logging.getLogger(__name__)

MIXED_CRS = "MIXED"
INTERRUPTED = "Processing was interrupted by a server restart. Please upload the file again."


def process_file(file_id: uuid.UUID, *, engine: Engine, settings: Settings) -> None:
    """Process one file to COMPLETED or FAILED. Never raises: it runs in a background
    task, where an exception would only reach the server log."""
    with Session(engine) as session:
        file = session.get(File, file_id)
        # Transitions only move forward (§12). Anything but PENDING was already handled.
        if file is None or file.status != FileStatus.PENDING:
            return
        file.status = FileStatus.PROCESSING
        session.add(file)
        session.commit()

        path = settings.upload_path(file.id, file.format.value)
        try:
            loaded = load(path, file.format.value, settings.work_dir, settings.max_extracted_bytes)
            _record(session, file, loaded)
            file.status = FileStatus.COMPLETED
        except LoaderError as exc:
            session.rollback()
            file.status, file.error = FileStatus.FAILED, str(exc)
        except Exception:
            # A bug, not a bad file. The full traceback goes to the log; the user gets a
            # message that does not leak internals.
            logger.exception("Unexpected error while processing file %s", file_id)
            session.rollback()
            file.status = FileStatus.FAILED
            file.error = "Processing failed because of an unexpected server error."
        finally:
            path.unlink(missing_ok=True)

        file.completed_at = utcnow()
        session.add(file)
        session.commit()


def _record(session: Session, file: File, loaded: LoadedFile) -> None:
    labels, any_assumed, index = set(), False, 0
    for layer in loaded.layers:
        layer_crs = crs_service.resolve_layer_crs(
            layer.crs, [feature.geometry for feature in layer.features]
        )
        source_label = crs_service.crs_label(layer_crs.crs)
        labels.add(source_label)
        any_assumed |= layer_crs.assumed
        for feature in layer.features:
            session.add(_feature_row(file.id, index, layer.name, source_label, feature, layer_crs))
            index += 1

    file.feature_count = index
    file.crs_assumed = any_assumed
    if file.format == FileFormat.KML:
        file.crs = "EPSG:4326"  # fixed by the KML specification, even for an empty file
    elif len(labels) == 1:
        file.crs = labels.pop()
    elif len(labels) > 1:
        file.crs = MIXED_CRS


def _feature_row(file_id, index, layer_name, source_label, feature, layer_crs) -> Feature:
    row = Feature(
        file_id=file_id,
        feature_index=index,
        layer=layer_name,
        geometry_type=feature.geometry.geom_type if feature.geometry is not None else None,
        properties=feature.properties,
        source_crs=source_label,
    )
    # Per-feature error boundary: one feature that breaks never sinks the file.
    try:
        row.geometry = None if feature.geometry is None else shapely.to_geojson(feature.geometry)
        _apply(row, measure(feature.geometry, layer_crs.crs, layer_crs.assumed))
    except Exception as exc:
        logger.exception("Feature %s of file %s could not be measured", index, file_id)
        row.measurement_type = MeasurementType.NONE
        row.note = f"Measurement failed: {type(exc).__name__}: {exc}"
    return row


def _apply(row: Feature, result: Measurement) -> None:
    row.measurement_type = MeasurementType(result.measurement_type)
    row.measurement_method = MeasurementMethod(result.method) if result.method else None
    row.value = result.value
    row.unit = result.unit
    row.geodesic_value = result.geodesic_value
    row.measurement_crs = result.measurement_crs
    row.repaired = result.repaired
    row.note = result.note


def recover_interrupted(engine: Engine, settings: Settings) -> int:
    """Mark files left PENDING or PROCESSING by a previous process as FAILED (D17).

    BackgroundTasks run inside the server process, so a restart loses them and those
    files would otherwise report PROCESSING forever. This assumes a single server
    process: with several workers, one worker's startup would fail another's in-flight
    file. A durable queue is the real fix and is in future scope.
    """
    with Session(engine) as session:
        stuck = session.exec(
            select(File).where(File.status.in_([FileStatus.PENDING, FileStatus.PROCESSING]))
        ).all()
        for file in stuck:
            file.status, file.error, file.completed_at = FileStatus.FAILED, INTERRUPTED, utcnow()
            settings.upload_path(file.id, file.format.value).unlink(missing_ok=True)
            session.add(file)
        session.commit()
        return len(stuck)
