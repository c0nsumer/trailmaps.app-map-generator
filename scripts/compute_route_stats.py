"""Compute per-route distance and elevation gain at build time.

Two stats, both gated by config:

  show_route_distance: true   →  walks the GeoJSON, sums haversine
                                 segment lengths per route. No data
                                 dependency; always runs when enabled.

  show_route_elevation: true  →  samples elevation along each route
                                 via USGS 3DEP's getSamples endpoint
                                 (1m lidar bare-earth where available,
                                 10m/30m fallback elsewhere), computes
                                 both positive elevation gain (climb)
                                 and absolute negative gain (loss).
                                 Requires network; degrades to
                                 no-data on failure or when the
                                 endpoint is unreachable.

Output: writes per-route stats into trails_geojson["metadata"]["routes"]
[<id>] as ``distance_m``, ``elevation_gain_m``, and ``elevation_loss_m``
(integers, meters). The runtime reads those values and formats them
per CONFIG.distanceUnits.

Caching:
  Elevation queries are cached in ``cache/route_stats/`` keyed by a
  stable hash of the sampled coordinates. A rebuild that doesn't
  change route geometry hits the cache and skips the network entirely.
  Distance is cheap and not cached (it'd just add bookkeeping cost).

Why USGS 3DEP (replaced opentopodata.org SRTM30m, 2026-04):
  Side-by-side comparison across nine maps and 71 comparable routes
  showed SRTM30m systematically over-reports elevation gain in
  forested terrain — its C-band radar penetrates only the top of
  forest canopy, not bare ground, so canopy-height variation is read
  as terrain change. 3DEP serves bare-earth data (vegetation removed
  by lidar processing) at 1m resolution everywhere this framework's
  primary maps live, with graceful 10m / 30m fallback elsewhere in
  the US.

  3DEP has no API key, no daily quota, supports up to 2000 points
  per request, and the only real failure mode is occasional HTTP 502
  under service load (handled by retry-with-backoff). Out-of-coverage
  (non-US) points return ``NoData`` and are treated as missing
  samples — non-US trails get ``elevation_*_m`` omitted from
  trails.geojson, and the runtime renders the route without stats.

Sampling tuned to the noise threshold (the two are coupled):
  - 25m horizontal spacing × 1m per-delta threshold detects every
    grade ≥4%. Going denser without lowering threshold rejects
    real mid-grade climbing signal; lowering threshold below
    lidar's ~0.5m noise floor lets noise back in.
  - 3-point centered moving average (75m window at 25m spacing)
    flattens lidar's residual noise without smoothing real climbs
    (which are typically ≥100m horizontal).
  - 1m noise threshold matches lidar bare-earth's actual vertical
    accuracy. Was 2m for SRTM30m's noisier output.

Failure modes (all non-fatal):
  - 3DEP API error / timeout              → log warning, skip
    elevation for that route; trails.geojson omits elevation_gain_m.
  - Out-of-coverage (non-US) point        → API returns NoData;
    treated like a missing sample.
  - Empty route (no coords)               → skip; both stats omitted.
  - HTTP 502/503 (transient overload)     → automatic retry with
    backoff (60s, 90s, 120s) per batch. After all retries are
    exhausted on a single batch, the build stops trying for the rest
    of the routes and lets cache fill in on a later run.
"""

import hashlib
import json
import os
import time

import console
import requests
from geodesy import haversine_m as _haversine_m


