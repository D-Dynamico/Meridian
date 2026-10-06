# CLAUDE.md

This file guides Claude Code when working in this repository. Keep it under 200 lines. Detail
belongs in `docs/`; this file holds the essentials and points to the rest.

## Session Protocol (read first)

This repo keeps a written memory so every session starts with full context. Maintaining it is
part of the work, not an extra.

1. **At the start of a session**, read this file and the newest note in
   [`docs/sessions/`](docs/sessions/). That note says what changed last time, why, and what is
   still open. [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) maps every section number to
   the doc it lives in. Section numbers are cited from docstrings and commit messages, so they
   must never be renumbered. Add new sections at the end of a file instead.
2. **After each behavior-changing task**, update the running session note
   `docs/sessions/<YYYY-MM-DD>-<topic>.md` (create it on the first such task of the session):
   what changed, why (the decision, not just the diff), files touched, and how it was verified.
3. **At session end**, make sure the note covers scope, decisions with reasoning, surprises and
   open items. Update any doc whose behavior changed: `README.md`, `docs/ARCHITECTURE.md`,
   `docs/API.md`, `docs/CRS.md` or `docs/WORKFLOWS.md`.
4. **Record surprises, not just successes.** A wrong area that looked plausible, a KML layer
   that silently went missing, a test that could not fail. These are worth more than a list
   of what shipped.

## Working practice

- **One commit per substep.** A substep is one bounded change. Stage specific files, never
  `git add -A`.
- **Push once per stage**, only after that stage's exit criteria in `docs/WORKFLOWS.md` §13
  pass. Never push a stage with red tests.
- **Commit title:** one line, imperative, no trailing period, no `type(scope):` prefix.
- **Commit description:** written the way you would explain the change to a teammate. What
  changed, why it mattered, what you decided against. Full sentences, not a file list.
- **No em dashes** in docs, README, commit messages or user-facing strings. Use commas,
  colons or separate sentences.

## What this is

A backend take-home for **Aereo**, a drone survey and mapping company. The service accepts a
geospatial file (a `.zip` Shapefile or a `.kml`), extracts every feature, and returns
measurements: **area for polygons, length for lines, nothing for points.**

The brief asks for three endpoints, correct CRS handling, graceful failure on unsupported
geometries, and a README covering setup, API, architecture, design decisions, learnings and
future scope. The original brief is summarised in `docs/ARCHITECTURE.md` §1.

**The thesis behind every design choice:** a measurement that looks plausible but is wrong is
worse than no measurement. Area and length are never computed on latitude/longitude degrees.
Every measured feature is transformed to a projected CRS in metres first, and a geodesic
value is stored beside it as an independent cross-check.

**Out of scope on purpose:** a frontend or map view, authentication, 3D or volume
measurement, editing or re-exporting files, formats beyond Shapefile and KML. These go in
the README's future scope section.

## Stack

FastAPI, Pydantic, GeoPandas with pyogrio, Shapely 2, pyproj, SQLModel on SQLite, FastAPI
`BackgroundTasks`, pytest. No external APIs and no API keys. Everything runs offline.
Rationale for each choice: `docs/DECISIONS.md` §10.

## Commands

```
py -3.12 -m venv .venv                                  # Linux/macOS: python3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m uvicorn app.main:app --reload   # then open http://127.0.0.1:8000/docs
.venv/Scripts/python -m pytest -q                       # offline, no fixtures downloaded
.venv/Scripts/python scripts/mutation_check.py --check  # after a refactor: patterns still match?
.venv/Scripts/python scripts/mutation_check.py          # all mutation checks, about 10 minutes
.venv/Scripts/python scripts/make_samples.py            # regenerate samples/ (tests pin them)
docker build -t meridian .                              # about 2 minutes on a cold cache
docker run --rm -p 8000:8000 meridian                   # add -v meridian-data:/data to keep data
docker run --rm meridian python -m pytest -q -p no:cacheprovider   # suite on Linux
```

Optional environment: `MERIDIAN_DATA_DIR` (default `data/`, `/data` in Docker),
`MERIDIAN_MAX_UPLOAD_MB` (default 50). Inside Docker the code folder is read-only to the
`meridian` user, so pytest's cache must be disabled there.

**File tooling on Windows:** always pass `encoding="utf-8"` when a script reads or writes
repo files. The default is cp1252, which double-encodes `§` on the way back out.

## Architecture in one paragraph

Routes stay thin. **The upload route** validates type and size, saves the file, creates a
`File` record with status `PENDING`, and returns 202. **A background task** runs the
processor, which calls three services in order. **The loader** extracts the zip safely or
reads the KML, iterating every layer, and returns one combined feature set. **The CRS
service** decides how each feature should be measured: as-is if already projected in metres,
otherwise in the UTM zone of the feature's own centroid. **The measure service** computes area
or length, repairs invalid polygons, and returns a null measurement with a note for anything
it cannot measure. Results land in the `Feature` table and the file becomes `COMPLETED` or
`FAILED`. The read endpoints only ever touch the database.

