# Architecture

Section numbers are stable and cited from code. Do not renumber. See
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for the full index.

## §1 The brief

Aereo's backend take-home: build a Django or FastAPI service that accepts a geospatial file,
processes its features and returns measurement information.

**Required by the brief**

- Accept a `.zip` containing a Shapefile, or a `.kml`.
- For every feature, identify at minimum: feature ID or index, geometry type, geometry, CRS,
  and properties or attributes.
- Polygon: calculate area. LineString: calculate length. Point: no measurement.
- Geometry types not supported for measurement are handled gracefully, never by crashing.
- Files in geographic coordinates (for example EPSG:4326) are transformed to an appropriate
  projected CRS before measuring. The projection strategy is ours to choose.
- Endpoints: `POST /api/files/`, `GET /api/files/{id}/`, `GET /api/files/{id}/measurements/`.
- README with setup, API with examples, architecture (structure, file-processing flow,
  measurement flow, CRS handling), design decisions, learnings and future scope.
- Submitted as a public GitHub repository.

**Why it matters to Aereo:** they run drone surveys for mining, infrastructure and land
records, and their platform includes a web-GIS. Measuring parcels and alignments correctly
from Shapefiles and KML is their everyday work. A reviewer there will check the CRS handling
first.

## §2 Scope and non-goals

**In scope**

- Upload, validation and safe extraction of Shapefile zips and KML files
- Reading every layer and every feature
- Per-feature measurement with a projected value and a geodesic cross-check
- Graceful handling of unsupported, empty and invalid geometries
- Three required endpoints plus a list endpoint and a health check
- Background processing with a status lifecycle
- Test suite, Dockerfile, sample files, complete README

**Non-goals (listed as future scope in the README)**

- Frontend or map rendering
- Authentication and per-user files
- 3D, volume or DEM-based measurement
- Formats beyond Shapefile and KML (GeoJSON, GeoPackage, KMZ)
- Editing, re-projecting for export, or downloading processed files
- Distributed workers (Celery) and PostGIS storage

## §3 Application structure and module map

```
app/
  main.py              Composition root. App factory create_app(settings), router
                       registration, startup (create folders and tables), health check,
                       and from Stage 4 the wiring of the real processor (D28)
  api/files.py         Routes only. No GeoPandas, Shapely or pyproj imports
  api/upload_limit.py  ASGI middleware enforcing the request-body size limit before
                       FastAPI reads the body
  api/deps.py          Request-scoped dependencies: settings, database session and (from
                       Stage 4) get_processor, read from app.state so each test can build
                       its own app
  core/config.py       Upload size limit, allowed extensions, storage paths
  db/models.py         SQLModel tables: File, Feature
  db/session.py        Engine creation (SQLite thread and foreign-key settings), tables
  schemas/files.py     Pydantic response models
  services/
    loader.py          Zip validation and safe extraction, KML reading, layer iteration.
                       Returns layers, each with its CRS and plain Python features
    properties.py      to_json_safe: NaN, numpy and pandas values to plain JSON (D29)
    crs.py             Source CRS detection, UTM selection, transformer cache
    measure.py         Area and length per geometry type, make_valid, notes
    processor.py       Orchestrates loader, crs, measure and database writes
tests/
samples/               Small files a reviewer can upload immediately
docs/
Dockerfile
README.md
CLAUDE.md
```

**Dependency direction:** `api` depends on `schemas` and `db`. It never imports
`processor`: the upload route receives the processing function through
`Depends(get_processor)`, typed as a plain callable taking a file id, and `main.py` (the
composition root) wires the real one (`DECISIONS.md` D28). `processor` depends on the
services and `db`. The services depend on nothing inside `app/` except `core/config` and
each other. Nothing imports `api` except `main.py`.

**Import rule:** geospatial libraries are imported only under `app/services/`. A test
enforces this (`TEST_PLAN.md` §16).

## §4 Data model

Two tables. Geometry is stored as GeoJSON text, properties as JSON.

**File**

| Column | Notes |
|---|---|
| id | UUID, exposed in URLs |
| filename | Original upload name |
| format | `SHAPEFILE` or `KML` |
| crs | Source CRS as a string such as `EPSG:4326`. When layers disagree, `MIXED` (each feature carries its own `source_crs`). When a CRS was inferred, the inferred value, never null. Null only when no CRS could be determined at all |
| crs_assumed | True when any layer declared no CRS and one was inferred |
| feature_count | Total features across all layers |
| status | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| error | Human-readable reason when `FAILED`, else null |
| created_at, completed_at | Timestamps |