def _coords_for_route(features, route_id):
    """Concatenate every segment's coords for one route, in feature order.

    Filters features by ``route_id`` only — NOT by ``shared_routes``.

    This is critical for correctness: when a way is a member of
    multiple OSM relations, build_geojson emits one feature *per
    parent relation*, each with the appropriate ``route_id`` and a
    ``shared_routes`` list naming all the parents. Including features
    on the strength of ``shared_routes`` would double-count shared
    geometry (counted once for the route that "owns" it via
    ``route_id``, then again for every other relation it's shared
    with). On RAMBA's Ranger Loop, this had the system reporting
    4.31 mi for a route JOSM measured at 1.74 mi — a 2.5x inflation
    because the loop's ways were typically shared with 2-3 other
    relations.

    The ``route_id`` filter is exhaustive on its own: build_geojson
    iterates every parent relation's ways and emits a feature with
    ``route_id`` set to that parent. Every way in route A appears
    exactly once with ``route_id == A``.

    Segments are returned in GeoJSON feature order — there's no
    canonical traversal of a route through its junctions, and the
    elevation-gain pipeline handles the resulting discontinuities by
    inserting break markers between segments.
    """
    target = str(route_id)
    out = []
    for f in features:
        props = f.get("properties") or {}
        if str(props.get("route_id")) != target:
            continue
        geom = f.get("geometry") or {}
        if geom.get("type") == "LineString":
            out.append(geom.get("coordinates") or [])
    return out


def compute_distances(trails_geojson):
    """Return ``{route_id: distance_m_int}`` for every route in metadata.

    Sums haversine distance along each segment, then sums segment totals
    per route. Cheap (~few thousand sqrt's per typical map). Always
    returns an int (rounded meters); routes with no geometry get 0.
    """
    metadata = trails_geojson.get("metadata") or {}
    routes = metadata.get("routes") or {}
    features = trails_geojson.get("features") or []
    out = {}
    for route_id in routes.keys():
        total = 0.0
        for line in _coords_for_route(features, route_id):
            for (lon1, lat1), (lon2, lat2) in zip(line, line[1:]):
                total += _haversine_m(lon1, lat1, lon2, lat2)
        out[str(route_id)] = round(total)
    return out


# ----------------------------------------------------------------------
# Elevation
# ----------------------------------------------------------------------

# USGS 3D Elevation Program (3DEP) — public ArcGIS ImageServer that
# serves a multi-resolution DEM mosaic (1m lidar where available,
# falling through 10m and 30m). Free, no API key, no daily quota,
# supports up to 2000 points per request via getSamples.
USGS_3DEP_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
)
USGS_3DEP_BATCH = 2000  # service hard limit per request
USGS_3DEP_TIMEOUT = 60  # service can be slow under load

# Sampling spacing tuned to the (sampling × threshold) coupling. The
# noise threshold is per-adjacent-sample-delta; sampling spacing
# determines what minimum grade is detectable. With a 1m threshold:
#
#     min_grade = threshold / spacing
#     50m spacing → 2% grade detectable
#     30m spacing → 3.3% grade detectable
#     25m spacing → 4% grade detectable
#     10m spacing → 10% grade detectable
#     5m  spacing → 20% grade detectable
#
# At 25m we capture every grade ≥4%, which covers the range a rider
# would describe as "climbing." Gentler than 4% reads as "rolling" and
# being filtered out is fine. Going denser than 25m with a 1m
# threshold systematically rejects mid-grade climbing signal — every
# per-sample delta on a 4% climb falls below threshold and gets
# treated as noise, so a long gentle climb sums to zero gain. (This
# is *coupled*: dense sampling requires proportionally smaller
# threshold, but smaller threshold falls below the lidar noise floor.)
SAMPLE_INTERVAL_M = 25

# Hard cap on samples per route — bounds API cost on long routes. At
# 25m spacing, 2000 samples covers a 50km route fully. Routes longer
# than 50km get proportionally coarser sampling, which is fine because
# high-resolution detail on a 50km+ traverse isn't a useful signal for
# riders deciding whether to commit.
MAX_SAMPLES_PER_ROUTE = 2000

# Smoothing window for the elevation profile, applied as a centered
# moving average before differencing. 3 points at 25m spacing = 75m
# window, which flattens residual lidar noise without smoothing real
# trail-scale climbs (which are typically ≥100m horizontal). Set to 1
# to disable smoothing.
ELEVATION_SMOOTH_WINDOW = 3

# Minimum elevation delta between (post-smoothing) adjacent samples to
# count as real climbing. Below this, the delta is treated as DEM
# noise and ignored. Lidar bare-earth output has a vertical noise SD of
# ~0.3-0.5m in good terrain; thresholding at 1m rejects noise while
# preserving real climbs. Was 2m for the noisier SRTM30m source. See
# SAMPLE_INTERVAL_M for the (spacing × threshold) coupling that
# determines the minimum detectable grade.
ELEVATION_NOISE_THRESHOLD_M = 1.0

