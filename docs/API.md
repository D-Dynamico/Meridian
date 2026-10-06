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
| GET | `/api/files/` | List uploaded files, newest first (optional) | 200 |
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
always be present. `summary` is null until the file is `COMPLETED`.

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
to check whether a key exists. The one exception is `geometry`, which is left out entirely
when `include_geometry=false`. `note` may hold several notes joined with `"; "`, for example
`"CRS assumed EPSG:4326 (no .prj); repaired invalid geometry"`.

## §8.5 Errors

All errors use FastAPI's standard shape: `{ "detail": "<human-readable reason>" }`.

| Case | Code | Example detail |
|---|---|---|
| Unsupported extension | 415 | Only .zip and .kml files are supported |
| File too large | 413 | Maximum upload size is 50 MB |
| Unknown file id | 404 | File not found |
| Measurements before completion | 409 | File is still PROCESSING |
| Measurements for a failed file | 409 | File processing FAILED: Shapefile zip is missing .dbf |

The 413 check runs twice. A declared `Content-Length` over the limit is rejected before
the body is read. The copy to disk also counts bytes, so a missing or false header cannot
get past the limit, and a partial file is deleted.

File-level processing problems (corrupt zip, missing parts) do not fail the upload request.
They surface as `status: FAILED` with `error` set on the file information endpoint.