**Feature**

| Column | Notes |
|---|---|
| id | Primary key |
| file_id | Foreign key to File |
| feature_index | Position within the file, stable across reads. Exposed as `index` in the API. Named this way for readability, so it is never confused with a DataFrame index (SQLModel quotes `index` correctly, so this is not a bug fix) |
| layer | Layer or folder name (KML) or shapefile name |
| source_crs | CRS of this feature's layer, for example `EPSG:4326`. Needed because a zip can hold several shapefiles with different `.prj` files |
| geometry_type | As read, for example `MultiPolygon` |
| geometry | GeoJSON in the source CRS |
| properties | Attributes as JSON, made JSON-safe |
| measurement_type | `area`, `length` or `none` |
| value | Projected measurement in m2 or m, or null |
| unit | `m2`, `m` or null |
| measurement_method | `projected` or `geodesic`: how `value` was computed. Null only when there is no measurement. Stated explicitly so consumers never infer it from `measurement_crs` |
| geodesic_value | Geodesic value in the same unit. Never null on a measured feature: it is the cross-check for `projected`, and equal to `value` for `geodesic` |
| measurement_crs | Projected CRS used, for example `EPSG:32643`. Null when no projection was used (no measurement, or a geodesic fallback beyond UTM limits) |
| repaired | True if `make_valid` changed the geometry |
| note | Why there is no measurement, or what was assumed, repaired or ignored. Several notes are joined with `"; "` in a fixed order |

Summary figures (counts by type, total area, total length) are computed on read, not stored.

## §5 File-processing flow

1. The upload is accepted in two layers:
   - **Size middleware** (`api/upload_limit.py`). FastAPI reads and parses the whole
     multipart body before it runs any dependency or the handler, so a check inside the
     route cannot stop an oversized upload from being received. The middleware sits in
     front: a declared `Content-Length` over the body limit gets 413 before any byte is
     read, and otherwise it counts bytes as they arrive and raises 413 once past the
     limit. The body limit is the file limit plus 64 KB for multipart framing.
   - **Upload route.** Checks the extension (case-insensitive) and the exact file size,
     keeps only the base name of the client's filename, writes the file to
     `data/uploads/<id><ext>` (never a client-controlled path), creates a `File` row with
     status `PENDING`, schedules the background task, and returns 202 with the id.
2. The background task sets status `PROCESSING` and calls the loader.
3. The loader:
   - for a zip, verifies it is a real zip, rejects the whole zip if any entry path could
     escape the extraction folder, ignores macOS metadata (`__MACOSX/`, `._*`,
     `.DS_Store`), matches extensions case-insensitively, requires `.shp`, `.shx` and
     `.dbf` for each shapefile (GDAL would read a shapefile without its `.dbf` and silently
     drop every attribute), refuses a zip whose declared sizes add up to more than 500 MB,
     and extracts each shapefile's parts under fixed names (`<n>/layer.shp`), so no name
     from the zip ever reaches the file system. A missing `.prj` shows up as a layer with
     no CRS;
   - for a KML, lists every layer and reads each one (pyogrio reads only the first layer
     when none is named, and only warns about it). Nested folders become separate, flat
     layers; GDAL names duplicate folders `Plots (#2)` and unnamed ones `Layer2`. LIBKML
     display fields are dropped (`DECISIONS.md` D26);
   - returns a list of layers, each with its own CRS and a list of features. A single
     combined GeoDataFrame is not possible, because it carries one CRS and a zip can hold
     shapefiles in several. Each feature is plain Python: a shapely geometry and a
     properties dict already passed through `to_json_safe` (D29), so no NaN, numpy or
     pandas value reaches the processor or the database. The processor walks the layers
     in order, which gives each feature its stable index.
4. The processor records the source CRS and feature count, then loops over features, sending
   each through the measure flow (§6) inside its own error boundary.
5. All `Feature` rows are written, the file becomes `COMPLETED`, and `completed_at` is set.
6. A file-level failure at any step sets `FAILED` with the reason in `error`.
7. The temp folder is always deleted in a `finally` block.
8. On startup, any file still `PENDING` or `PROCESSING` belongs to a process that no longer
   exists (`BackgroundTasks` lives inside the server process). It is marked `FAILED` with
   the error "Processing was interrupted by a server restart. Please upload the file
   again." Without this, such a file would report `PROCESSING` forever.