# Inter-request delay across the entire build (not just within a single
# route's batches). 3DEP doesn't publish a rate limit, but it's a
# public ArcGIS service — be polite. We track _last_api_call at module
# scope and sleep enough at the start of each request to guarantee the
# spacing.
INTER_REQUEST_DELAY_S = 1.0

# Backoff schedule for HTTP 5xx / transport-error retries. 3DEP's
# typical failure mode is brief load-balancer 502 bursts that clear
# in seconds, not extended outages. Start aggressive (5s, 15s) to
# avoid stalling the build on transient blips, then escalate to
# longer waits for genuine slow-recovery cases. After exhausting
# all retries on a single batch, we accept the API is genuinely
# unavailable for this build and stop trying for subsequent routes
# (the build still completes; missing routes get picked up on the
# next build via cache).
RETRY_BACKOFF_SECONDS = [5, 15, 60, 120]

# Module-scope timestamp of the last API call. Used by
# _fetch_elevations_batched to enforce ≥INTER_REQUEST_DELAY_S between
# any two requests (across routes), not just within one route's
# batches. Reset to 0 means "no prior call" → no sleep on the first
# request of a build.
_last_api_call = 0.0


def _subsample_segment(line, target_interval_m, max_samples):
    """Subsample a single connected line at ~target_interval_m spacing.

    Used by _subsample_route to handle each segment of a multi-segment
    route independently — see that function's docstring for why
    segment-aware sampling matters.

    Returns [] for degenerate (zero-length or single-point) input.
    """
    if len(line) < 2:
        return list(line)
    cum = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(line, line[1:]):
        cum.append(cum[-1] + _haversine_m(lon1, lat1, lon2, lat2))
    total_len = cum[-1]
    if total_len <= 0:
        return [line[0]]
    if total_len < target_interval_m:
        return list(line)
    sample_count = min(int(total_len / target_interval_m) + 1, max_samples)
    if sample_count < 2:
        sample_count = 2
    step = total_len / (sample_count - 1)
    out = [line[0]]
    pos = step
    j = 1
    for _ in range(1, sample_count - 1):
        while j < len(cum) and cum[j] < pos:
            j += 1
        if j >= len(cum):
            break
        seg_start = cum[j - 1]
        seg_end = cum[j]
        t = (pos - seg_start) / max(seg_end - seg_start, 1e-9)
        lon = line[j - 1][0] + t * (line[j][0] - line[j - 1][0])
        lat = line[j - 1][1] + t * (line[j][1] - line[j - 1][1])
        out.append([lon, lat])
        pos += step
    out.append(line[-1])
    return out


def _subsample_route(coord_lines, target_interval_m, max_samples):
    """Subsample a multi-segment route at ~target_interval_m spacing.

    Each segment in ``coord_lines`` is sampled independently and the
    results are concatenated with a ``None`` placeholder between
    segments. The placeholder marks a discontinuity for the gain
    computation: deltas across segment boundaries are dropped (a
    rider doesn't actually climb the elevation difference between the
    end of one segment and the start of the next, since those points
    aren't physically connected).

    This matters because OSM relations don't impose a traversal order
    on their member ways. ``_coords_for_route`` returns segments in
    GeoJSON feature order, which is rarely the order a rider would
    actually link them on the ground. Without segment-aware sampling,
    every transition between segments contributed a phantom positive
    delta to the elevation gain — typical RAMBA routes had 20-100
    such transitions, inflating the result by 2-3x.

    Total sample budget is ``max_samples`` across all segments, split
    proportionally by segment length. Very short segments (under one
    sample interval) get just their endpoints.

    Returns a list whose elements are either ``[lon, lat]`` pairs or
    ``None`` (segment break markers). The None markers must be
    preserved through to ``_gain_loss_from_samples``.
    """
    if not coord_lines:
        return []

    # Length of each segment (for proportional sample-budget allocation).
    seg_lens = []
    for line in coord_lines:
        if len(line) < 2:
            seg_lens.append(0.0)
            continue
        L = sum(
            _haversine_m(line[i][0], line[i][1], line[i + 1][0], line[i + 1][1])
            for i in range(len(line) - 1)
        )
        seg_lens.append(L)
    total_len = sum(seg_lens)
    if total_len <= 0:
        return []

    # Per-segment sample cap: the total budget split proportionally to
    # segment length, with a floor of 2 for any non-empty segment so
    # endpoints are always represented.
    per_seg_cap = []
    for L in seg_lens:
        if L <= 0:
            per_seg_cap.append(0)
        else:
            cap = max(2, int(round(max_samples * L / total_len)))
            per_seg_cap.append(cap)

    out = []
    for line, cap in zip(coord_lines, per_seg_cap):
        if cap == 0:
            continue
        sampled = _subsample_segment(line, target_interval_m, cap)
        if not sampled:
            continue
        if out:
            # Insert a break marker between segments so the elevation
            # gain computation doesn't compute a delta across the gap.
            out.append(None)
        out.extend(sampled)
    return out


