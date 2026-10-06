"""Application settings.

No environment variables are required (docs/WORKFLOWS.md §11). Two optional ones exist for
deployment: MERIDIAN_DATA_DIR and MERIDIAN_MAX_UPLOAD_MB. Tests build a Settings object
directly instead, pointing data_dir at a temporary folder.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Multipart framing (boundaries and part headers) adds a few hundred bytes around the file.
# The request-body limit allows this much on top of the file limit, so a file just under
# the limit is never rejected for its envelope. The exact file size is checked separately.
MULTIPART_ALLOWANCE_BYTES = 64 * 1024

# Upload extensions and the format each one maps to (docs/ARCHITECTURE.md §4).
FORMAT_BY_EXTENSION = {".zip": "SHAPEFILE", ".kml": "KML"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    max_upload_bytes: int = 50 * 1024 * 1024  # docs/DECISIONS.md D20
    allowed_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset(FORMAT_BY_EXTENSION)
    )

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'meridian.db'}"

    @property
    def max_body_bytes(self) -> int:
        return self.max_upload_bytes + MULTIPART_ALLOWANCE_BYTES

    @property
    def max_upload_mb(self) -> int:
        return self.max_upload_bytes // (1024 * 1024)


def load_settings() -> Settings:
    """Build settings from the environment, falling back to the defaults."""
    defaults = Settings()
    return Settings(
        data_dir=Path(os.environ.get("MERIDIAN_DATA_DIR", defaults.data_dir)),
        max_upload_bytes=int(
            os.environ.get("MERIDIAN_MAX_UPLOAD_MB", defaults.max_upload_mb)
        )
        * 1024
        * 1024,
    )
