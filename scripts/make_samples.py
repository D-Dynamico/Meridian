"""Write the reviewer sample files in samples/ (docs/WORKFLOWS.md §11).

Usage: python scripts/make_samples.py

The samples are committed so a reviewer can upload something at once, and generated here
so every expected value is known. Shapes are built on the ground with Geod.fwd, like the
accuracy fixtures (docs/CRS.md §9.5), so the areas are true ground areas, not shapes drawn
in the projection they are measured in.

- survey.kml: near Jaipur, three folders. A 1 km square, a self-intersecting plot that
  gets repaired, a 1 km road, a gate point and a mixed MultiGeometry that cannot be
  measured. Exercises the geographic path: EPSG:4326 transformed to UTM zone 43N.
- parcels.zip: three parcels near Bengaluru of 1, 3 and 5 hectares, stored in
  EPSG:32643 (UTM zone 43N) with a .prj. Exercises the measure-in-place path.
"""

import sys
import tempfile
import zipfile
from pathlib import Path
from xml.dom import minidom

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point, Polygon  # noqa: E402

from tests.builders import (  # noqa: E402
    GEOD,
    folder,
    ground_line,
    ground_rectangle,
    ground_square,
    kml,
    kml_line,
    kml_point,
    kml_polygon,
    placemark,
)

SAMPLES = REPO / "samples"
JAIPUR = (75.79, 26.91)
BENGALURU = (77.59, 12.97)
# Fixed zip timestamps, so regenerating unchanged samples gives the same zip entries.
ZIP_DATE = (2026, 10, 7, 0, 0, 0)


def bowtie(lon: float, lat: float, side: float = 500.0) -> Polygon:
    """A ground square with two corners swapped, so its outline crosses itself. A common
    digitising slip; make_valid splits it into two triangles of a quarter square each."""
    sw, se, ne, nw = ground_square(lon, lat, side).exterior.coords[:4]
    return Polygon([sw, ne, se, nw])


def survey_kml() -> bytes:
    """Indented, so a reviewer can read the file before uploading it."""
    east_lon, _, _ = GEOD.fwd(*JAIPUR, 90, 2000)
    gate_lon, gate_lat, _ = GEOD.fwd(*JAIPUR, 45, 50)
    marker = ground_line(*JAIPUR, length=300, azimuth=90)
    document = kml(
        folder(
            "Parcels",
            placemark(
                "Plot 12",
                kml_polygon(ground_square(*JAIPUR)),
                {"owner": "Asha Verma", "survey_no": "112/3"},
            ),
            placemark(
                "Plot 14",
                kml_polygon(bowtie(east_lon, JAIPUR[1])),
                {"owner": "R. Meena", "survey_no": "114/1"},
            ),
        )
        + folder(
            "Roads",
            placemark("Access road", kml_line(ground_line(*JAIPUR)), {"surface": "gravel"}),
            placemark("Site gate", kml_point(Point(gate_lon, gate_lat))),
        )
        + folder(
            "Reference",
            placemark(
                "Benchmark and baseline",
                "<MultiGeometry>" + kml_point(marker.interpolate(0)) + kml_line(marker) + "</MultiGeometry>",
            ),
        )
    )
    return minidom.parseString(document).toprettyxml(indent="  ", encoding="UTF-8")


def parcels_zip() -> bytes:
    lon, lat = BENGALURU
    # Parcels side by side along an east-west line, 20 m apart.
    shapes, sizes = [], [(100, 100), (200, 150), (250, 200)]
    for width, height in sizes:
        shapes.append(ground_rectangle(lon, lat, width, height))
        lon, _, _ = GEOD.fwd(lon, lat, 90, width + 20)
    frame = gpd.GeoDataFrame(
        {
            "survey_no": ["41/1", "41/2", "42"],
            "village": ["Hebbal"] * 3,
            "land_use": ["residential", "orchard", "fallow"],
        },
        geometry=shapes,
        crs="EPSG:4326",
    ).to_crs("EPSG:32643")
    with tempfile.TemporaryDirectory() as tmp:
        frame.to_file(Path(tmp) / "parcels.shp")
        parts = sorted(Path(tmp).iterdir())
        with tempfile.TemporaryFile() as buffer:
            with zipfile.ZipFile(buffer, "w") as archive:
                for part in parts:
                    info = zipfile.ZipInfo(part.name, ZIP_DATE)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, part.read_bytes())
            buffer.seek(0)
            return buffer.read()


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "survey.kml").write_bytes(survey_kml())
    (SAMPLES / "parcels.zip").write_bytes(parcels_zip())
    for path in sorted(SAMPLES.iterdir()):
        print(f"{path.relative_to(REPO)}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