def _hash_coords(coords_with_breaks):
    """Stable hash of a coords list + sampling parameters — used as
    the elevation cache key.

    Accepts the segment-aware shape from _subsample_route: a list of
    [lon, lat] pairs interspersed with None markers for segment breaks.
    The hash includes the breaks (encoded as the literal "|BREAK|")
    so that re-segmentation of a route changes the cache key — what
    we cache is gain-given-this-exact-sampling, and segment breaks
    affect the computed gain.

    The hash ALSO includes the three sampling-pipeline constants
    (SAMPLE_INTERVAL_M, MAX_SAMPLES_PER_ROUTE, ELEVATION_SMOOTH_WINDOW)
    so a curator tuning any of them invalidates the cache cleanly. If
    those constants weren't part of the key, a tuning change would
    silently keep returning old gain/loss numbers from cached files
    until the next --force rebuild.
    """
    h = hashlib.sha1()
    h.update(
        f"si={SAMPLE_INTERVAL_M},mx={MAX_SAMPLES_PER_ROUTE},sw={ELEVATION_SMOOTH_WINDOW}||".encode()
    )
    for item in coords_with_breaks:
        if item is None:
            h.update(b"|BREAK|")
            continue
        lon, lat = item
        # 6 decimals = ~11 cm precision, well below the DEM's resolution
        h.update(f"{lon:.6f},{lat:.6f}|".encode())
    return h.hexdigest()[:16]


def _elev_cache_path(cache_dir, route_id, coord_hash):
    return os.path.join(
        cache_dir,
        "route_stats",
        f"elev_{route_id}_{coord_hash}.json",
    )


