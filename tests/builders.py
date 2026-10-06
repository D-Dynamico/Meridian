"""Build test inputs in code (docs/TEST_PLAN.md §15), so every expected value is visible
in the test that uses it and no binary fixtures are committed."""

import io
import math
import struct
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon

GEOD = Geod(ellps="WGS84")


# Accuracy fixtures (docs/CRS.md §9.5). Built on the ellipsoid with Geod.fwd, which walks
# true ground distances. A square drawn in UTM coordinates would measure exactly
# 1,000,000 m2 in that zone and could not catch a missing or wrong transform.


def ground_rectangle(lon: float, lat: float, width: float, height: float) -> Polygon:
    """A width x height metre rectangle on the ground, counter-clockwise from its SW corner."""
    e_lon, e_lat, _ = GEOD.fwd(lon, lat, 90, width)
    ne_lon, ne_lat, _ = GEOD.fwd(e_lon, e_lat, 0, height)
    n_lon, n_lat, _ = GEOD.fwd(lon, lat, 0, height)
    return Polygon([(lon, lat), (e_lon, e_lat), (ne_lon, ne_lat), (n_lon, n_lat)])


def ground_square(lon: float, lat: float, side: float = 1000.0) -> Polygon:
    return ground_rectangle(lon, lat, side, side)


def ground_line(lon: float, lat: float, length: float = 1000.0, azimuth: float = 0.0) -> LineString:
    end_lon, end_lat, _ = GEOD.fwd(lon, lat, azimuth, length)
    return LineString([(lon, lat), (end_lon, end_lat)])


def utm_area_gap(lon: float, lat: float, zone: int) -> float:
    """Expected projected/geodesic area ratio minus 1, from the UTM scale factor.

    k = 0.9996 * (1 + (dlon * cos(lat))^2 / 2), with dlon in radians from the zone's
    central meridian; area scales by k^2. Independent of the code under test, so it can
    tell a right zone from a wrong one, which a 0.25 percent band alone cannot.
    """
    central_meridian = -183 + 6 * zone
    dlon = math.radians(lon - central_meridian)
    k = 0.9996 * (1 + (dlon * math.cos(math.radians(lat))) ** 2 / 2)
    return k * k - 1

# Small shapes near Jaipur. Not accuracy fixtures: those are built with Geod.fwd in
# Stage 3 (docs/CRS.md §9.5).
PLOT = Polygon([(75.80, 26.90), (75.81, 26.90), (75.81, 26.91), (75.80, 26.91)])
ROAD = LineString([(75.80, 26.90), (75.81, 26.91)])
GATE = Point(75.80, 26.90)


def shapefile_parts(frame: gpd.GeoDataFrame, stem: str = "parcels") -> dict[str, bytes]:
    """Write a GeoDataFrame as a shapefile and return {"parcels.shp": bytes, ...}."""
    with tempfile.TemporaryDirectory() as folder:
        frame.to_file(Path(folder) / f"{stem}.shp")
        return {p.name: p.read_bytes() for p in Path(folder).iterdir()}


def plots(crs: str | None = "EPSG:4326", count: int = 1) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": [f"Plot {i}" for i in range(count)]}, geometry=[PLOT] * count, crs=crs
    )


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def without(entries: dict[str, bytes], *extensions: str) -> dict[str, bytes]:
    return {n: d for n, d in entries.items() if not n.lower().endswith(extensions)}


def under(folder: str, entries: dict[str, bytes]) -> dict[str, bytes]:
    return {f"{folder}/{name}": data for name, data in entries.items()}


def set_flag_bits(zip_bytes: bytes, bits: int) -> bytes:
    """Set general-purpose flag bits on the first entry, in both of its headers.

    The standard library cannot write encrypted zips, so tests mark an entry as
    encrypted by setting bit 0 directly.
    """
    data = bytearray(zip_bytes)
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    for offset in (local + 6, central + 8):
        (flags,) = struct.unpack_from("<H", data, offset)
        struct.pack_into("<H", data, offset, flags | bits)
    return bytes(data)


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# KML


def kml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Survey</name>'
        f"{body}</Document></kml>"
    )


def folder(name: str, *placemarks: str) -> str:
    return f"<Folder><name>{name}</name>{''.join(placemarks)}</Folder>"


def placemark(name: str, geometry: str, extended: dict[str, str] | None = None) -> str:
    data = ""
    if extended:
        fields = "".join(
            f'<Data name="{k}"><value>{v}</value></Data>' for k, v in extended.items()
        )
        data = f"<ExtendedData>{fields}</ExtendedData>"
    return f"<Placemark><name>{name}</name>{data}{geometry}</Placemark>"


def kml_polygon(shape: Polygon = PLOT, z: float | None = 0.0) -> str:
    coords = " ".join(_coord(x, y, z) for x, y in shape.exterior.coords)
    return (
        "<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{coords}</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon>"
    )


def kml_line(shape: LineString = ROAD) -> str:
    coords = " ".join(_coord(x, y, None) for x, y in shape.coords)
    return f"<LineString><coordinates>{coords}</coordinates></LineString>"


def kml_point(shape: Point = GATE) -> str:
    return f"<Point><coordinates>{_coord(shape.x, shape.y, None)}</coordinates></Point>"


def _coord(x: float, y: float, z: float | None) -> str:
    return f"{x},{y}" if z is None else f"{x},{y},{z}"


def survey_kml() -> bytes:
    """A two-folder KML with a 1 km square, a 1 km line, a point and a placemark with no
    geometry, near Jaipur. Used by the processor and end-to-end API tests."""
    jaipur = (75.79, 26.91)
    return kml(
        folder("Parcels", placemark("Plot 12", kml_polygon(ground_square(*jaipur)), {"owner": "Asha"}))
        + folder(
            "Roads",
            placemark("Access road", kml_line(ground_line(*jaipur))),
            placemark("Gate", kml_point()),
            "<Placemark><name>Unplaced</name></Placemark>",
        )
    ).encode()
