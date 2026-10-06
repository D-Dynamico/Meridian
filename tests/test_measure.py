"""services/measure (docs/TEST_PLAN.md §16, docs/ARCHITECTURE.md §6, docs/CRS.md §9.5)."""

import pytest
import shapely
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.services import crs as crs_service
from app.services.crs import WGS84
from app.services.measure import measure
from tests.builders import GEOD, ground_line, ground_square, utm_area_gap

JAIPUR = (75.79, 26.91)
BENGALURU = (77.59, 12.97)

# (name, lon, lat, zone) for squares spread across zone 43 and one southern zone.
LOCATIONS = [
    ("Jaipur", *JAIPUR, 43),
    ("Bengaluru", *BENGALURU, 43),
    ("central meridian, equator", 74.995, 0.0, 43),
    ("zone edge, equator", 77.985, 0.0, 43),
    ("Johannesburg", 28.04, -26.20, 35),
]


def reversed_ring(polygon: Polygon) -> Polygon:
    return Polygon(list(polygon.exterior.coords)[::-1])


def gap(result) -> float:
    return result.value / result.geodesic_value - 1


# Accuracy (Stage 3 exit criteria)


def test_one_km_square_measures_one_million_m2():
    result = measure(ground_square(*JAIPUR), WGS84, crs_assumed=False)
    assert result.measurement_type == "area"
    assert result.unit == "m2"
    assert result.value == pytest.approx(1_000_000, rel=0.005)
    assert result.measurement_crs == "EPSG:32643"


def test_one_km_line_measures_one_thousand_m():
    result = measure(ground_line(*JAIPUR), WGS84, crs_assumed=False)
    assert result.measurement_type == "length"
    assert result.unit == "m"
    assert result.value == pytest.approx(1000, rel=0.005)


@pytest.mark.parametrize(("name", "lon", "lat", "zone"), LOCATIONS, ids=[l[0] for l in LOCATIONS])
def test_projected_and_geodesic_area_agree_within_tolerance(name, lon, lat, zone):
    result = measure(ground_square(lon, lat), WGS84, crs_assumed=False)
    assert abs(gap(result)) < 0.0025  # docs/CRS.md §9.5


@pytest.mark.parametrize(("name", "lon", "lat", "zone"), LOCATIONS, ids=[l[0] for l in LOCATIONS])
def test_area_gap_matches_the_utm_scale_factor_of_the_right_zone(name, lon, lat, zone):
    """The 0.25 percent band would also pass a neighbouring zone. The scale-factor formula
    is independent of the code and predicts the gap to about 0.002 percent, so matching it
    within 0.005 percent proves the feature was projected in its own zone."""
    result = measure(ground_square(lon, lat), WGS84, crs_assumed=False)
    centroid = ground_square(lon, lat).centroid
    assert gap(result) == pytest.approx(utm_area_gap(centroid.x, centroid.y, zone), abs=0.00005)


@pytest.mark.parametrize("azimuth", [0, 90, 45])
@pytest.mark.parametrize(("name", "lon", "lat", "zone"), LOCATIONS, ids=[l[0] for l in LOCATIONS])
def test_projected_and_geodesic_length_agree_within_tolerance(name, lon, lat, zone, azimuth):
    result = measure(ground_line(lon, lat, azimuth=azimuth), WGS84, crs_assumed=False)
    assert abs(gap(result)) < 0.0015


def test_geodesic_value_is_independent_of_the_projection():
    square = ground_square(*JAIPUR)
    result = measure(square, WGS84, crs_assumed=False)
    expected, _ = GEOD.geometry_area_perimeter(square)
    assert result.geodesic_value == pytest.approx(abs(expected), rel=1e-9)
    assert result.geodesic_value != result.value


def test_southern_hemisphere_feature_reports_a_327xx_code():
    result = measure(ground_square(28.04, -26.20), WGS84, crs_assumed=False)
    assert result.measurement_crs == "EPSG:32735"


# Source CRS handling


def test_utm_source_is_measured_without_transforming():
    utm = CRS.from_epsg(32643)
    square = crs_service._transform(ground_square(*JAIPUR), WGS84, utm)
    result = measure(square, utm, crs_assumed=False)
    assert result.value == square.area  # exactly the planar area in the source
    assert result.measurement_crs == "EPSG:32643"


def test_web_mercator_source_is_not_measured_in_place():
    mercator = CRS.from_epsg(3857)
    square = crs_service._transform(ground_square(*JAIPUR), WGS84, mercator)
    # The trap: measured in place, Web Mercator overstates this square by about 26
    # percent at 27 degrees north.
    assert square.area / 1_000_000 - 1 > 0.2

    result = measure(square, mercator, crs_assumed=False)
    assert result.measurement_crs == "EPSG:32643"
    assert abs(gap(result)) < 0.0025


