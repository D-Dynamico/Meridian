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
   - Shapefile: read it from that shapefile's `.prj`. A zip with several shapefiles can
     have a different CRS per layer, so the CRS is tracked per layer, not per file.
   - KML: always EPSG:4326, as required by the KML specification.
2. **No CRS declared** (missing `.prj`): see §9.6.
3. **Source is a UTM CRS** (pyproj reports a `utm_zone`): measure directly, no transform.
   Record the source CRS as the measurement CRS.
4. **Any other projected source**, whether in metres or not: transform to UTM like a
   geographic source. "Projected and in metres" is not enough to trust a CRS for
   measurement. EPSG:3857 (Web Mercator) is projected and in metres, and it inflates area
   by 22.3 percent at 25°N (§9.3). Sending every non-UTM CRS through the same path also
   covers US survey feet and national grids with one rule.
5. **Source is geographic:** compute the feature centroid in lon/lat and choose the UTM zone
   (§9.3).
6. **Transform** the geometry to that zone and compute `.area` or `.length`.
7. **Cross-check** with the geodesic value (§9.4) and store both. The geodesic calculation
   always needs lon/lat, so a projected source is transformed to EPSG:4326 for it.

The decision is made **per feature**, not once per file. A file whose features span several
UTM zones measures each in its own zone.

## §9.3 UTM zone selection

- Zone number: `floor((lon + 180) / 6) + 1`, clamped to 60, because longitude exactly 180
  would otherwise give zone 61.
- Hemisphere: EPSG 326xx for latitude at or above 0, EPSG 327xx below.
- UTM is defined only from 80°S to 84°N. Outside that band there is no zone (§9.6).
- India spans roughly zones 42 to 47. Jaipur and Bengaluru both fall in zone 43
  (EPSG:32643).
- Transformers are cached per target EPSG code and built with `always_xy=True`, so axis
  order is always lon, lat.

**Why UTM:** it is the standard projected system in survey work, conformal, metre-based, and
its distortion is small and well understood inside a zone.

**Why not Web Mercator (EPSG:3857):** it inflates area badly away from the equator. At 25°N
the measured area error is +22.3 percent. It is a display projection, not a measurement
one.

**A hemisphere bug is invisible in the numbers.** The same square at 56°N and 56°S measures
identically, because the northern and southern UTM zones differ only by a false northing.
Tests must assert the selected EPSG code, not just the area (`TEST_PLAN.md` §16).

## §9.4 Geodesic cross-check

pyproj's `Geod` on the WGS84 ellipsoid computes area and length directly on the ellipsoid
from lon/lat, with no projection at all. The service stores this value beside the projected
one for every measured feature.

Purpose:

- An independent check that the projected value is sane. A large gap points to a bug, for
  example a wrong zone or swapped axes.
- A ready answer to the interview question "how do you know your numbers are right?"
- A fallback for very large features where any single projection is a poor fit, and the
  only answer beyond UTM's latitude limits (§9.6).

Two rules for using it:

- **Input must be lon/lat.** A projected source is transformed to EPSG:4326 first.
- **Area is signed.** `Geod.geometry_area_perimeter` returns negative area for clockwise
  rings, and Shapefile exteriors are clockwise by specification. Always take `abs()`.

## §9.5 Accuracy expectations

- UTM applies a scale factor of 0.9996 at the zone's central meridian. The scale grows with
  distance from the central meridian, roughly `0.9996 * (1 + (dlon_rad * cos(lat))^2 / 2)`.
- Area error is roughly double the scale error.
- So the gap between projected and geodesic values depends on where the feature sits in its
  zone. It is not "well under 0.1 percent" everywhere, as an earlier version of this file
  claimed.

Measured gaps, projected area against geodesic area, for 1 km squares built with
`Geod.fwd`:

| Location | Area gap |
|---|---|
| Jaipur (about 0.8° from the central meridian) | -0.064% |
| Central meridian at the equator | -0.080% |
| Bengaluru (about 2.6° from the central meridian) | +0.116% |
| Zone edge at the equator | +0.195% |

The largest length gap measured was 0.098 percent.

**Test tolerances:** projected and geodesic agree within **0.25 percent for area** and
**0.15 percent for length**. Each tolerance sits just above the worst case inside a zone,
so it allows correct UTM behaviour, yet a wrong zone or swapped axes still fail it badly.

**Fixtures must be built on the ground, not in the projection.** A square built in UTM
coordinates and measured in the same UTM zone always comes out at exactly 1,000,000 m2 and
tests nothing. Accuracy fixtures are built with `Geod.fwd` (true ground distances) in
lon/lat (`TEST_PLAN.md` §15).

Record the gaps observed on the sample files in the README's learnings section.

## §9.6 CRS edge cases

| Case | Behaviour |
|---|---|
| Missing `.prj` | If every coordinate fits lon within ±180 and lat within ±90, assume EPSG:4326: the layer's CRS is stored as `EPSG:4326`, `crs_assumed` is true, and each feature is noted. Storing the assumed value instead of null keeps the brief's "every feature reports its CRS" true while the flag keeps it honest. Otherwise `crs` is null and measurement is skipped with a note. See `DECISIONS.md` D10. |
| Layers with different CRSs | Each feature records its own `source_crs`. File `crs` is `MIXED`. |
| Projected but not UTM (for example EPSG:3857) | Transformed to UTM, never measured in place (§9.2 step 4). |
| Feature crosses a UTM zone boundary | Use the centroid's zone. Error stays small for features a few km across. Mention in the README. |
| Very large feature (district or state scale) | Projected value is less reliable. The geodesic value is the better answer; note this as future work for automatic switching or a local equal-area projection. |
| Centroid beyond 84°N or 80°S | UTM is undefined there. No projected measurement: `value` is the geodesic value, `measurement_crs` is null, note "outside UTM coverage, geodesic value used". |
| Centroid exactly on the equator or a zone edge | Deterministic rule: latitude 0 counts as north; a longitude on a boundary goes to the higher zone; longitude 180 is clamped to zone 60. |
| Antimeridian-crossing geometry | Out of scope. Note as a known limitation. |
| Z coordinates | Ignored for measurement. Noted only when some Z value is non-zero, since GDAL's KML reader returns Z = 0 everywhere. |