def _fetch_elevations_batched(coords_with_breaks, log_prefix=""):
    """Call USGS 3DEP getSamples for the valid coords, preserving break markers.

    Input: a list whose items are either [lon, lat] pairs or None
    (segment break markers from _subsample_route).

    Output: a parallel list of the same length where each [lon, lat]
    is replaced by its elevation (float meters, or None if the API
    couldn't resolve it — out-of-coverage points return ``NoData``)
    and the None markers are passed through unchanged.

    Raises requests.RequestException on transport-level failure
    (after exhausting retries); raises RuntimeError on persistent
    HTTP error so the caller can stop hammering the API.
    """
    valid_indices = [i for i, item in enumerate(coords_with_breaks) if item is not None]
    valid_coords = [coords_with_breaks[i] for i in valid_indices]

    global _last_api_call
    elev_for_valid = [None] * len(valid_coords)
    total_batches = (len(valid_coords) + USGS_3DEP_BATCH - 1) // USGS_3DEP_BATCH

    for batch_index, i in enumerate(range(0, len(valid_coords), USGS_3DEP_BATCH)):
        batch = valid_coords[i : i + USGS_3DEP_BATCH]
        # ArcGIS multipoint geometry — points in [x, y] = [lon, lat]
        # order, EPSG:4326 (WGS84).
        geometry = json.dumps(
            {
                "points": [[lon, lat] for lon, lat in batch],
                "spatialReference": {"wkid": 4326},
            }
        )

        # Per-batch retry loop with exponential-ish backoff on transient
        # failures (5xx / network errors). 3DEP throws occasional 502s
        # under service load; a 60s wait usually clears it. After
        # exhausting all retries on a single batch we raise so the
        # caller can stop trying for the rest of the build.
        max_attempts = len(RETRY_BACKOFF_SECONDS) + 1
        result_data = None
        for attempt in range(max_attempts):
            elapsed = time.time() - _last_api_call
            if elapsed < INTER_REQUEST_DELAY_S:
                time.sleep(INTER_REQUEST_DELAY_S - elapsed)
            _last_api_call = time.time()

            try:
                resp = requests.post(
                    USGS_3DEP_URL,
                    data={
                        "geometry": geometry,
                        "geometryType": "esriGeometryMultipoint",
                        "returnFirstValueOnly": "true",
                        "interpolation": "RSP_BilinearInterpolation",
                        "f": "json",
                    },
                    timeout=USGS_3DEP_TIMEOUT,
                )
            except requests.RequestException:
                if attempt < max_attempts - 1:
                    backoff = RETRY_BACKOFF_SECONDS[attempt]
                    console.warn(
                        f"{log_prefix}batch "
                        f"{batch_index + 1}/{total_batches} transport "
                        f"error; waiting {backoff}s before retry "
                        f"{attempt + 2}/{max_attempts}..."
                    )
                    time.sleep(backoff)
                    continue
                raise

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < max_attempts - 1:
                    backoff = RETRY_BACKOFF_SECONDS[attempt]
                    console.warn(
                        f"{log_prefix}batch "
                        f"{batch_index + 1}/{total_batches} HTTP "
                        f"{resp.status_code}; waiting {backoff}s before "
                        f"retry {attempt + 2}/{max_attempts}..."
                    )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(
                    f"{log_prefix}3DEP HTTP {resp.status_code} after "
                    f"{max_attempts} attempts; skipping remaining "
                    f"elevation lookups for this build"
                )
            resp.raise_for_status()

            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError(
                    f"{log_prefix}3DEP returned non-JSON response: {resp.text[:200]}"
                ) from None
            if "error" in data:
                raise RuntimeError(f"{log_prefix}3DEP error: {data['error']}")
            result_data = data
            break

        # Response shape: {"samples": [{"locationId": 0, "value":
        # "287.5", "resolution": 1}, ...]}. Order may not match input
        # order; sort by locationId before splicing back in. ``value``
        # is a STRING (or "NoData" for out-of-coverage points).
        samples = result_data.get("samples") or []
        samples_sorted = sorted(samples, key=lambda s: int(s.get("locationId", 0)))

        for j, s in enumerate(samples_sorted):
            global_idx = i + j
            if global_idx >= len(valid_coords):
                break  # defensive — shouldn't happen
            value = s.get("value")
            if value is None or value == "NoData":
                elev_for_valid[global_idx] = None
            else:
                try:
                    elev_for_valid[global_idx] = float(value)
                except (TypeError, ValueError):
                    elev_for_valid[global_idx] = None

    # Splice the elevation results back in, preserving break-marker
    # positions so the gain-from-samples computation sees both the
    # within-segment delta chains and the explicit breaks between
    # them.
    out = list(coords_with_breaks)  # length-matched template
    for idx, elev in zip(valid_indices, elev_for_valid):
        out[idx] = elev
    return out


def _smooth_elevations(elevations, window):
    """Centered moving average over the elevation profile.

    Reduces lidar's residual noise (σ ≈ 0.3-0.5m on 1m bare-earth
    output) before differencing, which is the single biggest source
    of inflated elevation-gain numbers. A k-point average reduces
    noise variance by ~1/k while preserving any signal that spans
    more than `window` samples (~15 m at the default 5 m sampling × 3-
    point window) — covers every real-world MTB feature.

    Preserves None values (samples 3DEP couldn't resolve)
    rather than averaging them as zero. Endpoints use whatever window
    fits inside the array.
    """
    if window <= 1 or len(elevations) <= 2:
        return list(elevations)
    half = window // 2
    n = len(elevations)
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        vals = [v for v in elevations[lo:hi] if v is not None]
        if not vals:
            out.append(None)
        else:
            out.append(sum(vals) / len(vals))
    return out


