# Test Plan

§15 to §17. Tests are not required by the brief, which is exactly why a focused suite makes
the submission stand out. The suite runs fully offline. Stable section numbers, see
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md).

## §15 Test fixtures

Prefer fixtures generated in test setup with GeoPandas over committed binary files, so every
expected value is known and the repo stays small. Commit only what cannot be generated easily.

**Accuracy fixtures are built with `pyproj.Geod.fwd`**, which walks true ground distances on
the WGS84 ellipsoid and returns lon/lat. Never build them as a square in UTM coordinates:
measured back in the same zone, it always comes out at exactly 1,000,000 m2, so the test
passes whether or not the CRS handling is right (`CRS.md` §9.5).

| Fixture | Expected |
|---|---|
| 1 km by 1 km square near Jaipur in EPSG:4326, built with `Geod.fwd` | Area about 1,000,000 m2; projected against geodesic gap about -0.064% |
| Same square near Bengaluru | Gap about +0.116%, inside the 0.25% tolerance |
| Same square at a zone edge on the equator | Gap about +0.195%, the worst case inside a zone |
| Straight 1 km line running due north, built with `Geod.fwd` | Length about 1,000 m |
| Square already in EPSG:32643 | Measured with no transform |
| Square in EPSG:3857 | Transformed to UTM, not measured in place |
| Clockwise polygon (Shapefile ring order) | Positive geodesic area |
| MultiPolygon with one clockwise and one counter-clockwise part | Area is the sum of the parts, not about 0 |
| Polygon whose hole is wound like its exterior | Hole subtracted |
| Valid collinear polygon | Null measurement, "degenerate polygon" |
| Line rising 1000 m over 1 km | 1000 m, not about 1414 m |
| KML with two folders: one polygon layer, one line-and-point layer | Features from both layers, layer names kept |
| KML feature whose Z values are all 0 | No "Z ignored" note |
| Shapefile zip with `.prj` | CRS read correctly |
| Shapefile zip without `.prj` | `crs` is `EPSG:4326`, `crs_assumed: true` |
| Zip with two shapefiles in different CRSs | Per-feature `source_crs`, file `crs` is `MIXED` |
| Zip made on macOS (`__MACOSX/._parcels.shp` entries) | Metadata ignored, real shapefile read |
| Zip with upper-case extensions (`.SHP`, `.DBF`) | Read normally |
| Zip missing `.dbf` | File `FAILED`, reason names `.dbf` |
| Self-intersecting "bowtie" polygon | Repaired, positive area, `repaired: true` |
| Polygon that doubles back on itself | `make_valid` returns lines; null measurement, note "degenerate polygon" |
| Feature with a GeometryCollection | Null measurement, note, no exception |
| Empty geometry | Null measurement, note |
| Polygon beyond 84°N | Geodesic value, null `measurement_crs`, note |
| Corrupt zip (random bytes) | File `FAILED` |
| Zip with an entry path containing `../`, an absolute path or a drive letter | Rejected, nothing written outside the temp folder |
| Zip whose declared sizes exceed the extraction limit | Refused before extraction |
| Zip entry with a bad CRC | File `FAILED` as corrupt |
| Zip with the encrypted flag set | File `FAILED`, "not supported" |
| Two shapefiles with the same name in different folders | Layer names are their paths in the zip |
| Shapefile in a subfolder of the zip | Found and read |
| KML with nested folders | Each folder is its own layer |
| KML with no placemarks | No layers, no error |
| Malformed KML | `FAILED`; message has no server path |
| Polygon in the southern hemisphere | Uses an EPSG 327xx zone |

## §16 Per-module test requirements

**services/crs**
- Bengaluru centroid selects EPSG:32643.
- A southern-hemisphere centroid selects a 327xx code. **Assert the code, not the area:**
  the same square at 56°N and 56°S measures identically, so an area check cannot see a
  hemisphere bug (`CRS.md` §9.3).
- UTM input returns its own CRS and skips transforming.
- EPSG:3857 input is sent to a UTM zone, not measured in place.
- Longitude 180 selects zone 60, not 61.
- Latitude beyond 84°N or 80°S returns no zone.
- Transformer cache returns the same object for the same EPSG code.
- Missing CRS with valid lon/lat ranges infers EPSG:4326 and flags it; out-of-range
  coordinates skip measurement.

**services/measure**
- Square area within 0.5 percent of 1,000,000 m2.
- Line length within 0.5 percent of 1,000 m.
- Projected and geodesic values agree within 0.25 percent for area and 0.15 percent for
  length, at Jaipur, Bengaluru, the central meridian and the zone edge on the equator,
  and Johannesburg.
- The area gap matches the UTM scale-factor formula within 0.005 percent. The 0.25
  percent band alone would pass a neighbouring zone.
- Geodesic area of a clockwise polygon is positive. A mixed-orientation MultiPolygon sums
  its parts, and a same-wound hole is subtracted (`abs()` alone fails both).
- Geodesic value for a projected source is computed after transforming to lon/lat (compare
  it with the geodesic value of the same feature given in EPSG:4326).
- MultiPolygon area equals the sum of its parts.
- Point returns null with a note.
- GeometryCollection and empty geometry return null with a note and never raise.
- Bowtie polygon is repaired, only polygon parts are measured, and it is flagged.
- A polygon that repairs to lines, and a valid collinear polygon, both return null with
  note "degenerate polygon", never a length.
- A rising line measures its horizontal length; Z is noted only when non-zero.
- Beyond 84°N returns the geodesic value with a note, `measurement_method` `geodesic`,
  `geodesic_value` filled and null `measurement_crs`.
- Every measured feature has a non-null `measurement_method` and `geodesic_value`.
- Non-zero Z is ignored with a note; all-zero Z adds no note.
- Several notes on one feature are joined with `"; "` in the fixed order.

