"""Files API routes (docs/API.md §8).

Routes only. GeoPandas, Shapely and pyproj must never be imported here, directly or
through another module (docs/ARCHITECTURE.md §3). A test checks this in a clean
subprocess.

Each route is registered twice: with the trailing slash the brief uses, and without it,
hidden from the schema, so neither form is answered with a redirect
(docs/DECISIONS.md D18).
"""

import shutil
from pathlib import PurePath, PureWindowsPath

from fastapi import APIRouter, HTTPException, UploadFile, status

from app.api.deps import SessionDep, SettingsDep
from app.core.config import FORMAT_BY_EXTENSION
from app.db.models import File, FileFormat
from app.schemas.files import UploadResponse

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
def upload_file(file: UploadFile, session: SessionDep, settings: SettingsDep):
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
    destination = settings.uploads_dir / f"{record.id}{extension}"
    with destination.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        session.add(record)
        session.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    # No background task yet: the processor arrives in Stage 4, and until then files
    # stay PENDING (docs/WORKFLOWS.md §13).
    return UploadResponse(id=record.id, filename=record.filename, status=record.status)


def _size_of(upload: UploadFile) -> int:
    if upload.size is not None:
        return upload.size
    # Starlette sets size while parsing; measure the spooled file if it ever does not.
    position = upload.file.tell()
    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(position)
    return size