def _gain_loss_from_samples(elevations):
    """Compute (gain, loss) across consecutive samples in one pass.

    Pipeline: smooth → difference → threshold → split positives from
    negatives. The threshold is symmetric: any delta with magnitude
    below ELEVATION_NOISE_THRESHOLD_M is treated as noise and dropped
    in BOTH directions; remaining deltas are accumulated as gain
    (positive) or loss (absolute value of negative). This preserves
    the directional asymmetry that lets the runtime show ``↑X / ↓Y``
    while applying the same noise rejection to both.

    Why both: for loops gain ≈ loss, but for one-way routes the
    asymmetry tells the rider whether they're looking at mostly
    climbing or mostly descending — without us having to claim we
    know which direction the route is intended to be ridden (we
    don't; OSM doesn't carry that signal for MTB relations).

    None samples (where 3DEP didn't return an elevation, e.g. a
    coordinate outside US coverage) reset the running comparison so
    we don't compute a fake delta across the gap.

    Returns ``(gain_m, loss_m)`` as integer meters, both ≥ 0.
    """
    smoothed = _smooth_elevations(elevations, ELEVATION_SMOOTH_WINDOW)
    gain = 0.0
    loss = 0.0
    prev = None
    for e in smoothed:
        if e is None:
            prev = None
            continue
        if prev is not None:
            delta = e - prev
            if delta >= ELEVATION_NOISE_THRESHOLD_M:
                gain += delta
            elif -delta >= ELEVATION_NOISE_THRESHOLD_M:
                loss += -delta
        prev = e
    return round(gain), round(loss)


