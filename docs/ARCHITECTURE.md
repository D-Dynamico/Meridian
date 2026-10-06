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
  main.py              FastAPI app, router registration, startup (create tables)
  api/files.py         Routes only. No GeoPandas, Shapely or pyproj imports
  core/config.py       Upload size limit, allowed extensions, storage paths
  db/models.py         SQLModel tables: File, Feature
  db/session.py        Engine and session dependency
  schemas/files.py     Pydantic response models
  services/
    loader.py          Zip validation and safe extraction, KML reading, layer iteration
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

**Dependency direction:** `api` depends on `schemas` and `db`, and calls `processor` only to
schedule work. `processor` depends on the three services and `db`. The services depend on
nothing inside `app/` except `core/config`. Nothing imports `api`.

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
| crs | Source CRS as a string such as `EPSG:4326`, or null if unknown |
| crs_assumed | True when no CRS was declared and one was inferred |
| feature_count | Total features across all layers |
| status | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| error | Human-readable reason when `FAILED`, else null |
| created_at, completed_at | Timestamps |

**Feature**

| Column | Notes |
|---|---|
| id | Primary key |
| file_id | Foreign key to File |
| index | Position within the file, stable across reads |
| layer | Layer or folder name (KML) or shapefile name |
| geometry_type | As read, for example `MultiPolygon` |
| geometry | GeoJSON in the source CRS |
| properties | Attributes as JSON, made JSON-safe |
| measurement_type | `area`, `length` or `none` |
| value | Projected measurement in m2 or m, or null |
| unit | `m2`, `m` or null |
| geodesic_value | Geodesic cross-check in the same unit, or null |
| measurement_crs | Projected CRS used, for example `EPSG:32643` |
| repaired | True if `make_valid` changed the geometry |
| note | Why there is no measurement, or what was assumed |

Summary figures (counts by type, total area, total length) are computed on read, not stored.

## §5 File-processing flow

1. The upload route checks extension and size, writes the file to a temp folder, creates a
   `File` row with status `PENDING`, schedules the background task, and returns 202 with
   the id.
2. The background task sets status `PROCESSING` and calls the loader.
3. The loader:
   - for a zip, verifies it is a real zip, rejects any entry path that escapes the extraction
     folder, requires `.shp`, `.shx` and `.dbf`, and notes whether `.prj` is present;
   - for a KML, lists every layer and reads each one;
   - returns one combined feature set with a `layer` column and the source CRS.
4. The processor records the source CRS and feature count, then loops over features, sending
   each through the measure flow (§6) inside its own error boundary.
5. All `Feature` rows are written, the file becomes `COMPLETED`, and `completed_at` is set.
6. A file-level failure at any step sets `FAILED` with the reason in `error`.
7. The temp folder is always deleted in a `finally` block.

## §6 Measurement flow (per feature)

1. **Null or empty geometry:** no measurement, note "empty geometry".
2. **Invalid polygon:** run `make_valid`, set `repaired` to true, continue.
3. **Point or MultiPoint:** no measurement, note "no measurement for point geometries".
4. **Polygon or MultiPolygon:** ask the CRS service for a measurement CRS (§9), transform,
   take area in m2. Compute the geodesic area from the original coordinates.
5. **LineString or MultiLineString:** same path, length in m, geodesic length alongside.
6. **Z values present:** measure in 2D and note that Z was ignored.
7. **Anything else** (GeometryCollection, unknown types): no measurement, note "unsupported
   geometry type". Never raise.
8. **Unexpected exception** inside a single feature: caught by the processor, recorded as a
   note, loop continues.

## §7 Error handling and edge cases

| Situation | Caught in | Behaviour |
|---|---|---|
| Wrong extension | Upload route | 415, nothing saved |
| Over size limit | Upload route | 413, nothing saved |
| Corrupt or non-zip `.zip` | Loader | File `FAILED` with reason |
| Zip entry escaping the folder | Loader | File `FAILED`, nothing extracted outside |
| Zip missing `.shp`, `.shx` or `.dbf` | Loader | File `FAILED`, naming the missing parts |
| Zip with several shapefiles | Loader | Each read as its own layer |
| Missing `.prj` | CRS service | See `CRS.md` §9.6 |
| Multi-folder KML | Loader | Every layer read, name kept |
| Zero features | Processor | `COMPLETED` with `feature_count` 0 |
| Empty or null geometry | Measure service | Null measurement with note |
| Self-intersecting polygon | Measure service | Repaired, measured, flagged |
| GeometryCollection | Measure service | Null measurement with note |
| One feature throws | Processor | Note recorded, loop continues |
| Dates or NaN in attributes | Serialisation | Converted to strings or null |
| Unknown file id | Routes | 404 |
| Measurements while not completed | Routes | 409 with current status |
