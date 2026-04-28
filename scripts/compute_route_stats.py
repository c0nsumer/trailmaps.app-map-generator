"""Compute per-route distance and elevation gain at build time.

Two stats, both gated by config:

  show_route_distance: true   →  walks the GeoJSON, sums haversine
                                 segment lengths per route. No data
                                 dependency; always runs when enabled.

  show_route_elevation: true  →  samples elevation along each route
                                 via opentopodata.org's free SRTM30m
                                 endpoint, computes positive gain.
                                 Requires network; degrades to
                                 no-data on failure or when the
                                 endpoint is unreachable.

Output: writes per-route stats into trails_geojson["metadata"]["routes"]
[<id>] as ``distance_m`` and ``elevation_gain_m`` (integers, meters).
The runtime reads those values and formats them per CONFIG.distanceUnits.

Caching:
  Elevation queries are cached in ``cache/route_stats/`` keyed by a
  stable hash of the sampled coordinates. A rebuild that doesn't
  change route geometry hits the cache and skips the network entirely.
  Distance is cheap and not cached (it'd just add bookkeeping cost).

Why opentopodata over rasterio + DEM:
  rasterio + a DEM file gives more flexibility but adds a heavyweight
  Python dep (rasterio, often finicky to install on macOS) and either
  requires bundling an SRTM tile per map or matching the build's
  fetch_terrain.py SRTM cache (which is only populated when terrain
  uses the SRTM path, not Mapterhorn). opentopodata.org is a free,
  rate-limited (1000 requests/day, 100 points/request) HTTP endpoint
  that handles SRTM30m sampling for us. Typical maps need 5–20
  requests per build. If the endpoint is ever unreachable, the build
  proceeds without elevation — distance still works, runtime degrades
  gracefully.

Failure modes (all non-fatal):
  - opentopodata API error / timeout  → log warning, skip elevation
    for that route; trails.geojson omits elevation_gain_m.
  - Empty route (no coords)           → skip; both stats omitted.
  - HTTP 429 (rate limit)             → automatic retry with backoff
    (60s, 90s, 120s) per batch. Build pauses to wait the API out
    rather than failing or requiring a manual rerun. Only after all
    retries are exhausted on a single batch do we give up and skip
    remaining routes — at that point the API is genuinely
    unavailable and continuing would just incur more waits with no
    payoff. The cache picks up where the build left off on the next
    run, so partial completion is still useful.
"""

import hashlib
import json
import math
import os
import time

import requests


# ----------------------------------------------------------------------
# Distance
# ----------------------------------------------------------------------

EARTH_R_M = 6371000.0  # mean Earth radius (matches templates/app.js
                       # haversineMeters). Off by ~0.1% from WGS84
                       # equatorial radius — irrelevant for trail-scale
                       # distances; consistency with the runtime
                       # off-screen indicator math matters more.


def _haversine_m(lon1, lat1, lon2, lat2):
    """Great-circle distance between two lng/lat points, in meters."""
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)
    return EARTH_R_M * 2 * math.asin(math.sqrt(a))


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

OPENTOPODATA_URL = "https://api.opentopodata.org/v1/srtm30m"
OPENTOPODATA_BATCH = 100        # API hard limit per request
OPENTOPODATA_TIMEOUT = 30       # seconds; the API can be slow

# Sampling interval matched to SRTM30m's effective resolution (~30m
# horizontal). Sampling tighter than the DEM resolves doesn't add real
# information — just more chances for adjacent-cell noise to integrate
# into phantom climbing. 50m is a comfortable multiple of the cell
# size and gives one sample per ~165 ft of trail.
SAMPLE_INTERVAL_M = 50

# Hard cap on samples per route — bounds the API cost on long routes.
# At 50m spacing, 400 samples covers a 20km route fully; longer routes
# get proportionally coarser sampling, which is fine because high-
# resolution detail on a 30km+ traverse isn't a useful signal for
# riders deciding whether to commit.
MAX_SAMPLES_PER_ROUTE = 400

# Smoothing window for the elevation profile, applied as a centered
# moving average before differencing. Three points reduces the
# adjacent-sample noise variance by ~1/3 (σ → σ/√3) while preserving
# any climb that spans more than ~150m horizontally — which covers
# every real-world MTB climb. Set to 1 to disable smoothing.
ELEVATION_SMOOTH_WINDOW = 3

# Minimum elevation delta between (post-smoothing) adjacent samples to
# count as real climbing. Below this, the delta is treated as DEM
# noise and ignored. SRTM30m's adjacent-cell noise SD is ~2-3m even
# after smoothing; thresholding at 2m rejects most of it. Real climbs
# at typical MTB grades (3-15%) over 50m intervals produce 1.5-7.5m
# of vertical gain, so this threshold only loses very gentle (<4%)
# rolling terrain — which most riders wouldn't call "climbing"
# anyway.
ELEVATION_NOISE_THRESHOLD_M = 2.0

