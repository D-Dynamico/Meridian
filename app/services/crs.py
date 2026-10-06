"""Decide how each feature is measured: in which CRS, and from which lon/lat (docs/CRS.md §9).

The rules, in the order they are applied:

1. A layer with no CRS (shapefile without .prj) is assumed EPSG:4326 only if every
   coordinate is a plausible longitude and latitude, and the assumption is flagged.
2. A source in UTM, in metres, is measured in place (§9.2 step 3).
3. Every other source, geographic or projected, is taken to lon/lat and then to the UTM
   zone of the feature's own centroid (§9.2 steps 4 to 6). "Projected and in metres" is
   not enough: Web Mercator is both, and overstates area by 22 percent in India.
4. Beyond 84°N or 80°S there is no UTM zone, and the geodesic value is used (§9.6).

Every transformer is built with always_xy=True, so axis order is always lon, lat, and
cached, because building one costs far more than using it.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)

# UTM is defined only between these latitudes (§9.3).
UTM_MIN_LAT = -80.0
UTM_MAX_LAT = 84.0


@dataclass(frozen=True)
class LayerCrs:
    """The CRS a layer is measured from, and whether it was assumed."""

    crs: CRS | None  # None: no CRS declared and none could be assumed
    assumed: bool


@dataclass(frozen=True)
class Plan:
    """How one feature is measured. Geometries are 2D."""

    lonlat: BaseGeometry  # the feature in EPSG:4326, for the geodesic value
    projected: BaseGeometry | None  # the feature in measurement_crs; None: no UTM zone
    measurement_crs: CRS | None


def resolve_layer_crs(crs: CRS | None, geometries: list[BaseGeometry | None]) -> LayerCrs:
    """Apply the missing-.prj rule (§9.6, docs/DECISIONS.md D10)."""
    if crs is not None:
        return LayerCrs(crs, assumed=False)
    present = [g for g in geometries if g is not None and not g.is_empty]
    if not present:
        return LayerCrs(None, assumed=False)  # nothing to check, nothing to measure
    min_x, min_y, max_x, max_y = shapely.total_bounds(present)
    if -180 <= min_x and max_x <= 180 and -90 <= min_y and max_y <= 90:
        return LayerCrs(WGS84, assumed=True)
    # Coordinates outside lon/lat ranges are in some unknown projected unit. Guessing
    # would produce a plausible-looking wrong number, so nothing is measured.
    return LayerCrs(None, assumed=False)


def utm_epsg(lon: float, lat: float) -> int | None:
    """The WGS84 UTM zone EPSG code for a point, or None beyond UTM's latitude limits.

    Latitude 0 counts as north. A longitude on a zone boundary goes to the higher zone,
    which is what floor does, and longitude 180 is clamped to zone 60 (§9.6). The
    Norway and Svalbard zone exceptions are not applied: the plain zone is still a
    valid projection there, just slightly further from its central meridian.
    """
    if not UTM_MIN_LAT <= lat <= UTM_MAX_LAT:
        return None
    zone = min(int((lon + 180) // 6) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


def is_utm_in_metres(crs: CRS) -> bool:
    return crs.utm_zone is not None and crs.axis_info[0].unit_name in ("metre", "meter")


def plan(geometry: BaseGeometry, source: CRS) -> Plan:
    """Choose the measurement CRS for one 2D, non-empty feature and transform it."""
    lonlat = geometry if source == WGS84 else _transform(geometry, source, WGS84)

    if is_utm_in_metres(source):
        return Plan(lonlat, projected=geometry, measurement_crs=source)

    centroid = lonlat.centroid
    epsg = utm_epsg(centroid.x, centroid.y)
    if epsg is None:
        return Plan(lonlat, projected=None, measurement_crs=None)
    target = CRS.from_epsg(epsg)
    return Plan(lonlat, projected=_transform(lonlat, WGS84, target), measurement_crs=target)


def crs_label(crs: CRS | None) -> str | None:
    """A short label such as "EPSG:32643", or the CRS name when it has no EPSG code."""
    if crs is None:
        return None
    authority = crs.to_authority(min_confidence=70)
    return ":".join(authority) if authority else crs.name


@lru_cache(maxsize=128)
def transformer(source: CRS, target: CRS) -> Transformer:
    """Cached per (source, target) pair. In practice the pairs are "source CRS to
    EPSG:4326" and "EPSG:4326 to a UTM zone", so there is one entry per UTM zone used."""
    return Transformer.from_crs(source, target, always_xy=True)


def _transform(geometry: BaseGeometry, source: CRS, target: CRS) -> BaseGeometry:
    t = transformer(source, target)
    # shapely.transform passes all coordinates as one (n, 2) array, so pyproj does the
    # whole geometry in a single vectorised call.
    return shapely.transform(geometry, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))