def test_geodesic_value_of_a_projected_source_comes_from_lon_lat():
    utm = CRS.from_epsg(32643)
    square = ground_square(*JAIPUR)
    from_utm = measure(crs_service._transform(square, WGS84, utm), utm, crs_assumed=False)
    from_lonlat = measure(square, WGS84, crs_assumed=False)
    # Fed raw UTM metres, Geod would report an absurd area. Equal values prove the
    # geodesic path converted to lon/lat first.
    assert from_utm.geodesic_value == pytest.approx(from_lonlat.geodesic_value, rel=1e-6)


# Polygon orientation and composition


def test_clockwise_polygon_has_positive_geodesic_area():
    square = ground_square(*JAIPUR)
    clockwise = reversed_ring(square)
    assert not clockwise.exterior.is_ccw
    assert measure(clockwise, WGS84, False).geodesic_value == pytest.approx(
        measure(square, WGS84, False).geodesic_value
    )


def test_multipolygon_with_mixed_orientation_sums_its_parts():
    # One counter-clockwise and one clockwise part of equal size: pyproj's signed sum is
    # about 0, and abs() cannot recover it. Orienting the rings can.
    a = ground_square(*JAIPUR)
    b = reversed_ring(ground_square(JAIPUR[0] + 0.05, JAIPUR[1]))
    both = measure(MultiPolygon([a, b]), WGS84, False)
    alone = measure(a, WGS84, False).geodesic_value + measure(b, WGS84, False).geodesic_value
    assert both.geodesic_value == pytest.approx(alone, rel=1e-9)
    assert both.value == pytest.approx(
        measure(a, WGS84, False).value + measure(b, WGS84, False).value, rel=1e-9
    )


def test_hole_wound_like_its_exterior_is_still_subtracted():
    outer = ground_square(*JAIPUR)
    hole = ground_square(JAIPUR[0] + 0.002, JAIPUR[1] + 0.002, side=200)
    assert hole.exterior.is_ccw == outer.exterior.is_ccw  # same winding: the trap
    result = measure(Polygon(outer.exterior.coords, [hole.exterior.coords]), WGS84, False)
    assert result.geodesic_value == pytest.approx(1_000_000 - 40_000, rel=0.001)
    assert abs(gap(result)) < 0.0025


# Points, unsupported and empty input: notes, never exceptions


@pytest.mark.parametrize("geometry", [Point(*JAIPUR), MultiPoint([JAIPUR, BENGALURU])])
def test_points_have_no_measurement(geometry):
    result = measure(geometry, WGS84, False)
    assert result.measurement_type == "none"
    assert result.value is None
    assert result.method is None
    assert result.note == "No measurement for point geometries"


def test_geometry_collection_is_unsupported():
    mixed = GeometryCollection([Point(*JAIPUR), ground_line(*JAIPUR)])
    result = measure(mixed, WGS84, False)
    assert result.value is None
    assert result.note == "Unsupported geometry type: GeometryCollection"


@pytest.mark.parametrize("geometry", [None, Polygon(), LineString(), GeometryCollection()])
def test_empty_geometry_has_no_measurement(geometry):
    result = measure(geometry, WGS84, False)
    assert result.value is None
    assert result.note == "Empty geometry"


def test_no_crs_skips_measurement_with_a_reason():
    result = measure(Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)]), None, False)
    assert result.value is None
    assert "units are unknown" in result.note


# Repair


def test_bowtie_is_repaired_and_both_halves_measured():
    lon, lat = JAIPUR
    bowtie = Polygon([(lon, lat), (lon + 0.01, lat + 0.01), (lon + 0.01, lat), (lon, lat + 0.01)])
    assert not bowtie.is_valid
    result = measure(bowtie, WGS84, False)
    assert result.repaired is True
    assert result.note == "Repaired invalid geometry"
    # The two triangles together cover half of the 0.01 degree box.
    box = measure(shapely.box(lon, lat, lon + 0.01, lat + 0.01), WGS84, False)
    assert result.value == pytest.approx(box.value / 2, rel=0.001)