## §6 Measurement flow (per feature)

1. **Null or empty geometry:** no measurement, note "empty geometry".
2. **Point or MultiPoint:** no measurement, note "no measurement for point geometries".
3. **Invalid polygon:** run `make_valid` and set `repaired` to true. `make_valid` can return
   a GeometryCollection (polygons plus stray lines) or only lines (a zero-area sliver
   becomes a MultiLineString). Keep only the polygonal parts. If none remain, no
   measurement, note "degenerate polygon". A repaired polygon is never measured as a
   length.
4. **Polygon or MultiPolygon:** ask the CRS service for a measurement CRS (§9), transform,
   take area in m2. Compute the geodesic area from lon/lat coordinates: a geographic
   source is used as-is, a projected source is first transformed to EPSG:4326. Take the
   absolute value, because `Geod` returns signed area by ring orientation and Shapefile
   exteriors are clockwise, which comes back negative.
5. **LineString or MultiLineString:** same path, length in m, geodesic length alongside.
6. **No UTM zone** (centroid beyond 84°N or 80°S): no projected measurement. `value` and
   `geodesic_value` both hold the geodesic value, `measurement_method` is `geodesic`,
   `measurement_crs` is null, note "outside UTM coverage, geodesic value used"
   (`CRS.md` §9.6). Every other measured feature has `measurement_method` `projected`.
7. **Z values present:** measure in 2D. Note "Z ignored" only when some Z value is
   non-zero. GDAL keeps whatever the file has, and Google Earth exports write `,0` on
   every coordinate, so the note would otherwise appear on nearly every KML feature.
8. **Anything else** (GeometryCollection as input, unknown types): no measurement, note
   "unsupported geometry type". Never raise.
9. **Unexpected exception** inside a single feature: caught by the processor, recorded as a
   note, loop continues.

Notes are joined with `"; "` in a fixed order: CRS assumed, repaired, Z ignored, then the
reason for no measurement or the geodesic fallback.

## §7 Error handling and edge cases

| Situation | Caught in | Behaviour |
|---|---|---|
| Wrong extension | Upload route | 415, nothing saved |
| Over size limit | Upload route | 413, nothing saved |
| Corrupt or non-zip `.zip` | Loader | File `FAILED` with reason |
| Zip entry escaping the folder | Loader | File `FAILED`, nothing extracted outside |
| Zip missing `.shp`, `.shx` or `.dbf` | Loader | File `FAILED`, naming the missing parts |
| Zip with several shapefiles | Loader | Each read as its own layer with its own CRS; file `crs` is `MIXED` if they differ |
| Zip made on macOS (`__MACOSX/`, `._*`) | Loader | Metadata entries ignored |
| Zip expanding past 500 MB | Loader | File `FAILED` before anything is extracted |
| Password-protected zip | Loader | File `FAILED`, "not supported" |
| Shapefile GDAL cannot read | Loader | File `FAILED`, naming the shapefile |
| KML with no placemarks | Loader | No layers; `COMPLETED` with `feature_count` 0 |
| Malformed KML | Loader | File `FAILED` with GDAL's parse error, server paths removed |
| Missing `.prj` | CRS service | See `CRS.md` §9.6 |
| Multi-folder KML | Loader | Every layer read, name kept |
| Zero features | Processor | `COMPLETED` with `feature_count` 0 |
| Empty or null geometry | Measure service | Null measurement with note |
| Self-intersecting polygon | Measure service | Repaired, polygon parts measured, flagged |
| Polygon that repairs to lines only | Measure service | Null measurement, note "degenerate polygon" |
| Clockwise polygon ring | Measure service | Geodesic area taken as absolute value |
| Feature beyond 84°N or 80°S | Measure service | Geodesic value, note |
| Server restart mid-processing | Startup | File marked `FAILED` with a restart message |
| GeometryCollection | Measure service | Null measurement with note |
| One feature throws | Processor | Note recorded, loop continues |
| Dates or NaN in attributes | Loader (`to_json_safe`) | NaN, NaT and infinity become null; numpy scalars become Python values; date objects become ISO strings; shapefile date strings pass through unparsed |
| Unknown file id | Routes | 404 |
| Measurements while not completed | Routes | 409 with current status |
