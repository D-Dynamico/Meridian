# Test Plan

§15 to §17. Tests are not required by the brief, which is exactly why a focused suite makes
the submission stand out. The suite runs fully offline. Stable section numbers, see
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md).

## §15 Test fixtures

Prefer fixtures generated in test setup with GeoPandas over committed binary files, so every
expected value is known and the repo stays small. Commit only what cannot be generated easily.

| Fixture | Expected |
|---|---|
| 1 km by 1 km square near Jaipur in EPSG:4326 | Area about 1,000,000 m2 |
| Straight 1 km line running due north | Length about 1,000 m |
| Square already in EPSG:32643 | Measured with no transform |
| KML with two folders: one polygon layer, one line-and-point layer | Features from both layers, layer names kept |
| Shapefile zip with `.prj` | CRS read correctly |
| Shapefile zip without `.prj` | `crs_assumed: true` |
| Zip missing `.dbf` | File `FAILED`, reason names `.dbf` |
| Self-intersecting "bowtie" polygon | Repaired, positive area, `repaired: true` |
| Feature with a GeometryCollection | Null measurement, note, no exception |
| Empty geometry | Null measurement, note |
| Corrupt zip (random bytes) | File `FAILED` |
| Zip with an entry path containing `../` | Rejected, nothing written outside the temp folder |
| Polygon in the southern hemisphere | Uses an EPSG 327xx zone |

## §16 Per-module test requirements

**services/crs**
- Bengaluru centroid selects EPSG:32643.
- A southern-hemisphere centroid selects a 327xx code.
- Projected-in-metres input returns its own CRS and skips transforming.
- Transformer cache returns the same object for the same EPSG code.
- Missing CRS with valid lon/lat ranges infers EPSG:4326 and flags it; out-of-range
  coordinates skip measurement.

**services/measure**
- Square area within 0.5 percent of 1,000,000 m2.
- Line length within 0.5 percent of 1,000 m.
- Projected and geodesic values agree within 0.1 percent for both.
- MultiPolygon area equals the sum of its parts.
- Point returns null with a note.
- GeometryCollection and empty geometry return null with a note and never raise.
- Bowtie polygon is repaired and flagged.
- Z coordinates are ignored with a note.

**services/loader**
- Multi-layer KML returns every layer.
- Missing required shapefile parts are named in the error.
- Zip-slip entries are rejected.
- Corrupt zip fails cleanly.
- Temp folder is removed after success and after failure.

**services/processor**
- A single feature that raises is recorded with a note and the rest still complete.
- Status moves `PENDING` to `PROCESSING` to `COMPLETED`.
- File-level failure sets `FAILED` with an error message.

**api**
- Upload KML returns 202, then the file reaches `COMPLETED` with the correct
  `feature_count`.
- Upload Shapefile zip, same check.
- Wrong extension returns 415; oversize returns 413.
- Unknown id returns 404 on both GET endpoints.
- Measurements before completion return 409.
- `geometry_type` filter returns only matching features.
- `include_geometry=false` omits geometry.
- Pagination returns the right slice and total count.

**architecture guard**
- Importing `app.api.files` does not import GeoPandas, Shapely or pyproj. Check this in a
  clean subprocess, because an in-process module check depends on test order and can
  silently stop enforcing anything.

## §17 Mutation checks

Before Stage 5 exits, apply each mutation by hand, confirm at least one test fails, then
revert. Record the results in the session note. A mutation that leaves the suite green means
a test is missing.

| Mutation | Must be caught by |
|---|---|
| Skip the CRS transform and measure in source coordinates | Square area test |
| Swap lon and lat (drop `always_xy`) | Zone selection test or area tolerance test |
| Always use zone 43 regardless of location | Southern-hemisphere test |
| Read only the first KML layer | Multi-layer KML test |
| Remove the per-feature error boundary | Processor single-failure test |
| Remove `make_valid` | Bowtie test |
| Drop the zip-slip check | Zip-slip test |
| Silently assume 4326 without setting the flag | Missing `.prj` test |
| Return geodesic value equal to projected value | Cross-check agreement is not enough here; add a test that the geodesic path runs on unprojected input |
