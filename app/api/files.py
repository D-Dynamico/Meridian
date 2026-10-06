"""Files API routes (docs/API.md §8).

Routes only. GeoPandas, Shapely and pyproj must never be imported here, directly or
through another module (docs/ARCHITECTURE.md §3). A test checks this in a clean
subprocess.

Each route is registered twice: with the trailing slash the brief uses, and without it,
hidden from the schema, so neither form is answered with a redirect
(docs/DECISIONS.md D18).
"""

import json
import shutil
import uuid
from pathlib import PurePath, PureWindowsPath
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, UploadFile, status
from sqlmodel import func, select

from app.api.deps import ProcessorDep, SessionDep, SettingsDep
from app.core.config import FORMAT_BY_EXTENSION
from app.db import queries
from app.db.models import Feature, File, FileFormat, FileStatus, MeasurementType
from app.schemas.files import (
    FileDetail,
    FileListItem,
    FileListResponse,
    MeasurementItem,
    MeasurementsResponse,
    MeasurementValue,
    Summary,
    UploadResponse,
    metres,
)

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post(
    "", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse,
    include_in_schema=False,
)
@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadResponse,
    responses={
        413: {"description": "File larger than the upload limit"},
        415: {"description": "Not a .zip or .kml file"},
    },
)
def upload_file(
    file: UploadFile,
    session: SessionDep,
    settings: SettingsDep,
    processor: ProcessorDep,
    background_tasks: BackgroundTasks,
):
    """Upload a Shapefile .zip or a .kml. Returns 202 at once; processing runs later."""
    # Keep only the base name. python-multipart strips drive-letter paths (C:\...) but
    # passes relative and POSIX paths through. PureWindowsPath splits on both / and \.
    filename = PureWindowsPath(file.filename or "").name
    extension = PurePath(filename).suffix.lower()

    if extension not in settings.allowed_extensions:
        allowed = " and ".join(sorted(settings.allowed_extensions))
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Only {allowed} files are supported",
        )

    # The middleware limits the whole request body, envelope included. This checks the
    # file itself against the exact limit.
    if _size_of(file) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Maximum upload size is {settings.max_upload_label}",
        )

    record = File(filename=filename, format=FileFormat(FORMAT_BY_EXTENSION[extension]))
    # Stored under the generated id, never the client's name, so the client controls
    # nothing about the path on disk.
    destination = settings.upload_path(record.id, record.format.value)
    with destination.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        session.add(record)
        session.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    # Runs after the response is sent. The processor arrives through a dependency, so
    # this module never imports it or any geospatial library (docs/DECISIONS.md D28).
    background_tasks.add_task(processor, record.id)
    return UploadResponse(id=record.id, filename=record.filename, status=record.status)


@router.get("", response_model=FileListResponse, include_in_schema=False)
@router.get("/", response_model=FileListResponse)
def list_files(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """List uploaded files, newest first (docs/API.md §8.6)."""
    total = session.exec(select(func.count()).select_from(File)).one()
    # id breaks ties between uploads with the same timestamp, so pages never overlap.
    files = session.exec(
        select(File)
        .order_by(File.created_at.desc(), File.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return FileListResponse(
        count=total,
        limit=limit,
        offset=offset,
        results=[FileListItem.model_validate(f, from_attributes=True) for f in files],
    )


@router.get("/{file_id}", response_model=FileDetail, include_in_schema=False)
@router.get("/{file_id}/", response_model=FileDetail, responses={404: {"description": "File not found"}})
def get_file(file_id: uuid.UUID, session: SessionDep):
    """File information and status, with a summary once processing has completed."""
    file = _get_or_404(session, file_id)
    summary = None
    if file.status == FileStatus.COMPLETED:
        totals = queries.file_totals(session, file_id)
        summary = Summary.from_totals(totals.by_type, totals.area_m2, totals.length_m)
    return FileDetail(**file.model_dump(), summary=summary)


MEASUREMENTS_RESPONSES = {
    404: {"description": "File not found"},
    409: {"description": "File is not COMPLETED (still processing, or FAILED with its reason)"},
}


@router.get(
    "/{file_id}/measurements",
    response_model=MeasurementsResponse,
    response_model_exclude_unset=True,
    include_in_schema=False,
)
@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    response_model_exclude_unset=True,
    responses=MEASUREMENTS_RESPONSES,
)
def get_measurements(
    file_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    geometry_type: Annotated[str | None, Query(examples=["Polygon"])] = None,
    include_geometry: bool = True,
):
    """Per-feature measurements in file order (docs/API.md §8.4)."""
    file = _get_or_404(session, file_id)
    if file.status == FileStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"File processing FAILED: {file.error}")
    if file.status != FileStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"File is still {file.status.value}")

    total, rows = queries.measurements_page(session, file_id, limit, offset, geometry_type)
    return MeasurementsResponse(
        file_id=file_id,
        count=total,
        limit=limit,
        offset=offset,
        results=[_measurement_item(row, include_geometry) for row in rows],
    )


def _measurement_item(row: Feature, include_geometry: bool) -> MeasurementItem:
    # Every field is passed explicitly, None included, because the response is sent with
    # exclude_unset: a field left out here would vanish from the JSON. Only geometry is
    # left out on purpose, when the caller asked for no geometry.
    fields = dict(
        index=row.feature_index,
        layer=row.layer,
        geometry_type=row.geometry_type,
        source_crs=row.source_crs,
        measurement_method=row.measurement_method,
        measurement_crs=row.measurement_crs,
        measurement=_measurement_value(row),
        geodesic_value=metres(row.geodesic_value),
        repaired=row.repaired,
        note=row.note,
        properties=row.properties,
    )
    if include_geometry:
        fields["geometry"] = None if row.geometry is None else json.loads(row.geometry)
    return MeasurementItem(**fields)


def _measurement_value(row: Feature) -> MeasurementValue | None:
    if row.value is None:
        return None
    if row.measurement_type == MeasurementType.AREA:
        return MeasurementValue(
            type="area", value=metres(row.value), unit="m2", hectares=round(row.value / 10_000, 4)
        )
    return MeasurementValue(type="length", value=metres(row.value), unit="m", km=round(row.value / 1000, 4))


def _get_or_404(session, file_id: uuid.UUID) -> File:
    file = session.get(File, file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
    return file


def _size_of(upload: UploadFile) -> int:
    if upload.size is not None:
        return upload.size
    # Starlette sets size while parsing; measure the spooled file if it ever does not.
    position = upload.file.tell()
    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(position)
    return size