def compute_elevations(trails_geojson, cache_dir):
    """Return ``{route_id: (gain_m_int, loss_m_int)}`` (sparse — missing
    entries for routes whose elevation couldn't be computed).

    Uses USGS 3DEP getSamples. Per-route results are cached to
    ``cache/route_stats/elev_<route_id>_<coord_hash>.json`` so a
    rebuild that doesn't change route geometry hits the cache and
    skips the network entirely. Cache files store BOTH gain and
    loss; entries written by an older version (gain only) are
    treated as cache misses and refetched so the runtime can
    consistently render ``↑X / ↓Y``.

    On unrecoverable API failure, logs a warning and stops trying to
    fetch — already-cached results still flow through.
    """
    metadata = trails_geojson.get("metadata") or {}
    routes = metadata.get("routes") or {}
    features = trails_geojson.get("features") or []

    os.makedirs(os.path.join(cache_dir, "route_stats"), exist_ok=True)
    out = {}
    api_failed = False

    for route_id in routes.keys():
        rid_str = str(route_id)
        coord_lines = _coords_for_route(features, rid_str)
        if not coord_lines:
            continue
        sampled = _subsample_route(coord_lines, SAMPLE_INTERVAL_M, MAX_SAMPLES_PER_ROUTE)
        if len(sampled) < 2:
            continue

        coord_hash = _hash_coords(sampled)
        cache_path = _elev_cache_path(cache_dir, rid_str, coord_hash)
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                # Require BOTH fields for a cache hit. Old-format
                # entries (gain only) get treated as cache miss and
                # refetched.
                if (
                    isinstance(cached, dict)
                    and "elevation_gain_m" in cached
                    and "elevation_loss_m" in cached
                ):
                    out[rid_str] = (
                        int(cached["elevation_gain_m"]),
                        int(cached["elevation_loss_m"]),
                    )
                    continue
            except (OSError, ValueError, TypeError):
                pass  # corrupt cache; refetch.

        if api_failed:
            # Don't even try once we've hit a hard API failure in this build.
            continue

        try:
            elevations = _fetch_elevations_batched(sampled, log_prefix=f"route {rid_str}: ")
            gain, loss = _gain_loss_from_samples(elevations)
            out[rid_str] = (gain, loss)
            try:
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "elevation_gain_m": gain,
                            "elevation_loss_m": loss,
                            "samples": len(sampled),
                        },
                        fh,
                    )
            except OSError as e:
                console.warn(f"couldn't write elevation cache for route {rid_str}: {e}")
        except RuntimeError as e:
            # Persistent API failure — stop hitting the API for the
            # rest of this build.
            console.warn(f"{e}")
            api_failed = True
        except requests.RequestException as e:
            # Transport error after retries — log but keep going, the
            # next route might succeed (a per-batch transient error
            # could have been the cause).
            console.warn(f"route {rid_str} elevation fetch failed: {e}")

    return out


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def compute_and_attach(trails_geojson, config, cache_dir):
    """Compute enabled stats and attach them to trails_geojson in place.

    Reads ``show_route_distance`` and ``show_route_elevation`` from
    config. Writes ``distance_m``, ``elevation_gain_m``, and
    ``elevation_loss_m`` (when available) into ``metadata.routes[<id>]``.
    Returns True if anything was attached (caller writes back to disk).

    MUST run on canonical (pre-subway-expansion) geometry. The
    multi-mode subway pass replaces each truncated host feature with
    one full-length variant per active mode, all carrying the same
    ``route_id`` — computing on that output counts host geometry once
    per mode (a 4-mode map inflated one route ~4x). Guarded below.
    """
    want_distance = bool(config.get("show_route_distance"))
    want_elevation = bool(config.get("show_route_elevation"))

    for f in trails_geojson.get("features") or []:
        props = f.get("properties") or {}
        if props.get("isStub") or props.get("_subwayHostVariant"):
            raise ValueError(
                "compute_and_attach called on subway-expanded geometry "
                "(found isStub/_subwayHostVariant features). Stats must "
                "be computed on the canonical base or every multi-mode "
                "host route is counted once per mode."
            )

    metadata = trails_geojson.setdefault("metadata", {})
    routes = metadata.setdefault("routes", {})
    if not routes:
        return False

    # Even with both gates off we still need to walk the cleanup
    # branches below — a previous build may have left distance_m /
    # elevation_*_m fields on the routes, and the runtime would keep
    # rendering those stale values until they're explicitly removed.
    # Return early ONLY when there's nothing to do AND nothing stale
    # to clean up.
    if not (want_distance or want_elevation):
        has_stale = any(
            "distance_m" in info or "elevation_gain_m" in info or "elevation_loss_m" in info
            for info in routes.values()
        )
        if not has_stale:
            return False

    changed = False

    if want_distance:
        console.info("computing per-route distance...")
        distances = compute_distances(trails_geojson)
        for rid, dist in distances.items():
            if rid in routes and routes[rid].get("distance_m") != dist:
                routes[rid]["distance_m"] = dist
                changed = True
    else:
        # Strip stale values if previously set then disabled.
        for info in routes.values():
            if "distance_m" in info:
                del info["distance_m"]
                changed = True

    if want_elevation:
        console.info("computing per-route elevation gain + loss (via USGS 3DEP)...")
        elevations = compute_elevations(trails_geojson, cache_dir)
        # Strip stale entries on routes whose computation failed this
        # run so the runtime doesn't keep showing yesterday's
        # elevation when today's value is unknown. Both gain and loss
        # are written/cleared together — they're computed in one pass
        # and a partial state would be confusing in the UI.
        for rid, info in routes.items():
            new_val = elevations.get(rid)
            had_gain = "elevation_gain_m" in info
            had_loss = "elevation_loss_m" in info
            if new_val is None:
                if had_gain:
                    del info["elevation_gain_m"]
                    changed = True
                if had_loss:
                    del info["elevation_loss_m"]
                    changed = True
                continue
            new_gain, new_loss = new_val
            if info.get("elevation_gain_m") != new_gain:
                info["elevation_gain_m"] = new_gain
                changed = True
            if info.get("elevation_loss_m") != new_loss:
                info["elevation_loss_m"] = new_loss
                changed = True
    else:
        for info in routes.values():
            if "elevation_gain_m" in info:
                del info["elevation_gain_m"]
                changed = True
            if "elevation_loss_m" in info:
                del info["elevation_loss_m"]
                changed = True

    return changed
