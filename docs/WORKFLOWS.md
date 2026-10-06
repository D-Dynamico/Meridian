# Workflows

§11 to §14. How the project is set up, how a file moves through the system, and how the
build is staged. Stable section numbers, see [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md).

## §11 Local setup

Target: a reviewer goes from fresh clone to a working `/docs` page in under five minutes.

- Python 3.12 (`DECISIONS.md` D23). The Docker image is `python:3.12-slim`.
- A virtual environment in `.venv/`. On Windows the interpreter is under `.venv/Scripts/`.
- All dependencies pinned in `requirements.txt`. pyogrio wheels bundle GDAL, so no system
  GDAL install should be needed. If a platform needs one, document it in the README.
- No environment variables are required. Optional settings (upload limit, data directory)
  have defaults in `app/core/config.py`.
- The SQLite database file and upload temp folders live under a `data/` directory that is
  git-ignored and created on startup.
- A Dockerfile provides the one-command alternative. The README documents both paths.
- `samples/` holds a small KML and a small Shapefile zip so reviewers can upload something
  immediately.

## §12 File status lifecycle

| Status | Set by | Meaning |
|---|---|---|
| `PENDING` | Upload route | Saved and queued, processing not started |
| `PROCESSING` | Background task | Loader and measurement running |
| `COMPLETED` | Processor | Every feature recorded, including unmeasurable ones |
| `FAILED` | Processor | A file-level problem stopped processing; `error` explains it |

Rules:

- Transitions only move forward: `PENDING` to `PROCESSING` to `COMPLETED` or `FAILED`.
- A file is `COMPLETED` even if some features have null measurements. Unmeasurable is not
  failure.
- `FAILED` always carries a human-readable `error`.
- On startup, files left `PENDING` or `PROCESSING` by a previous process are marked
  `FAILED` with a restart message (`ARCHITECTURE.md` §5 step 8, `DECISIONS.md` D17).
- Tests run the processor synchronously rather than racing the background task.

## §13 Build stages and exit criteria

One commit per substep. Push once per stage, only when its exit criteria pass.

**Stage 0: Planning and docs**
- Exit: `CLAUDE.md` and every file in `docs/` exist and agree with each other.

**Stage 1: Skeleton and upload**
- Project layout from `ARCHITECTURE.md` §3, config, database models, session dependency.
- Upload route with extension and exact file-size checks, behind a size middleware that
  rejects oversized bodies before FastAPI reads them (`DECISIONS.md` D24). `File` row
  created as `PENDING`. The route schedules no background task yet,
  because there is no processor until Stage 4. Files stay `PENDING` until then.
- Health and list endpoints, with hidden slashless aliases (`DECISIONS.md` D18).
- Exit: app starts, `/docs` loads, uploading a KML returns 202 and creates a row; tests for
  upload validation pass.

**Stage 2: Loader**
- Zip validation, safe extraction, required-part checks, `.prj` detection.
- KML reading across every layer, layer name kept per feature.
- Combined feature set with source CRS.
- Exit: loader tests for multi-layer KML, missing parts, zip-slip and corrupt zips pass.

**Stage 3: CRS and measurement**
- CRS service: source detection, projected-in-metres shortcut, UTM selection, transformer
  cache, missing-`.prj` rule.
- Measure service: area, length, points, unsupported types, empty geometry, `make_valid`
  with polygon-part extraction, Z handling, geodesic cross-check with `abs()`, geodesic
  fallback beyond UTM limits.
- Exit: the `Geod.fwd`-built 1 km square and 1 km line fixtures measure within tolerance;
  projected and geodesic agree within 0.25 percent for area and 0.15 percent for length
  (`CRS.md` §9.5); every §16 measure and CRS test passes.

**Stage 4: Processor and read endpoints**
- Background processing, per-feature error boundary, status transitions, temp cleanup,
  startup recovery of interrupted files.
- File information endpoint with summary, measurements endpoint with pagination, filter and
  `include_geometry`.
- Exit: end-to-end API tests pass for KML and Shapefile; failed files report a reason;
  409 returned before completion.

**Stage 5: Hardening**
- Mutation checks from `TEST_PLAN.md` §17.
- Error messages reviewed for clarity.
- Module sizes checked against the 300-line guideline.
- Exit: every mutation check turns at least one test red.

**Stage 6: Packaging and README**
- Dockerfile, sample files, README with all required sections.
- Fresh-clone check on a clean machine or container.
- Exit: §14 definition of done is fully met.

## §14 Definition of done and submission

- [ ] All three required endpoints work for both `.zip` Shapefile and `.kml`
- [ ] Every feature reports index, geometry type, geometry, CRS and properties
- [ ] Polygons report area, lines report length, points report no measurement
- [ ] Unsupported geometries return a note, never an error
- [ ] Geographic input is never measured in degrees
- [ ] Full test suite passes offline
- [ ] README covers setup, API with example requests and responses, architecture
      (structure, file-processing flow, measurement flow, CRS handling), design decisions,
      learnings and future scope
- [ ] Sample files included and referenced in the README
- [ ] Fresh clone to working `/docs` in under five minutes, both with venv and Docker
- [ ] No em dashes in README or docs
- [ ] Repository is public on GitHub and the link has been shared
