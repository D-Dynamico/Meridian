"""Read an uploaded Shapefile zip or KML into layers of features (docs/ARCHITECTURE.md §5).

The loader returns layers rather than one combined GeoDataFrame, because a GeoDataFrame
carries a single CRS and a zip can hold shapefiles in different CRSs. The processor walks
the layers in order, which gives every feature a stable index across the whole file.

Only file-level problems raise LoaderError: a corrupt or unsafe zip, missing shapefile
parts, an unreadable file. Anything wrong with a single feature is the measure service's
business, never the loader's.
"""

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import geopandas as gpd
import pyogrio
from pyogrio.errors import DataSourceError, DataLayerError

REQUIRED_SHAPEFILE_PARTS = (".shp", ".shx", ".dbf")

# LIBKML adds these display hints to every KML layer and fills tessellate, extrude and
# visibility with defaults even when the file does not contain them, so a real value
# cannot be told apart from a default. They are always dropped (docs/DECISIONS.md D26).
KML_DISPLAY_FIELDS = ("altitudeMode", "tessellate", "extrude", "visibility", "drawOrder", "icon")
# Standard KML fields that are real data when present. Dropped only when empty for the
# whole layer, so an unused field does not appear as null on every feature.
KML_STANDARD_FIELDS = ("id", "Name", "description", "timestamp", "begin", "end")


class LoaderError(Exception):
    """A file-level problem. The message is shown to the user as the file's error."""


@dataclass(frozen=True)
class SourceLayer:
    name: str
    # Geometry plus attribute columns, in the layer's own CRS. frame.crs is None when a
    # shapefile has no .prj; the CRS service decides what that means.
    frame: gpd.GeoDataFrame


@dataclass(frozen=True)
class LoadedFile:
    layers: list[SourceLayer]

    @property
    def feature_count(self) -> int:
        return sum(len(layer.frame) for layer in self.layers)


def load(
    path: Path,
    file_format: Literal["SHAPEFILE", "KML"],
    work_dir: Path,
    max_extracted_bytes: int,
) -> LoadedFile:
    """Read every layer of the file. Raises LoaderError for file-level problems.

    Zip contents are extracted into a temporary folder under work_dir, which is always
    removed before this returns or raises. Everything is read into memory first.
    """
    if file_format == "KML":
        return _load_kml(path)
    return _load_shapefile_zip(path, work_dir, max_extracted_bytes)


# KML


def _load_kml(path: Path) -> LoadedFile:
    try:
        # Every layer must be listed and read by name. Without a layer name, GeoPandas
        # reads only the first folder and merely warns about the rest (D12).
        layer_names = [name for name, _ in pyogrio.list_layers(path)]
        layers = [
            SourceLayer(name, _clean_kml_frame(gpd.read_file(path, layer=name)))
            for name in layer_names
        ]
    except (DataSourceError, DataLayerError) as exc:
        raise LoaderError(f"The KML file could not be read: {_scrub(exc, path, 'the file')}") from exc
    # A document with no placemarks has no layers at all. That is a valid, empty file.
    return LoadedFile(layers)


