"""Architecture guard: routes never load geospatial libraries (docs/ARCHITECTURE.md §3).

This runs in a clean subprocess. Inside the test process, any other test may already have
imported GeoPandas, so checking sys.modules here would pass or fail depending on test
order, and could silently stop enforcing anything (docs/TEST_PLAN.md §16).
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GEOSPATIAL = ("geopandas", "shapely", "pyproj", "pyogrio", "fiona", "osgeo")

PROBE = f"""
import sys
import app.api.files
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in {GEOSPATIAL!r}
)
print(",".join(loaded))
"""


def test_routes_do_not_import_geospatial_libraries():
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", f"app.api.files loaded: {result.stdout.strip()}"
