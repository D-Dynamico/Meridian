# Design Decisions

§10. Each decision lists what was chosen, what else was considered, and why. The README's
design decisions section is a condensed version of this file. When a decision changes,
update the row and explain the change in the session note; do not delete the history of why.

## §10 Decision log

| # | Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|---|
| D1 | Framework | FastAPI | Django + DRF | Three endpoints do not need Django's admin, ORM conventions or app structure. FastAPI gives typed request and response models and an OpenAPI UI at `/docs` for free, which lets reviewers test without reading the README first. |
| D2 | Projection | Per-feature UTM from the feature's centroid | One UTM zone for the whole file; local Lambert Azimuthal Equal-Area per feature; geodesic only | Accurate for files whose features are spread out, standard in survey work, easy to explain and verify. LAEA is more exact for area but harder to justify quickly. Geodesic-only skips the projection the brief explicitly asks for. |
| D3 | Cross-check | Store a geodesic value beside every projected value | No cross-check | Shows the numbers are verified, not just computed. Catches wrong-zone and swapped-axis bugs. Cheap to compute. |
| D4 | Processing model | FastAPI `BackgroundTasks` | Synchronous processing in the request; Celery with Redis | Makes the status field meaningful and keeps uploads fast, without adding a broker reviewers must run. Celery is the named scale-up path in future scope. |
| D5 | Database | SQLite through SQLModel | PostgreSQL with PostGIS | Zero setup for reviewers. PostGIS is the production answer and is named in future scope. |
| D6 | Geometry storage | GeoJSON text column | WKB; PostGIS geometry type | Human-readable, returned directly in responses, no conversion layer. |
| D7 | File reading | GeoPandas over pyogrio | Fiona; raw GDAL/OGR; a KML-specific parser | pyogrio is faster and ships GDAL inside its wheel, avoiding system installs. GeoPandas gives one API for both formats. |
| D8 | Identifiers | UUID | Auto-increment integer | Not guessable, safe to expose in URLs. |
| D9 | Invalid geometry | Repair with `make_valid` and flag it | Reject the feature | Field data is often slightly invalid. Repairing keeps results useful; the flag keeps them honest. |
| D10 | Missing `.prj` | Infer EPSG:4326 only when coordinates fit, store `EPSG:4326` as the CRS, and flag it with `crs_assumed` | Reject the file; always assume 4326; infer but store `crs` as null | Rejecting loses usable data. Silent assumption hides risk. Inferring with a visible flag is the honest middle. Revised in the 2026-10-07 review: the first version stored null, which broke the brief's "every feature reports its CRS". The flag, not a null, is what carries the warning. |
| D11 | Units | m2 and m canonical, hectares and km as extra fields | Metres only | Hectares are how land parcels are discussed in India; metric base units stay unambiguous for machines. |
| D12 | Layer handling | Read every layer, keep the layer name per feature | Read only the default layer | GeoPandas reads one layer by default and drops the other folders of a multi-folder KML. pyogrio does warn, but a warning in a background task's log is easy to miss and nothing in the response would show the loss. |
| D13 | Code layout | Thin routes, logic in three services | Logic inside route handlers | Services are unit-testable without HTTP, and interview changes (add perimeter, add GeoJSON) touch one module. |
| D14 | Async handlers | Plain `def` for anything touching geospatial libraries | `async def` everywhere | GeoPandas and pyproj are CPU-bound and blocking. Plain `def` runs in FastAPI's threadpool and keeps the event loop free. |
| D15 | Measuring in the source CRS | Only when the source is UTM | Any projected CRS in metres | "Projected and in metres" lets Web Mercator through, which overstates area by 22.3 percent at 25°N. UTM is the one family whose distortion is known and bounded, so every other CRS is sent to UTM. |
| D16 | Multiple notes per feature | One `note` string, parts joined with `"; "` in a fixed order | A list of notes; only the most important note | Keeps the response flat and readable. A feature can be CRS-assumed, repaired and Z-stripped at once, and dropping any of those would hide an assumption. |
| D17 | Files interrupted by a restart | Mark `PENDING` and `PROCESSING` files `FAILED` on startup | Leave them; re-run them on startup | `BackgroundTasks` dies with the process, so those files would report `PROCESSING` forever. Re-running would need the upload to outlive its temp folder. A durable queue (Celery) is the real fix and is in future scope. |
| D18 | Trailing slashes | Canonical paths with a slash, as in the brief, plus hidden slashless aliases | Redirect only; slashless only | Matches the brief exactly. The aliases avoid a 307 round trip and any reliance on a client re-sending a multipart body after a redirect. |
| D19 | Feature position column | `feature_index` in the database, `index` in the API | `index` everywhere | Readability: `index` is easy to confuse with a DataFrame index in the processor. SQLModel would quote `index` correctly, so this is not a correctness fix. |

| D20 | Upload size limit | 50 MB | No limit; 10 MB; 500 MB | Survey parcel and alignment files are usually well under 50 MB, and the limit bounds both disk use and the memory GeoPandas needs to read a file. Configurable in `app/core/config.py`. |
| D21 | Measurements before completion | 409 for `PENDING`, `PROCESSING` and `FAILED`, with the status (and the failure reason for `FAILED`) in `detail` | Partial results while `PROCESSING`; 200 with an empty list | The processor writes all `Feature` rows at the end (`ARCHITECTURE.md` §5), so there are no partial results to return. An empty 200 would look like a file with no features. |
| D22 | How a value was computed | Explicit `measurement_method` (`projected` or `geodesic`), with `geodesic_value` always filled on a measured feature | Infer it from `measurement_crs` being null | A consumer should not have to know that a null CRS means "geodesic". The explicit field also leaves room for a future equal-area method. |
| D23 | Runtime | Python 3.12, `python:3.12-slim` in Docker | Full Debian image with system GDAL; Python 3.13 | pyogrio and pyproj wheels bundle GDAL and PROJ, so the slim image needs no system packages. 3.12 matches the development machine. |
| D24 | Where the size limit is enforced | ASGI middleware on the upload path, plus an exact file-size check in the route | Check in the route only; Starlette `max_part_size`; reverse proxy limit | FastAPI parses the whole multipart body before any dependency or handler runs, so a route check only fires after the upload is fully received and spooled. The middleware stops it at the header or at the first byte past the limit. It raises `HTTPException` because FastAPI re-raises those from body parsing and turns any other exception into a 400. A reverse proxy is the production answer and is in future scope. |

## Open decisions

Record new ones here until they are settled, then move them into the table with reasoning.

- KML system fields in `properties`. GDAL's LIBKML driver adds about 11 mostly-null
  columns to every KML feature (`altitudeMode`, `tessellate`, `extrude`, `visibility`,
  `drawOrder`, `icon`, `begin`, `end` and similar). Options: keep them all; drop them only
  where null for that feature; drop them always. Leaning towards dropping a known list
  only where null, which removes the noise but keeps a real value such as an explicit
  `altitudeMode`. Decide in Stage 2 with real KML output in front of us.