**services/loader**
- Multi-layer KML returns every layer.
- KML display fields are dropped, unused standard fields are dropped, ExtendedData and
  used standard fields are kept.
- Missing required shapefile parts are named in the error, every missing part listed.
- Zip-slip entries are rejected. Backslash traversal is tested on `_safe_members`
  directly, because zipfile converts backslashes to "/" on Windows before the loader
  sees them, so a zip-based test can only exercise that path on Linux.
- Zips over the extraction limit, with bad CRCs or encrypted entries fail cleanly.
- Error messages never contain the server's temp paths.
- Corrupt zip fails cleanly.
- macOS metadata entries are ignored; upper-case extensions are accepted.
- Shapefiles with different CRSs keep their own CRS per layer.
- Temp folder is removed after success and after failure.

**services/processor**
- A single feature that raises is recorded with a note and the rest still complete.
- Status moves `PENDING` to `PROCESSING` to `COMPLETED`.
- File-level failure sets `FAILED` with an error message.
- Files left `PENDING` or `PROCESSING` are marked `FAILED` by the startup recovery.

**services/properties** (Stage 2)
- Every missing or non-finite form (None, NaN, infinity, `pd.NA`, `pd.NaT`, NaT
  datetime64) becomes None.
- numpy scalars become Python values of the matching type, including `numpy.float64` and
  `numpy.str_`, which subclass `float` and `str` and would otherwise slip through.
- Dates become ISO strings; shapefile date strings pass through unparsed.
- A shapefile with a null string, a null number, a date and an int column loads as plain
  Python and passes `json.dumps(allow_nan=False)`. Plain `json.dumps` would write NaN and
  pass, so the strict form is required.

**api**
- API tests override `get_processor` (D28): a no-op for upload and status tests, the real
  processor run synchronously for end-to-end tests.
- The same null-string, null-number, date and int shapefile round-trips through the
  measurements endpoint with 200 and JSON nulls, not NaN, **and** the stored properties
  pass SQLite's `json_valid`. The response alone cannot catch a NaN: Pydantic serializes
  it as null through a `response_model`, so with `to_json_safe` removed the response
  check stays green and only the stored-text check goes red.
- Upload KML returns 202, then the file reaches `COMPLETED` with the correct
  `feature_count`.
- Upload Shapefile zip, same check.
- Wrong extension returns 415; oversize returns 413 and saves nothing. Cover each layer
  separately: a declared `Content-Length` (tested on the middleware alone, with a
  `receive` that fails if called), a chunked body with no `Content-Length`, and a file
  just over the limit but inside the multipart allowance. Middleware tests use an
  unsupported extension so that a 413 cannot come from the route instead (the route
  would answer 415).
- Client paths in the filename are reduced to the base name. Test relative and POSIX
  paths; python-multipart already strips drive-letter paths before the route sees them.
- Slashless paths are served directly, with no 307 redirect.
- Unknown id returns 404 on both GET endpoints.
- Measurements before completion return 409; for a `FAILED` file the 409 detail carries
  the failure reason.
- `geometry_type` filter returns only matching features.
- `include_geometry=false` omits geometry.
- Pagination returns the right slice and total count.
- Every result carries every field, with null where there is no value.

**architecture guard**
- Importing `app.api.files` does not import GeoPandas, Shapely or pyproj. Check this in a
  clean subprocess, because an in-process module check depends on test order and can
  silently stop enforcing anything.

## §17 Mutation checks

A mutation check breaks one guard on purpose and confirms that a test fails. A mutation
that leaves the suite green means a test is missing, or that the code it targets is dead.

They are automated in `scripts/mutation_check.py`, with every mutation listed in
`scripts/mutations.json`. Each entry is an exact text replacement in one file; the script
applies it, runs the tests, and restores the file in a `finally` block.

```
python scripts/mutation_check.py            # every mutation, about 10 minutes
python scripts/mutation_check.py --check    # only confirm every pattern still matches
python scripts/mutation_check.py zip-slip   # mutations whose name contains "zip-slip"
```

Run `--check` after any refactor. Patterns are exact source text, so a refactor can turn
a mutation stale, and `--check` names it instead of letting it be skipped. (Stage 5 found
five stale patterns this way.)

The mutations below are the documented core. For each, `mutations.json` names the tests
in the right-hand column under `expect`, and those exact tests must fail, not just any
test. The other entries in the file, from every stage, only need some test to fail.

| Mutation | Must be caught by |
|---|---|
| Skip the CRS transform and measure in source coordinates | Square area test |
| Swap lon and lat (drop `always_xy`) | Axis order test |
| Always use zone 43 regardless of location | Southern-hemisphere EPSG code test |
| Always use the northern hemisphere (326xx) | Hemisphere code test (an area assertion would stay green) |
| Read only the first KML layer | Multi-layer KML test |
| Remove the per-feature error boundary | Processor single-failure test |
| Remove `make_valid` | Bowtie test |
| Measure whatever `make_valid` returns, including lines | Repairs-to-lines test |
| Drop ring orientation on the geodesic area | Clockwise polygon test |
| `abs()` instead of ring orientation | Mixed-orientation MultiPolygon test and same-wound hole test |
| Exact `area == 0` instead of the ratio test | Valid collinear polygon test |
| Trust any projected CRS in metres | Web Mercator test |
| Drop the zip-slip check | Zip-slip test |
| Remove `to_json_safe` (pass raw pandas values) | Strict-serialization loader test and the stored-text `json_valid` check in the measurements round trip |
| Route imports the processor directly | Architecture guard |
| Silently assume 4326 without setting the flag | Missing-CRS test in the CRS service and missing `.prj` test in the processor |
| Return the projected value as the geodesic value | Geodesic-independence test |
