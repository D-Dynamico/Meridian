"""services/crs (docs/TEST_PLAN.md §16, docs/CRS.md §9)."""

import pytest
import shapely
from pyproj import CRS
from shapely.geometry import Point

from app.services import crs as crs_service
from app.services.crs import WGS84, crs_label, plan, resolve_layer_crs, transformer, utm_epsg
from tests.builders import ground_square

JAIPUR = (75.79, 26.91)
BENGALURU = (77.59, 12.97)


# Zone selection


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (*BENGALURU, 32643),
        (*JAIPUR, 32643),
        (28.04, -26.20, 32735),  # Johannesburg: southern hemisphere, 327xx
        (151.21, -33.87, 32756),  # Sydney
        (-0.13, 51.51, 32630),  # London, just west of Greenwich
        (75.0, 0.0, 32643),  # latitude 0 counts as north
        (78.0, 20.0, 32644),  # on the 43/44 boundary: the higher zone
        (180.0, 10.0, 32660),  # would be zone 61 without the clamp
        (-180.0, 10.0, 32601),
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert utm_epsg(lon, lat) == expected


def test_hemisphere_is_visible_only_in_the_code():
    # The same square at 56N and 56S has the same projected/geodesic gap, so an area
    # assertion cannot see a hemisphere bug (docs/CRS.md §9.3). The code can.
    assert utm_epsg(10.0, 56.0) == 32632
    assert utm_epsg(10.0, -56.0) == 32732


@pytest.mark.parametrize(("lat", "has_zone"), [(84.0, True), (84.01, False), (-80.0, True), (-80.01, False)])
def test_no_zone_beyond_utm_latitude_limits(lat, has_zone):
    assert (utm_epsg(10.0, lat) is not None) == has_zone


# Plans


def test_geographic_source_goes_to_the_centroid_zone():
    result = plan(ground_square(*BENGALURU), WGS84)
    assert crs_label(result.measurement_crs) == "EPSG:32643"
    # Coordinates are now metres, not degrees.
    assert result.projected.bounds[0] > 1000


def test_southern_hemisphere_source_uses_a_327xx_zone():
    result = plan(ground_square(28.04, -26.20), WGS84)
    assert crs_label(result.measurement_crs) == "EPSG:32735"


def test_utm_source_is_measured_in_place():
    utm = CRS.from_epsg(32643)
    square = crs_service._transform(ground_square(*JAIPUR), WGS84, utm)
    result = plan(square, utm)
    assert result.projected is square  # no transform at all
    assert result.measurement_crs == utm
    # The geodesic path still gets lon/lat.
    assert result.lonlat.bounds[0] == pytest.approx(JAIPUR[0], abs=1e-6)


@pytest.mark.parametrize("code", ["EPSG:3857", "EPSG:24378", "EPSG:2263"])
def test_non_utm_projected_source_is_sent_to_utm(code):
    # Web Mercator is projected and in metres, Kalianpur India zone I is a Lambert
    # projection in metres, and New York state plane is in US survey feet. None is UTM,
    # so all are transformed (docs/DECISIONS.md D15).
    source = CRS(code)
    lon, lat = (-73.99, 40.75) if code == "EPSG:2263" else JAIPUR
    square = crs_service._transform(ground_square(lon, lat), WGS84, source)
    expected_zone = 32618 if code == "EPSG:2263" else 32643
    assert crs_label(plan(square, source).measurement_crs) == f"EPSG:{expected_zone}"


def test_polar_feature_has_no_projection():
    result = plan(ground_square(10.0, 85.0), WGS84)
    assert result.projected is None
    assert result.measurement_crs is None


def test_transformers_are_cached_per_pair():
    utm = CRS.from_epsg(32643)
    assert transformer(WGS84, utm) is transformer(WGS84, CRS.from_epsg(32643))
    assert transformer(WGS84, utm) is not transformer(WGS84, CRS.from_epsg(32644))


def test_transformers_use_lon_lat_axis_order():
    # EPSG:4326 is officially lat, lon. Without always_xy=True this would land in the sea.
    x, y = transformer(WGS84, CRS.from_epsg(32643)).transform(*JAIPUR)
    assert 400_000 < x < 600_000  # near the zone's 500 km false easting
    assert 2_900_000 < y < 3_100_000  # about 27 degrees north


# Missing .prj


def test_declared_crs_is_used_as_is():
    declared = CRS.from_epsg(32643)
    assert resolve_layer_crs(declared, [Point(500_000, 3_000_000)]) == crs_service.LayerCrs(declared, False)


def test_missing_crs_with_lon_lat_coordinates_is_assumed_4326_and_flagged():
    result = resolve_layer_crs(None, [ground_square(*JAIPUR), None, Point(-179.9, -89.9)])
    assert result.crs == WGS84
    assert result.assumed is True


def test_missing_crs_with_projected_looking_coordinates_is_not_guessed():
    result = resolve_layer_crs(None, [Point(500_000, 3_000_000)])
    assert result.crs is None
    assert result.assumed is False


def test_missing_crs_with_no_coordinates_is_not_guessed():
    assert resolve_layer_crs(None, [None, shapely.Point()]).crs is None


# Labels


def test_crs_label():
    assert crs_label(CRS.from_epsg(32643)) == "EPSG:32643"
    assert crs_label(None) is None
    custom = CRS.from_proj4("+proj=tmerc +lon_0=77 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m")
    assert crs_label(custom) == custom.name
