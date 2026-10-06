"""Response models for the files API (docs/API.md §8)."""

import uuid

from pydantic import BaseModel

from app.db.models import FileStatus


class UploadResponse(BaseModel):
    id: uuid.UUID
    filename: str
    status: FileStatus
