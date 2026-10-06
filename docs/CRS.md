# CRS Handling

§9. This is the core of the assignment and the first thing a reviewer at a mapping company
will check. Every rule here should be explainable in an interview without notes.

## §9.1 Why degrees fail

Shapely does flat-plane geometry. It has no idea that coordinates in EPSG:4326 are angles on
an ellipsoid. Calling `.area` on lon/lat returns square degrees, a unit with no fixed size.

One degree of latitude is about 111 km everywhere. One degree of longitude is about 111 km at
the equator, about 100 km at 25°N (Rajasthan), and zero at the poles. A square degree near
Bengaluru and a square degree near Delhi cover different ground areas, so no single
conversion factor exists.

**Rule:** geometry in a geographic CRS is always transformed to a projected CRS in metres
before area or length is computed.

## §9.2 Per-feature decision steps

1. **Find the source CRS.**
   - Shapefile: read it from the `.prj`.
   - KML: always EPSG:4326, as required by the KML specification.
2. **No CRS declared** (missing `.prj`): see §9.6.
3. **Source is projected and in metres** (for example a UTM zone): measure directly, no
   transform. Record the source CRS as the measurement CRS.
4. **Source is projected but not in metres** (for example US survey feet): transform to UTM
   like a geographic source, so units are always metres.
5. **Source is geographic:** compute the feature centroid in lon/lat and choose the UTM zone
   (§9.3).
6. **Transform** the geometry to that zone and compute `.area` or `.length`.
7. **Cross-check** with the geodesic value (§9.4) and store both.

The decision is made **per feature**, not once per file. A file whose features span several
UTM zones measures each in its own zone.

## §9.3 UTM zone selection

- Zone number: longitude shifted by 180, divided into 6-degree bands, counted from 1.
- Hemisphere: EPSG 326xx for latitude at or above 0, EPSG 327xx below.
- India spans roughly zones 42 to 47. Jaipur and Bengaluru both fall in zone 43
  (EPSG:32643).
- Transformers are cached per target EPSG code and built with `always_xy=True`, so axis
  order is always lon, lat.

**Why UTM:** it is the standard projected system in survey work, conformal, metre-based, and
its distortion is small and well understood inside a zone.

**Why not Web Mercator (EPSG:3857):** it inflates area badly away from the equator. At 25°N
the area error is already around 20 percent. It is a display projection, not a measurement
one.

## §9.4 Geodesic cross-check

pyproj's `Geod` on the WGS84 ellipsoid computes area and length directly on the ellipsoid
from lon/lat, with no projection at all. The service stores this value beside the projected
one for every measured feature.

Purpose:

- An independent check that the projected value is sane. A large gap points to a bug, for
  example a wrong zone or swapped axes.
- A ready answer to the interview question "how do you know your numbers are right?"
- A fallback for very large features where any single projection is a poor fit (§9.6).

## §9.5 Accuracy expectations

- UTM applies a scale factor of 0.9996 at the zone's central meridian, so scale error is
  about 0.04 percent there and grows towards the zone edges.
- Area error is roughly double the scale error.
- For parcel-sized and site-sized features, projected and geodesic values should agree to
  well under 0.1 percent. Tests assert this (`TEST_PLAN.md` §16).
- Record the actual gap observed on test data in the README's learnings section.

## §9.6 CRS edge cases

| Case | Behaviour |
|---|---|
| Missing `.prj` | File `crs` is null. If every coordinate fits lon within ±180 and lat within ±90, assume EPSG:4326, set `crs_assumed: true`, and note it on each feature. Otherwise skip measurement with a note. Document the choice in `DECISIONS.md`. |
| Feature crosses a UTM zone boundary | Use the centroid's zone. Error stays small for features a few km across. Mention in the README. |
| Very large feature (district or state scale) | Projected value is less reliable. The geodesic value is the better answer; note this as future work for automatic switching or a local equal-area projection. |
| Centroid exactly on the equator or a zone edge | Deterministic rule: latitude 0 counts as north; a longitude on a boundary goes to the higher zone. |
| Antimeridian-crossing geometry | Out of scope. Note as a known limitation. |
| Z coordinates | Ignored for measurement, noted per feature. |