def _clean_kml_frame(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    # No CRS fallback is needed: GDAL always reports EPSG:4326 for KML, as the KML
    # specification requires, and a test pins that.
    unused = [f for f in KML_STANDARD_FIELDS if f in frame and _all_empty(frame[f])]
    drop = [f for f in KML_DISPLAY_FIELDS if f in frame] + unused
    return frame.drop(columns=drop)


def _all_empty(column) -> bool:
    # LIBKML returns missing text fields as null or as "", depending on the field.
    return all(v is None or v != v or (isinstance(v, str) and not v.strip()) for v in column)


# Shapefile zip


def _load_shapefile_zip(path: Path, work_dir: Path, max_extracted_bytes: int) -> LoadedFile:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise LoaderError("The file is not a valid zip archive") from exc

    with archive:
        members = _safe_members(archive)
        groups = _shapefile_groups(members)
        # Zip bomb guard. Checking the declared sizes is enough: Python's zipfile never
        # yields more bytes than an entry declares, and a header that understates the
        # size fails its CRC check on read, which surfaces as a corrupt zip below.
        declared = sum(info.file_size for parts in groups.values() for info in parts.values())
        if declared > max_extracted_bytes:
            raise LoaderError(
                f"The zip expands to more than {max_extracted_bytes // (1024 * 1024)} MB"
            )

        work_dir.mkdir(parents=True, exist_ok=True)
        extract_root = Path(tempfile.mkdtemp(dir=work_dir))
        try:
            layers = []
            for number, (zip_path, parts) in enumerate(groups.items()):
                # Parts are written under fixed names in a folder per layer. No name from
                # the zip ever reaches the file system, and lower-case extensions let
                # GDAL find the parts on case-sensitive file systems.
                layer_dir = extract_root / str(number)
                layer_dir.mkdir()
                for extension, info in parts.items():
                    _extract(archive, info, layer_dir / f"layer{extension}")
                name = _layer_name(zip_path, groups)
                layers.append(SourceLayer(name, _read_shapefile(layer_dir / "layer.shp", name)))
            return LoadedFile(layers)
        finally:
            shutil.rmtree(extract_root)


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Reject the whole zip if any entry could escape the extraction folder (zip slip).

    Entry names are never used as extraction paths (see _load_shapefile_zip), so this is
    a second line of defence. A legitimate shapefile zip has no reason to contain such
    names, so the file is refused outright rather than having entries skipped.
    """
    members = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
            raise LoaderError(f"The zip contains an unsafe path: {info.filename}")
        if info.is_dir() or _is_os_metadata(parts):
            continue
        members.append(info)
    return members


def _is_os_metadata(parts: tuple[str, ...]) -> bool:
    # macOS adds __MACOSX/._name.shp resource forks that look like real shapefile parts.
    return "__MACOSX" in parts or parts[-1].startswith("._") or parts[-1] == ".DS_Store"


def _shapefile_groups(members: list[zipfile.ZipInfo]) -> dict[str, dict[str, zipfile.ZipInfo]]:
    """Group entries into shapefiles: {path without extension: {".ext": entry}}.

    Matching is case-insensitive, since some GIS tools write PARCELS.SHP. Every file
    sharing the stem is kept, so .prj and .cpg (the attribute encoding) travel along.
    """
    by_stem: dict[str, dict[str, zipfile.ZipInfo]] = {}
    for info in members:
        posix = PurePosixPath(info.filename.replace("\\", "/"))
        key = str(posix.with_suffix("")).lower()
        by_stem.setdefault(key, {})[posix.suffix.lower()] = info

    groups = {}
    for parts in by_stem.values():
        if ".shp" in parts:
            shp_name = parts[".shp"].filename.replace("\\", "/")
            groups[str(PurePosixPath(shp_name).with_suffix(""))] = parts
    if not groups:
        raise LoaderError("The zip contains no shapefile (.shp)")

    problems = []
    for zip_path, parts in groups.items():
        missing = [ext for ext in REQUIRED_SHAPEFILE_PARTS if ext not in parts]
        if missing:
            problems.append(
                f"Shapefile '{PurePosixPath(zip_path).name}' is missing {' and '.join(missing)}"
            )
    if problems:
        raise LoaderError("; ".join(problems))
    return groups


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    try:
        with archive.open(info) as source, target.open("wb") as out:
            shutil.copyfileobj(source, out)
    except zipfile.BadZipFile as exc:  # bad CRC, truncated data, understated size
        raise LoaderError(f"The zip is corrupt: {exc}") from exc
    except RuntimeError as exc:  # zipfile's error for an encrypted entry
        raise LoaderError("Password-protected zips are not supported") from exc


def _layer_name(zip_path: str, groups: dict) -> str:
    """The shapefile's name, or its path inside the zip if two share a name."""
    stem = PurePosixPath(zip_path).name
    same = [p for p in groups if PurePosixPath(p).name.lower() == stem.lower()]
    return stem if len(same) == 1 else zip_path


def _read_shapefile(shp_path: Path, name: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(shp_path)
    except (DataSourceError, DataLayerError) as exc:
        raise LoaderError(
            f"Shapefile '{name}' could not be read: {_scrub(exc, shp_path, name + '.shp')}"
        ) from exc


def _scrub(exc: Exception, path: Path, shown: str) -> str:
    """GDAL errors quote the full server path. Replace it with a name the user knows."""
    message = str(exc)
    for form in (str(path), path.as_posix()):
        message = message.replace(form, shown)
    return message
