"""Response models for the files API (docs/API.md §8).

Timestamps need no conversion here: SQLModel maps datetime columns to its UTCDateTime
type, which returns timezone-aware UTC values from SQLite, so they serialize with a
trailing "Z". A test pins this in case a SQLModel upgrade changes it.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import FileFormat, FileStatus


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
