"""Database tables: File and Feature (docs/ARCHITECTURE.md §4).

Geometry is stored as GeoJSON text and properties as JSON (docs/DECISIONS.md D6). Summary
figures such as total area are computed on read, never stored, so they cannot drift from
the Feature rows.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileFormat(str, Enum):
    SHAPEFILE = "SHAPEFILE"
    KML = "KML"


class FileStatus(str, Enum):
    """Lifecycle in docs/WORKFLOWS.md §12. Transitions only move forward."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementType(str, Enum):
    AREA = "area"
    LENGTH = "length"
    NONE = "none"


class MeasurementMethod(str, Enum):
    """How a value was computed (docs/DECISIONS.md D22)."""

    PROJECTED = "projected"
    GEODESIC = "geodesic"


class File(SQLModel, table=True):
    # UUID, not an integer, so ids in URLs cannot be guessed (docs/DECISIONS.md D8).
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    filename: str
    format: FileFormat
    # "MIXED" when layers disagree; the inferred value when crs_assumed is true.
    crs: str | None = None
    crs_assumed: bool = False
    # Null until processing finishes: an unprocessed file has an unknown count, not zero.
    feature_count: int | None = None
    status: FileStatus = Field(default=FileStatus.PENDING, index=True)
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    completed_at: datetime | None = None


class Feature(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("file_id", "feature_index"),)

    id: int | None = Field(default=None, primary_key=True)
    file_id: uuid.UUID = Field(foreign_key="file.id", index=True)
    # Exposed as "index" in the API (docs/DECISIONS.md D19).
    feature_index: int
    layer: str
    geometry_type: str | None = None
    geometry: str | None = None  # GeoJSON in the source CRS
    properties: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    source_crs: str | None = None
    measurement_type: MeasurementType = MeasurementType.NONE
    measurement_method: MeasurementMethod | None = None
    value: float | None = None
    unit: str | None = None
    geodesic_value: float | None = None
    measurement_crs: str | None = None
    repaired: bool = False
    note: str | None = None
