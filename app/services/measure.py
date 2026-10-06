"""Area for polygons, length for lines, nothing for points (docs/ARCHITECTURE.md §6).

measure() never raises for bad or unsupported input: every such case comes back as a
Measurement with no value and a note. The processor still wraps each call in its own
error boundary, for genuine bugs.
"""

from dataclasses import dataclass, field
from typing import Literal

import shapely
from pyproj import CRS, Geod
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from app.services import crs as crs_service

GEOD = Geod(ellps="WGS84")

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAL = {"LineString", "MultiLineString", "LinearRing"}
POINTS = {"Point", "MultiPoint"}

NOTE_CRS_ASSUMED = "CRS assumed EPSG:4326 (no .prj)"
NOTE_REPAIRED = "repaired invalid geometry"
NOTE_Z_IGNORED = "Z values ignored"
NOTE_EMPTY = "empty geometry"
NOTE_POINT = "no measurement for point geometries"
NOTE_DEGENERATE = "degenerate polygon"
NOTE_NO_CRS = "no CRS declared and coordinates are not longitude and latitude, so the units are unknown"
NOTE_GEODESIC = "outside UTM coverage, geodesic value used"


@dataclass(frozen=True)
class Measurement:
    measurement_type: Literal["area", "length", "none"] = "none"
    method: Literal["projected", "geodesic"] | None = None
    value: float | None = None
    unit: Literal["m2", "m"] | None = None
    geodesic_value: float | None = None
    measurement_crs: str | None = None
    repaired: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def note(self) -> str | None:
        """Notes joined in their fixed order, first letter capitalised (D16)."""
        if not self.notes:
            return None
        joined = "; ".join(self.notes)
        return joined[0].upper() + joined[1:]


def measure(geometry: BaseGeometry | None, source: CRS | None, crs_assumed: bool) -> Measurement:
    """Measure one feature given its layer's CRS (already resolved by the CRS service)."""
    # Fixed note order: CRS assumed, repaired, Z ignored, then the outcome.
    notes = [NOTE_CRS_ASSUMED] if crs_assumed else []

    if geometry is None or geometry.is_empty:
        return Measurement(notes=notes + [NOTE_EMPTY])

    kind = geometry.geom_type
    if kind in POINTS:
        return Measurement(notes=notes + [NOTE_POINT])
    if kind not in POLYGONAL | LINEAL:
        return Measurement(notes=notes + [f"unsupported geometry type: {kind}"])

    repaired = False
    if kind in POLYGONAL and not geometry.is_valid:
        geometry, repaired = _polygonal_parts(make_valid(geometry)), True
        notes.append(NOTE_REPAIRED)

    # Z needs no stripping: shapely's area and length, shapely.transform and pyproj's
    # Geod all work in 2D. A test pins that a rising line still measures its 2D length.
    if _has_nonzero_z(geometry):
        notes.append(NOTE_Z_IGNORED)

    measurement_type = "area" if kind in POLYGONAL else "length"
    if measurement_type == "area" and _has_no_area(geometry):
        # Nothing with area is left: make_valid returned only lines, or the polygon was
        # valid but collinear. Measuring leftover lines as a length would answer a
        # question nobody asked.
        return Measurement(repaired=repaired, notes=notes + [NOTE_DEGENERATE])

    if source is None:
        return Measurement(repaired=repaired, notes=notes + [NOTE_NO_CRS])

    plan = crs_service.plan(geometry, source)
    geodesic = _geodesic(plan.lonlat, measurement_type)
    unit = "m2" if measurement_type == "area" else "m"

    if plan.projected is None:
        return Measurement(
            measurement_type, "geodesic", geodesic, unit, geodesic,
            repaired=repaired, notes=notes + [NOTE_GEODESIC],
        )
    projected = plan.projected.area if measurement_type == "area" else plan.projected.length
    return Measurement(
        measurement_type, "projected", projected, unit, geodesic,
        crs_service.crs_label(plan.measurement_crs), repaired, notes,
    )


def _polygonal_parts(geometry: BaseGeometry) -> BaseGeometry:
    """Keep only the polygons from make_valid's output, which may also hold lines."""
    polygons = [
        part for part in shapely.get_parts(geometry) if part.geom_type in POLYGONAL
    ]
    parts = [p for poly in polygons for p in shapely.get_parts(poly)]
    if not parts:
        return Polygon()
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _has_no_area(geometry: BaseGeometry) -> bool:
    """True for an empty or collinear polygon, judged in the source CRS.

    A ratio against the bounding box, so it is unitless and never a measurement in
    degrees. An exact == 0 test fails twice over: floating point leaves a collinear
    polygon a tiny non-zero area, and a line that is straight in lon/lat is curved in
    UTM and on the ellipsoid, so a 3 km collinear sliver near Jaipur came out as
    139 m2 projected and 137 m2 geodesic. A real thin feature, such as a 5 m wide road
    strip 10 km long on a diagonal, has a ratio near 0.001, far above the threshold.
    """
    if geometry.is_empty:
        return True
    return geometry.area <= 1e-9 * geometry.envelope.area


def _has_nonzero_z(geometry: BaseGeometry) -> bool:
    # GDAL keeps Z as written, and Google Earth writes 0 on every coordinate, so only a
    # non-zero Z is worth a note (§6 step 7).
    if not geometry.has_z:
        return False
    return bool((shapely.get_coordinates(geometry, include_z=True)[:, 2] != 0).any())


def _geodesic(lonlat: BaseGeometry, measurement_type: str) -> float:
    if measurement_type == "length":
        return GEOD.geometry_length(lonlat)
    # pyproj's area is signed by ring orientation, and it adds holes that wind the same
    # way as their exterior instead of subtracting them. abs() would only fix the overall
    # sign: a MultiPolygon with one clockwise and one counter-clockwise part measured 0.
    # Orienting every ring (exteriors counter-clockwise, holes clockwise) fixes all cases.
    area, _perimeter = GEOD.geometry_area_perimeter(shapely.orient_polygons(lonlat))
    return area