def test_repair_keeps_only_the_polygon_parts():
    lon, lat = JAIPUR
    # A square with a spike: make_valid returns a GeometryCollection of the square and
    # a line. The line has no area and must not change the result.
    spike = Polygon([
        (lon, lat), (lon + 0.01, lat), (lon + 0.01, lat + 0.01), (lon, lat + 0.01),
        (lon, lat), (lon - 0.01, lat - 0.01), (lon, lat),
    ])
    assert shapely.make_valid(spike).geom_type == "GeometryCollection"
    result = measure(spike, WGS84, False)
    square = measure(shapely.box(lon, lat, lon + 0.01, lat + 0.01), WGS84, False)
    assert result.repaired is True
    assert result.value == pytest.approx(square.value, rel=1e-9)


def test_polygon_that_repairs_to_lines_is_degenerate_not_a_length():
    lon, lat = JAIPUR
    there_and_back = Polygon([(lon, lat), (lon + 0.01, lat + 0.01), (lon + 0.02, lat), (lon + 0.01, lat + 0.01), (lon, lat)])
    assert not there_and_back.is_valid
    assert shapely.make_valid(there_and_back).geom_type in ("LineString", "MultiLineString")
    result = measure(there_and_back, WGS84, False)
    assert result.measurement_type == "none"
    assert result.value is None
    assert result.repaired is True
    assert result.note == "Repaired invalid geometry; degenerate polygon"


def test_valid_zero_area_polygon_is_degenerate():
    lon, lat = JAIPUR
    collinear = Polygon([(lon, lat), (lon + 0.01, lat + 0.01), (lon + 0.02, lat + 0.02)])
    assert collinear.is_valid  # shapely accepts it, so make_valid never runs
    result = measure(collinear, WGS84, False)
    assert result.value is None
    assert result.repaired is False
    assert result.note == "Degenerate polygon"


# Polar fallback


def test_beyond_84_north_uses_the_geodesic_value():
    result = measure(ground_square(10.0, 85.0), WGS84, False)
    assert result.method == "geodesic"
    assert result.value == result.geodesic_value
    assert result.value == pytest.approx(1_000_000, rel=0.005)
    assert result.measurement_crs is None
    assert result.note == "Outside UTM coverage, geodesic value used"


@pytest.mark.parametrize("geometry", [ground_square(*JAIPUR), ground_line(*JAIPUR), ground_square(10.0, 85.0)])
def test_every_measured_feature_has_a_method_and_a_geodesic_value(geometry):
    result = measure(geometry, WGS84, False)
    assert result.method in ("projected", "geodesic")
    assert result.geodesic_value is not None


# Z and notes


def with_z(geometry, z):
    return shapely.force_3d(geometry, z)


def test_non_zero_z_is_ignored_with_a_note():
    flat = measure(ground_square(*JAIPUR), WGS84, False)
    raised = measure(with_z(ground_square(*JAIPUR), 350.0), WGS84, False)
    assert raised.value == pytest.approx(flat.value, rel=1e-12)
    assert raised.note == "Z values ignored"


@pytest.mark.parametrize("source", ["EPSG:4326", "EPSG:32643"])
def test_rising_line_measures_its_horizontal_length(source):
    # 1 km on the ground, climbing 1000 m. Counting Z would give about 1414 m.
    line = ground_line(*JAIPUR)
    crs = CRS(source)
    if source != "EPSG:4326":
        line = crs_service._transform(line, WGS84, crs)  # exercises the measure-in-place path
    (x0, y0), (x1, y1) = line.coords
    rising = LineString([(x0, y0, 0.0), (x1, y1, 1000.0)])
    result = measure(rising, crs, False)
    assert result.value == pytest.approx(1000, rel=0.005)
    assert result.geodesic_value == pytest.approx(1000, rel=1e-6)
    assert result.note == "Z values ignored"


def test_zero_z_adds_no_note():
    # Google Earth writes ",0" on every coordinate.
    assert measure(with_z(ground_square(*JAIPUR), 0.0), WGS84, False).note is None


def test_notes_are_joined_in_a_fixed_order():
    lon, lat = JAIPUR
    bowtie = Polygon([(lon, lat, 5), (lon + 0.01, lat + 0.01, 5), (lon + 0.01, lat, 5), (lon, lat + 0.01, 5)])
    result = measure(bowtie, WGS84, crs_assumed=True)
    assert result.note == "CRS assumed EPSG:4326 (no .prj); repaired invalid geometry; Z values ignored"


def test_multilinestring_length_is_the_sum_of_its_parts():
    a, b = ground_line(*JAIPUR), ground_line(JAIPUR[0] + 0.05, JAIPUR[1], azimuth=90)
    both = measure(MultiLineString([a, b]), WGS84, False)
    assert both.value == pytest.approx(measure(a, WGS84, False).value + measure(b, WGS84, False).value)
    assert both.geodesic_value == pytest.approx(2000, rel=1e-6)