INTER_REQUEST_DELAY_S = 1.0     # opentopodata's free tier asks for
                                # ≤1 req/sec across the whole build,
                                # not just within a single route. We
                                # track _last_api_call at module scope
                                # and sleep enough at the start of each
                                # request to guarantee the spacing —
                                # this matters because batches within
                                # a route end up <1s apart from the
                                # first batch of the NEXT route, which
                                # was the original cause of mid-build
                                # 429s after only 4-5 routes processed.

# Backoff schedule for HTTP 429 retries. opentopodata's per-second
# rate-limit window typically clears within ~60s; the longer waits
# are for when their daily quota or burst protection kicks in. After
# exhausting all retries on a single batch, we accept that the API
# is genuinely unavailable for this build and stop trying for
# subsequent routes (the build still completes; missing routes get
# picked up on the next build via cache).
RETRY_BACKOFF_SECONDS = [60, 90, 120]

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
    preserved through to ``_gain_from_samples``.
    """
    if not coord_lines:
        return []

    # Length of each segment (for proportional sample-budget allocation).
    seg_lens = []
    for line in coord_lines:
        if len(line) < 2:
            seg_lens.append(0.0)
            continue
        L = sum(_haversine_m(line[i][0], line[i][1],
                             line[i + 1][0], line[i + 1][1])
                for i in range(len(line) - 1))
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
    for i, (line, cap) in enumerate(zip(coord_lines, per_seg_cap)):
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
    """Stable hash of a coords list — used as the elevation cache key.

    Accepts the segment-aware shape from _subsample_route: a list of
    [lon, lat] pairs interspersed with None markers for segment breaks.
    The hash includes the breaks (encoded as the literal "|BREAK|")
    so that re-segmentation of a route changes the cache key — what
    we cache is gain-given-this-exact-sampling, and segment breaks
    affect the computed gain.
    """
    h = hashlib.sha1()
    for item in coords_with_breaks:
        if item is None:
            h.update(b"|BREAK|")
            continue
        lon, lat = item
        # 6 decimals = ~11 cm precision, well below SRTM's resolution
        h.update(f"{lon:.6f},{lat:.6f}|".encode("utf-8"))
    return h.hexdigest()[:16]


def _elev_cache_path(cache_dir, route_id, coord_hash):
    return os.path.join(
        cache_dir, "route_stats",
        f"elev_{route_id}_{coord_hash}.json",
    )


def _fetch_elevations_batched(coords_with_breaks, log_prefix=""):
    """Call opentopodata.org for the valid coords, preserving break markers.

    Input: a list whose items are either [lon, lat] pairs or None
    (segment break markers from _subsample_route).

    Output: a parallel list of the same length where each [lon, lat]
    is replaced by its elevation (float meters, or None if the API
    couldn't resolve it) and the None markers are passed through
    unchanged.

    Raises requests.RequestException on transport-level failure;
    raises RuntimeError on HTTP 429 so the caller can stop hammering.
    Other HTTP errors bubble up as the caller's responsibility.
    """
    # Indices of the valid coords in the input list — used to splice
    # API results back in alongside the preserved None markers.
    valid_indices = [i for i, item in enumerate(coords_with_breaks)
                     if item is not None]
    valid_coords = [coords_with_breaks[i] for i in valid_indices]

    global _last_api_call
    elev_for_valid = []
    total_batches = (len(valid_coords) + OPENTOPODATA_BATCH - 1) // OPENTOPODATA_BATCH
    for batch_index, i in enumerate(
            range(0, len(valid_coords), OPENTOPODATA_BATCH)):
        batch = valid_coords[i:i + OPENTOPODATA_BATCH]
        # opentopodata wants "lat,lon|lat,lon|..." (note: lat first).
        locations = "|".join(f"{lat},{lon}" for lon, lat in batch)

        # Per-batch retry loop with exponential-ish backoff on 429.
        # The first attempt has index 0 (no prior backoff); subsequent
        # attempts wait RETRY_BACKOFF_SECONDS[attempt-1] between
        # tries. After RETRY_BACKOFF_SECONDS is exhausted, we raise
        # RuntimeError — the caller treats this as "API genuinely
        # unavailable, stop trying for the rest of the build, but
        # let it complete with whatever was cached/computed."
        max_attempts = len(RETRY_BACKOFF_SECONDS) + 1
        result_data = None
        for attempt in range(max_attempts):
            # Sleep enough to guarantee ≥INTER_REQUEST_DELAY_S since
            # the last API call ANYWHERE in this build run — across
            # routes too, not just within one route's batches.
            elapsed = time.time() - _last_api_call
            if elapsed < INTER_REQUEST_DELAY_S:
                time.sleep(INTER_REQUEST_DELAY_S - elapsed)
            _last_api_call = time.time()
            resp = requests.get(
                OPENTOPODATA_URL,
                params={"locations": locations},
                timeout=OPENTOPODATA_TIMEOUT,
            )
            if resp.status_code == 429:
                if attempt < max_attempts - 1:
                    backoff = RETRY_BACKOFF_SECONDS[attempt]
                    print(
                        f"{log_prefix}batch {batch_index + 1}/{total_batches} "
                        f"rate-limited; waiting {backoff}s before retry "
                        f"{attempt + 2}/{max_attempts}..."
                    )
                    time.sleep(backoff)
                    continue
                # Exhausted retries — surface to the caller so the
                # rest of the build can stop hammering.
                raise RuntimeError(
                    f"{log_prefix}opentopodata rate-limited (HTTP 429) "
                    f"after {max_attempts} attempts; skipping remaining "
                    f"elevation lookups for this build"
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                raise RuntimeError(
                    f"{log_prefix}opentopodata returned status "
                    f"{data.get('status')!r}: {data.get('error')}"
                )
            result_data = data
            break

        for r in (result_data.get("results") or []):
            elev = r.get("elevation")
            elev_for_valid.append(float(elev) if elev is not None else None)

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

    Reduces SRTM's adjacent-cell noise (σ ≈ 2-3 m) before differencing,
    which is the single biggest source of inflated elevation-gain
    numbers. A k-point average reduces noise variance by ~1/k while
    preserving any signal that spans more than `window` samples
    (~150 m at the default 50 m sampling × 3-point window) — covers
    every real-world MTB climb.

    Preserves None values (samples opentopodata couldn't resolve)
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

    None samples (where opentopodata didn't return an elevation,
    e.g. a coordinate outside SRTM coverage) reset the running
    comparison so we don't compute a fake delta across the gap.

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

    Uses opentopodata.org SRTM30m. Per-route results are cached to
    ``cache/route_stats/elev_<route_id>_<coord_hash>.json`` so a
    rebuild that doesn't change route geometry hits the cache and
    skips the network entirely. Cache files store BOTH gain and
    loss; entries written by an older version (gain only) are
    treated as cache misses and refetched so the runtime can
    consistently render ``↑X / ↓Y``.

    On rate-limit (HTTP 429) or unrecoverable API failure, logs a
    warning and stops trying to fetch — already-cached results still
    flow through.
    """
    metadata = trails_geojson.get("metadata") or {}
    routes = metadata.get("routes") or {}
    features = trails_geojson.get("features") or []

    os.makedirs(os.path.join(cache_dir, "route_stats"), exist_ok=True)
    out = {}
    rate_limited = False

    for route_id in routes.keys():
        rid_str = str(route_id)
        coord_lines = _coords_for_route(features, rid_str)
        if not coord_lines:
            continue
        sampled = _subsample_route(coord_lines, SAMPLE_INTERVAL_M,
                                   MAX_SAMPLES_PER_ROUTE)
        if len(sampled) < 2:
            continue

        coord_hash = _hash_coords(sampled)
        cache_path = _elev_cache_path(cache_dir, rid_str, coord_hash)
        if os.path.isfile(cache_path):
            try:
                with open(cache_path) as fh:
                    cached = json.load(fh)
                # Require BOTH fields for a cache hit. Old-format
                # entries (gain only) get treated as cache miss and
                # refetched — the route geometry is the same, so the
                # gain value will match the cached one and we'll fill
                # in loss too.
                if (isinstance(cached, dict)
                        and "elevation_gain_m" in cached
                        and "elevation_loss_m" in cached):
                    out[rid_str] = (
                        int(cached["elevation_gain_m"]),
                        int(cached["elevation_loss_m"]),
                    )
                    continue
            except (OSError, ValueError, TypeError):
                pass  # corrupt cache; refetch.

        if rate_limited:
            # Don't even try once we've hit 429 in this build.
            continue

        try:
            elevations = _fetch_elevations_batched(
                sampled, log_prefix=f"  route {rid_str}: "
            )
            gain, loss = _gain_loss_from_samples(elevations)
            out[rid_str] = (gain, loss)
            try:
                with open(cache_path, "w") as fh:
                    json.dump({
                        "elevation_gain_m": gain,
                        "elevation_loss_m": loss,
                        "samples": len(sampled),
                    }, fh)
            except OSError as e:
                print(f"  warn: couldn't write elevation cache for "
                      f"route {rid_str}: {e}")
        except RuntimeError as e:
            # 429 or API status error — stop hitting the API.
            print(f"  warn: {e}")
            rate_limited = True
        except requests.RequestException as e:
            # Transport (timeout, DNS, connection refused) — log but
            # keep going, the next route might succeed.
            print(f"  warn: route {rid_str} elevation fetch failed: {e}")

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
    """
    want_distance = bool(config.get("show_route_distance"))
    want_elevation = bool(config.get("show_route_elevation"))

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
            "distance_m" in info
            or "elevation_gain_m" in info
            or "elevation_loss_m" in info
            for info in routes.values()
        )
        if not has_stale:
            return False

    changed = False

    if want_distance:
        print("  computing per-route distance...")
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
        print("  computing per-route elevation gain + loss "
              "(via opentopodata)...")
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
