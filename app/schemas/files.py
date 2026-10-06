"""Response models for the files API (docs/API.md §8).

Timestamps need no conversion here: SQLModel maps datetime columns to its UTCDateTime
type, which returns timezone-aware UTC values from SQLite, so they serialize with a
trailing "Z". A test pins this in case a SQLModel upgrade changes it.

Rounding happens only here, on the way out; stored values are never rounded. Metres and
square metres get 2 decimals, which is already finer than UTM's accuracy. Hectares and
kilometres get 4, so a single square metre still shows in hectares.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.db.models import FileFormat, FileStatus, MeasurementMethod


def metres(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


class UploadResponse(BaseModel):
    id: uuid.UUID
    filename: str
    status: FileStatus


class FileListItem(BaseModel):
    id: uuid.UUID
    filename: str
    format: FileFormat
    status: FileStatus
    feature_count: int | None
    created_at: datetime
    completed_at: datetime | None


class FileListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    results: list[FileListItem]


class Summary(BaseModel):
    by_type: dict[str, int]
    total_area_m2: float
    total_area_ha: float
    total_length_m: float
    total_length_km: float

    @classmethod
    def from_totals(cls, by_type: dict[str, int], area_m2: float, length_m: float) -> "Summary":
        return cls(
            by_type=by_type,
            total_area_m2=round(area_m2, 2),
            total_area_ha=round(area_m2 / 10_000, 4),
            total_length_m=round(length_m, 2),
            total_length_km=round(length_m / 1000, 4),
        )


class FileDetail(BaseModel):
    id: uuid.UUID
    filename: str
    format: FileFormat
    feature_count: int | None
    crs: str | None
    crs_assumed: bool
    status: FileStatus
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    summary: Summary | None  # null until the file is COMPLETED


class MeasurementValue(BaseModel):
    type: Literal["area", "length"]
    value: float
    unit: Literal["m2", "m"]
    # Only the one that matches the type is sent (see MeasurementsResponse).
    hectares: float | None = None
    km: float | None = None


class MeasurementItem(BaseModel):
    index: int
    layer: str
    geometry_type: str | None
    source_crs: str | None
    measurement_method: MeasurementMethod | None
    measurement_crs: str | None
    measurement: MeasurementValue | None
    geodesic_value: float | None
    repaired: bool
    note: str | None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None = None


class MeasurementsResponse(BaseModel):
    """Served with response_model_exclude_unset. Every field is set explicitly, null
    included, except geometry when include_geometry=false and the hectares or km field
    that does not match the measurement type. Those are left unset, so they are left out
    of the JSON instead of appearing as null."""

    file_id: uuid.UUID
    count: int
    limit: int
    offset: int
    results: list[MeasurementItem]
