"""JSON-safe properties (docs/DECISIONS.md D29).

Every assertion that a value is serializable uses json.dumps(allow_nan=False), the same
strictness Starlette applies when it sends a response. Plain json.dumps would write NaN
and pass, which is exactly the delayed failure the helper exists to prevent.
"""

import datetime
import json
import math
from decimal import Decimal

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from app.services.loader import load
from app.services.properties import to_json_safe
from tests.builders import shapefile_parts, write_zip


def strict_dumps(value) -> str:
    return json.dumps(value, allow_nan=False)


# The helper, one type at a time


@pytest.mark.parametrize(
    "missing",
    [None, float("nan"), float("inf"), -float("inf"), np.float64("nan"), pd.NA, pd.NaT,
     np.datetime64("NaT", "ns")],
    ids=["None", "nan", "inf", "-inf", "numpy-nan", "pd.NA", "pd.NaT", "datetime64-NaT"],
)
def test_missing_and_non_finite_values_become_none(missing):
    assert to_json_safe(missing) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (np.int64(7), 7),
        (np.int32(-3), -3),
        (np.float64(1.5), 1.5),
        (np.bool_(True), True),
        (np.str_("Plot 12"), "Plot 12"),
    ],
)
def test_numpy_scalars_become_python_values(value, expected):
    result = to_json_safe(value)
    assert result == expected
    assert type(result) is type(expected)


def test_plain_values_pass_through():
    for value in ["Plot 12", "", 0, -4, 2.5, True, False]:
        assert to_json_safe(value) == value


def test_shapefile_date_strings_are_not_parsed():
    # Formats vary between writers. They are passed through exactly as read.
    for value in ["2026-01-15", "2026/01/15 00:00:00"]:
        assert to_json_safe(value) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (pd.Timestamp("2026-01-15 10:30"), "2026-01-15T10:30:00"),
        (datetime.datetime(2026, 1, 15, 10, 30), "2026-01-15T10:30:00"),
        (datetime.date(2026, 1, 15), "2026-01-15"),
        (datetime.time(10, 30), "10:30:00"),
        (np.datetime64("2026-01-15T10:30:00.000000000"), "2026-01-15T10:30:00"),
    ],
)
def test_dates_and_times_become_iso_strings(value, expected):
    assert to_json_safe(value) == expected


def test_binary_becomes_a_placeholder():
    assert to_json_safe(b"\x00\x01\x02") == "<binary data, 3 bytes>"


def test_containers_are_converted_recursively():
    value = {"tags": np.array(["a", "b"]), "scores": [np.int64(1), float("nan")], 3: "x"}
    assert to_json_safe(value) == {"tags": ["a", "b"], "scores": [1, None], "3": "x"}


def test_other_types_become_strings_or_floats():
    assert to_json_safe(Decimal("2.50")) == 2.5
    assert to_json_safe(Decimal("NaN")) is None
    assert to_json_safe(Point(1, 2)) == "POINT (1 2)"


# Through the loader, from a real shapefile


def test_shapefile_properties_come_out_plain_and_strictly_serializable(tmp_path):
    frame = gpd.GeoDataFrame(
        {
            "name": ["Plot 12", None],
            "area_ha": [1.5, None],
            "surveyed": [datetime.date(2026, 1, 15), None],
            "plots": [3, 4],
        },
        geometry=[Point(75.8, 26.9), Point(75.81, 26.91)],
        crs="EPSG:4326",
    )
    path = write_zip(tmp_path / "upload.zip", shapefile_parts(frame))
    features = load(path, "SHAPEFILE", tmp_path / "work", 10 * 1024 * 1024, 100).layers[0].features

    complete, empty = (f.properties for f in features)
    assert complete == {"name": "Plot 12", "area_ha": 1.5, "surveyed": "2026-01-15", "plots": 3}
    # pandas reports all three missing values as float NaN; they must arrive as None.
    assert empty == {"name": None, "area_ha": None, "surveyed": None, "plots": 4}
    assert type(complete["plots"]) is int
    assert strict_dumps([complete, empty])


def test_no_property_is_nan(tmp_path):
    """A blunt second check: walks every value instead of trusting the dict comparison,
    since NaN == NaN is False and a NaN could only fail the comparison above indirectly."""
    frame = gpd.GeoDataFrame(
        {"name": [None], "area_ha": [None]}, geometry=[Point(75.8, 26.9)], crs="EPSG:4326"
    )
    path = write_zip(tmp_path / "upload.zip", shapefile_parts(frame))
    properties = load(path, "SHAPEFILE", tmp_path / "work", 10 * 1024 * 1024, 100).layers[0].features[0].properties
    assert not any(isinstance(v, float) and math.isnan(v) for v in properties.values())
