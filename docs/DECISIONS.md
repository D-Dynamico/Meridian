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
| D10 | Missing `.prj` | Infer EPSG:4326 only when coordinates fit, and flag it | Reject the file; always assume 4326 | Rejecting loses usable data. Silent assumption hides risk. Inferring with a visible flag is the honest middle. |
| D11 | Units | m2 and m canonical, hectares and km as extra fields | Metres only | Hectares are how land parcels are discussed in India; metric base units stay unambiguous for machines. |
| D12 | Layer handling | Read every layer, keep the layer name per feature | Read only the default layer | GeoPandas reads one layer by default, which silently drops data from multi-folder KML files. |
| D13 | Code layout | Thin routes, logic in three services | Logic inside route handlers | Services are unit-testable without HTTP, and interview changes (add perimeter, add GeoJSON) touch one module. |
| D14 | Async handlers | Plain `def` for anything touching geospatial libraries | `async def` everywhere | GeoPandas and pyproj are CPU-bound and blocking. Plain `def` runs in FastAPI's threadpool and keeps the event loop free. |

## Open decisions

Record new ones here until they are settled, then move them into the table with reasoning.

- Upload size limit: 50 MB proposed. Confirm once real sample files are tested.
- Whether `GET /api/files/{id}/measurements/` should return partial results while
  `PROCESSING` instead of 409.
