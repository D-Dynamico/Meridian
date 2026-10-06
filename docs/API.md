# API Reference

§8. Stable section numbers, see [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md). Example values below
are illustrative. Keep this file and the README's API section in sync with the real
responses once the endpoints exist.

## §8.1 Endpoint summary

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/api/files/` | Upload a file and start processing | 202 |
| GET | `/api/files/{id}/` | File information and status | 200 |
| GET | `/api/files/{id}/measurements/` | Per-feature measurements | 200 |
| GET | `/api/files/` | List uploaded files, newest first (optional, §8.6) | 200 |
| GET | `/health` | Liveness check (optional) | 200 |

Interactive docs are served at `/docs` by FastAPI.

The trailing-slash paths above are canonical because the brief uses them. The same routes
are also registered without the trailing slash and hidden from the schema
(`include_in_schema=False`). Without them, FastAPI answers a slashless request with a 307
redirect. httpx and requests follow it and resend the body, but serving the request
directly removes a round trip and any dependence on how a given client handles redirects.

## §8.2 Upload

`POST /api/files/` with multipart form field `file`.

- Accepts `.zip` (Shapefile) and `.kml`.
- Returns immediately with status `PENDING`; processing continues in the background.

Response 202:

```json
{
  "id": "3f6c1a2e-8b7d-4c1e-9a21-7d4e5f6a8b90",
  "filename": "survey.kml",
  "status": "PENDING"
}
```

## §8.3 File information

`GET /api/files/{id}/`

Response 200:

```json
{
  "id": "3f6c1a2e-8b7d-4c1e-9a21-7d4e5f6a8b90",
  "filename": "survey.kml",
  "format": "KML",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "crs_assumed": false,
  "status": "COMPLETED",
  "error": null,
  "created_at": "2026-10-07T10:15:02Z",
  "completed_at": "2026-10-07T10:15:03Z",
  "summary": {
    "by_type": { "Polygon": 1, "LineString": 1, "Point": 1 },
    "total_area_m2": 1002345.12,
    "total_area_ha": 100.23,
    "total_length_m": 1530.4,
    "total_length_km": 1.53
  }
}
```

The fields shown in the brief's example (`id`, `filename`, `feature_count`, `crs`, `status`) must
always be present. `summary` is null until the file is `COMPLETED`, and `feature_count` is
null until processing has finished.

`summary` is computed from the stored features on every read. `by_type` counts features by
geometry type as read; features with no geometry at all are counted under `"No geometry"`.
The totals add up measured values only, so points and unmeasurable features add nothing.

Rounding happens only in responses: `m2` and `m` values to 2 decimals (finer than UTM's
accuracy), hectares and km to 4 (so one square metre still shows in hectares). Stored
values are never rounded.

An id that is not a valid UUID returns 422 (FastAPI's validation error); a valid UUID that
matches no file returns 404.

`crs` is `MIXED` when a zip holds shapefiles with different CRSs; each measurement then
carries its own `source_crs`. When `crs_assumed` is true, `crs` holds the assumed value
(`EPSG:4326`), not null.

## §8.4 Measurements

`GET /api/files/{id}/measurements/`

Query parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `limit` | 100 | Page size, maximum 1000 |
| `offset` | 0 | Page start |
| `geometry_type` | none | Filter, for example `Polygon` |
| `include_geometry` | true | Set false to drop raw geometry from the payload |

Response 200:

```json
{
  "file_id": "3f6c1a2e-8b7d-4c1e-9a21-7d4e5f6a8b90",
  "count": 3,
  "limit": 100,
  "offset": 0,
  "results": [
    {
      "index": 0,
      "layer": "Parcels",
      "geometry_type": "Polygon",
      "source_crs": "EPSG:4326",
      "measurement_method": "projected",
      "measurement_crs": "EPSG:32643",
      "measurement": {
        "type": "area",
        "value": 1002345.12,
        "unit": "m2",
        "hectares": 100.23
      },
      "geodesic_value": 1002101.88,
      "repaired": false,
      "note": null,
      "properties": { "name": "Plot 12" },
      "geometry": { "type": "Polygon", "coordinates": [] }
    },
    {
      "index": 1,
      "layer": "Roads",
      "geometry_type": "LineString",
      "source_crs": "EPSG:4326",
      "measurement_method": "projected",
      "measurement_crs": "EPSG:32643",
      "measurement": { "type": "length", "value": 1530.4, "unit": "m", "km": 1.53 },
      "geodesic_value": 1530.1,
      "repaired": false,
      "note": null,
      "properties": { "name": "Access road" },
      "geometry": { "type": "LineString", "coordinates": [] }
    },
    {
      "index": 2,
      "layer": "Roads",
      "geometry_type": "Point",
      "source_crs": "EPSG:4326",
      "measurement_method": null,
      "measurement_crs": null,
      "measurement": null,
      "geodesic_value": null,
      "repaired": false,
      "note": "No measurement for point geometries",
      "properties": { "name": "Gate" },
      "geometry": { "type": "Point", "coordinates": [75.81, 26.91] }
    }
  ]
}
```

Every result carries every field, using null where there is no value, so clients never have
to check whether a key exists. Two exceptions: `geometry` is left out entirely when
`include_geometry=false`, and `measurement` carries only the convenience field that matches
its type (`hectares` for area, `km` for length). Results are in file order (`index`), and
`count` is the total matching the `geometry_type` filter, not the page size.

`measurement.value` and `geodesic_value` are rounded to 2 decimals in the response,
hectares and km to 4; stored values are not rounded.

A file that is not `COMPLETED` returns 409: `"File is still PENDING"` (or `PROCESSING`), or
`"File processing FAILED: <reason>"` for a failed file (`DECISIONS.md` D21). `note` may hold several notes joined with `"; "`, for example
`"CRS assumed EPSG:4326 (no .prj); repaired invalid geometry"`.

`measurement_method` says how `measurement.value` was computed:

- `projected`: transformed to `measurement_crs` (a UTM zone) and measured there. This is
  the normal case. `geodesic_value` is the independent cross-check.
- `geodesic`: computed on the WGS84 ellipsoid, used only where UTM is undefined (beyond
  84°N or 80°S, `CRS.md` §9.6). `measurement_crs` is null and `geodesic_value` equals
  `measurement.value`.
- `null`: there is no measurement.

On a measured feature, `measurement_method` and `geodesic_value` are never null.

## §8.5 Errors

All errors use FastAPI's standard shape: `{ "detail": "<human-readable reason>" }`.

| Case | Code | Example detail |
|---|---|---|
| Unsupported extension | 415 | Only .kml and .zip files are supported |
| File too large | 413 | Maximum upload size is 50 MB |
| Missing `file` form field | 422 | FastAPI's standard validation error |
| Unknown file id | 404 | File not found |
| Measurements before completion | 409 | File is still PROCESSING |
| Measurements for a failed file | 409 | File processing FAILED: Shapefile zip is missing .dbf |

The 413 comes from one of two layers (`ARCHITECTURE.md` §5 step 1). Middleware rejects a
request body over the limit before FastAPI reads it: at once when `Content-Length`
declares it, otherwise as soon as the counted bytes pass the limit. The route then checks
the exact file size. Either way, nothing is saved. An oversized upload with an unsupported
extension gets 413, not 415, because the middleware answers before the route checks the
extension.

File-level processing problems (corrupt zip, missing parts) do not fail the upload request.
They surface as `status: FAILED` with `error` set on the file information endpoint, and in
the 409 from the measurements endpoint. The messages, exactly as returned:

| Problem | `error` |
|---|---|
| Not a zip archive | The file is not a valid zip archive |
| Entry fails its checksum | The zip is corrupt: Bad CRC-32 for file 'parcels.prj' |
| Password-protected zip | Password-protected zips are not supported |
| Entry path escapes the folder | The zip contains an unsafe path that points outside the archive: ../evil.shp |
| Expands past the limit | The zip expands to more than 500 MB |
| Too many shapefiles | The zip contains 120 shapefiles; at most 100 are supported |
| Too many KML folders | The KML has 120 folders; at most 100 are supported |
| No `.shp` in the zip | The zip contains no shapefile (.shp) |
| Missing parts | Shapefile 'parcels' is missing .shx and .dbf |
| GDAL cannot read a shapefile | Shapefile 'parcels' could not be read: 'parcels.shp' not recognized as being in a supported file format |
| Malformed KML | The KML file could not be read: syntax error on line 1 at offset 0 |
| Server restarted mid-processing | Processing was interrupted by a server restart; please upload the file again |
| Any other server-side failure | Processing failed because of an unexpected server error |

Messages never contain server paths, and an unexpected error never exposes exception
details; those go to the server log. The same policy applies to a single feature: a bug
while measuring one feature leaves the note "Measurement failed because of an unexpected
server error" and the rest of the file is processed normally.

## §8.6 List files

`GET /api/files/`, newest first. Not required by the brief; it lets a reviewer find the id
of an earlier upload without keeping it.

Query parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `limit` | 100 | Page size, 1 to 1000 |
| `offset` | 0 | Page start |

Out-of-range values return 422.

Response 200:

```json
{
  "count": 1,
  "limit": 100,
  "offset": 0,
  "results": [
    {
      "id": "3f6c1a2e-8b7d-4c1e-9a21-7d4e5f6a8b90",
      "filename": "survey.kml",
      "format": "KML",
      "status": "PENDING",
      "feature_count": null,
      "created_at": "2026-10-07T10:15:02Z",
      "completed_at": null
    }
  ]
}
```

`count` is the total across all pages. The envelope matches the measurements endpoint
(§8.4). Ties on `created_at` are broken by `id`, so pages never overlap.
