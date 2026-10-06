"""Make feature attributes safe to store and serve as JSON (docs/DECISIONS.md D29).

The danger this guards against is a delayed failure. pandas reports a missing value as
float NaN, in string, number and date columns alike. json.dumps writes NaN without
complaint, so the database write succeeds, and the error appears only later as a 500
when Starlette serializes the response with allow_nan=False. Converting here, in the
loader, means everything downstream handles plain Python values only.
"""

import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


def to_json_safe(value: Any) -> Any:
    """Convert one attribute value to something json.dumps(allow_nan=False) accepts."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    # numpy checks come first: numpy.float64 subclasses float and numpy.str_ subclasses
    # str, so the plain-type checks below would pass them through as numpy values.
    if isinstance(value, np.datetime64):
        # .item() on a nanosecond datetime64 returns an int, not a datetime, so go
        # through pandas instead.
        return None if np.isnat(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        # numpy.int64, numpy.float64, numpy.bool_: .item() gives the Python equivalent,
        # which still has to pass the NaN and infinity check below.
        return to_json_safe(value.item())
    # bool is a subclass of int; both pass through unchanged.
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date, time)):
        # pandas Timestamp is a datetime subclass, so it is covered here too. Shapefile
        # dates normally arrive as strings already and are passed through unparsed.
        return value.isoformat()
    if isinstance(value, Decimal):
        return to_json_safe(float(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Binary is kept as a visible placeholder rather than dropped, so the attribute
        # does not silently disappear. Shapefile and KML have no binary field type;
        # this only matters if another format is added.
        return f"<binary data, {len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        # GDAL list fields (for example StringList) arrive as numpy arrays.
        return [to_json_safe(v) for v in value]
    return str(value)


def json_safe_properties(record: dict) -> dict[str, Any]:
    return {str(key): to_json_safe(value) for key, value in record.items()}