Full detail, module map and data model: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Conventions to preserve when editing

- **Never measure in degrees.** Shapely does flat-plane maths, so `.area` on EPSG:4326
  returns square degrees, which mean nothing. All measurement goes through the CRS service
  (`docs/CRS.md` §9).
- **Routes contain no geospatial logic.** GeoPandas, Shapely and pyproj are imported only in
  `app/services/`. This keeps the services unit-testable without the API and makes live
  interview changes (add perimeter, add GeoJSON) a small edit in one place. The upload
  route reaches the processor only through `Depends(get_processor)`, wired in `main.py`;
  it never imports it, not even lazily (D28).
- **Nothing from pandas or numpy leaves the loader.** Properties pass through
  `to_json_safe`, because `json.dumps` happily stores NaN as invalid JSON, and Pydantic
  then hides it as null in responses, so only the stored text shows the damage (D29).
- **One bad feature never sinks the file.** Each feature is processed inside its own error
  boundary. A feature that fails is recorded with a note and the loop continues. Only
  file-level problems (corrupt zip, missing `.shp`) mark the whole file `FAILED`.
- **Unsupported is an answer, not an error.** Points, GeometryCollections and empty
  geometries get `measurement: null` plus a human-readable `note`. Never raise for these.
- **Be honest about assumptions.** A missing `.prj` is never silently treated as EPSG:4326.
  If the CRS is assumed, the response says `crs_assumed: true`. If a polygon was repaired,
  it says `repaired: true`.
- **Only UTM is measured in place.** Every other CRS, even one projected in metres such as
  Web Mercator (+22.3% area at 25°N), is transformed to the feature's UTM zone first.
- **Geodesic area is signed: orient rings, do not just `abs()`.** `Geod` signs area by
  ring orientation. `abs()` fails for a MultiPolygon with opposite-wound parts and for a
  hole wound like its exterior, so rings are oriented with `shapely.orient_polygons`
  first (D30). Feed `Geod` lon/lat only.
- **Accuracy fixtures are built on the ground.** Use `Geod.fwd`, never a square drawn in UTM,
  which measures exactly 1,000,000 m2 whether or not the CRS code works.
- **Read every KML layer.** GeoPandas reads only the first layer by default and only warns
  about the rest. The loader must list layers and read each one, keeping the layer name on
  every feature.
- **Axis order is always lon, lat.** Every pyproj transformer is built with `always_xy=True`.
  Transformers are cached per EPSG code, not rebuilt per feature.
- **Blocking work stays off the event loop.** GeoPandas and pyproj are CPU-bound. Handlers
  that touch them are plain `def`, and processing runs in the background task.
- **Upload safety is not optional.** Size limit, extension check, zip-slip protection on
  extraction, and temp folders cleaned up in a `finally` block.
- **Metric units in the API.** `m2` and `m` are the canonical values. Hectares and km are
  convenience fields, never replacements.
- **Every module stays under about 300 lines** of code. Split before it grows past that.
- **Mutation-test anything that guards something.** If deleting the CRS transform does not
  turn a test red, that test is not protecting anything (`docs/TEST_PLAN.md` §17).

## Status

Stage 0 (docs, reviewed against real library behaviour), **Stage 1** (skeleton, data
model, upload with size middleware, list, health, architecture guard), **Stage 2**
(loader, JSON-safe properties), **Stage 3** (CRS and measure services) and **Stage 4**
(processor injected via `get_processor`, file information and measurements endpoints)
and **Stage 5** (error messages, automated mutation checks) complete; see
`docs/sessions/2026-10-07-stage1.md`. **Stage 6** (samples, Dockerfile, README,
fresh-clone check) complete; see `docs/sessions/2026-10-07-stage6.md`. Left for
submission: making the GitHub repository public, which is the user's call.
Stages and exit criteria: `docs/WORKFLOWS.md` §13.

Open questions are tracked in the newest session note, not here.

## Docs

| File | What it is |
|---|---|
| [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) | Index. Maps every § number to its file |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Brief, scope, structure, data model, processing and measurement flows |
| [`docs/API.md`](docs/API.md) | Endpoints, request and response shapes, error codes |
| [`docs/CRS.md`](docs/CRS.md) | Projection strategy, UTM selection, geodesic cross-check, edge cases |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Design decisions with alternatives considered |
| [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) | Setup, file lifecycle, build stages with exit criteria, definition of done |
| [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) | Fixtures, per-module test requirements, mutation checks |
| [`docs/sessions/`](docs/sessions/) | Dated notes: what changed, why, and what nearly broke |
