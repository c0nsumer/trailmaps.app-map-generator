"""Compute per-route distance and elevation gain at build time.

Two stats, both gated by config:

  show_distance: true   →  walks the GeoJSON, sums haversine
                           segment lengths per route. No data
                           dependency; always runs when enabled.

  show_elevation: true  →  samples elevation along each route
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
in the viewer's chosen units.

Caching:
  Elevation queries are cached in ``cache/route_stats/`` keyed by a
  stable hash of the sampled coordinates. A rebuild that doesn't
  change route geometry hits the cache and skips the network entirely.
  Distance is cheap and not cached (it'd just add bookkeeping cost).

Why USGS 3DEP: SRTM30m over-reports gain in forested terrain, because
its radar reads canopy height as terrain. 3DEP serves lidar bare-earth
data at 1m where this framework's maps live, with a 10m / 30m fallback
elsewhere in the US. It needs no API key and has no daily quota.
Out-of-coverage (non-US) points return ``NoData`` and count as missing
samples, so non-US trails omit ``elevation_*_m`` and the runtime renders
the route without stats.

The gain/loss algorithm is documented in ``_gain_loss_from_samples``;
the segment-chaining rationale is in ``_chain_segments``.

Failure modes (all non-fatal):
  - 3DEP API error / timeout              → log warning, skip
    elevation for that route.
  - Out-of-coverage (non-US) point        → treated as a missing sample.
  - Empty route (no coords)               → skip; both stats omitted.
  - HTTP 502/503 (transient overload)     → retry with backoff
    (RETRY_BACKOFF_SECONDS) per batch. After all retries fail on one
    batch, the build stops trying for the remaining routes and lets the
    cache fill in on a later run.
"""

import hashlib
import json
import os
import time

import cache_manifest
import console
import requests
from enrichment import resolve_color_modes
from geodesy import haversine_m as _haversine_m


def _coords_for_route(features, route_id):
    """Concatenate every segment's coords for one route, in feature order.

    Filters by ``route_id`` only, NOT by ``shared_routes``. When a way
    belongs to several relations, build_geojson emits one feature per
    parent relation, each carrying its own ``route_id``, so the
    ``route_id`` filter already finds every way of a route exactly once.
    Also matching ``shared_routes`` would double-count shared geometry:
    RAMBA's Ranger Loop reported 4.31 mi against 1.74 mi measured in
    JOSM.

    Segments come back in feature order, since a route has no canonical
    traversal through its junctions; the elevation pipeline handles the
    discontinuities with break markers.
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

# USGS 3D Elevation Program (3DEP) - public ArcGIS ImageServer that
# serves a multi-resolution DEM mosaic (1m lidar where available,
# falling through 10m and 30m). Free, no API key, no daily quota,
# supports up to 2000 points per request via getSamples.
USGS_3DEP_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
)
USGS_3DEP_BATCH = 2000  # service hard limit per request
USGS_3DEP_TIMEOUT = 60  # service can be slow under load

# Horizontal sampling spacing. 25m resolves every terrain feature a
# rider would notice while keeping API request counts modest. Spacing
# and the noise band are decoupled (see _gain_loss_from_samples), so it
# only trades API cost against horizontal resolution.
SAMPLE_INTERVAL_M = 25

# Hard cap on samples per route - bounds API cost on long routes. At
# 25m spacing, 2000 samples covers a 50km route fully. Routes longer
# than 50km get proportionally coarser sampling, which is fine because
# high-resolution detail on a 50km+ traverse isn't a useful signal for
# riders deciding whether to commit.
MAX_SAMPLES_PER_ROUTE = 2000

# Centered moving-average window over the elevation profile. 3 points at
# 25m spacing = 75m, which flattens residual lidar noise without
# smoothing real climbs (typically ≥100m horizontal). 1 disables it.
ELEVATION_SMOOTH_WINDOW = 3

# Hysteresis band for the gain/loss accumulator (see
# _gain_loss_from_samples). Lidar bare-earth has a vertical noise SD of
# ~0.3-0.5m, so a 1m band rejects noise while real grades still
# accumulate. Part of the elevation cache key.
ELEVATION_NOISE_THRESHOLD_M = 1.0

# Minimum spacing between requests across the whole build. 3DEP publishes
# no rate limit, but it is a public ArcGIS service.
INTER_REQUEST_DELAY_S = 1.0

# Backoff schedule for HTTP 5xx / transport-error retries. 3DEP's usual
# failure is a brief 502 burst that clears in seconds, so the early waits
# are short; the later ones cover slow recovery.
RETRY_BACKOFF_SECONDS = [5, 15, 60, 120]

# Timestamp of the last API call, so INTER_REQUEST_DELAY_S holds across
# routes. 0 means no prior call.
_last_api_call = 0.0


def _subsample_segment(line, target_interval_m, max_samples):
    """Subsample a single connected line at ~target_interval_m spacing.

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


def _chain_segments(coord_lines):
    """Orient and join segments whose endpoints touch into continuous
    chains, minimizing the number of segment-break markers downstream.

    OSM relations don't order their member ways, so a closed loop
    arrives as many disconnected pieces in arbitrary order and
    direction. Every remaining break hides the elevation change between
    two samples that ARE connected on the ground, and those hidden
    deltas don't cancel between gain and loss. Measured on RAMBA's
    Ranger Loop (9 segments): unchained gain/loss was 10/35 m; chained,
    the loop closes and gain equals loss.

    Greedy: seed a chain from the first unused segment, then repeatedly
    absorb any unused segment one of whose endpoints coincides with
    either end of the chain (reversing the segment as needed).
    Segments are only ever from ONE route, so a join is always a
    physically walkable continuation - at a junction shared by more
    than two of the route's segments, whichever continuation is
    absorbed first is still real terrain, and the leftover branch
    seeds its own chain. Deterministic: input order drives seeding
    and absorption.

    Endpoint matching is exact to 7 decimals (~1 cm) - segments from
    the same OSM way/node share coordinates bit-for-bit, so this is a
    node-identity check, not a proximity heuristic (two trails passing
    1 m apart must NOT join).

    Returns a new list of chains; input lines are not mutated.
    Degenerate (< 2 point) segments are dropped - they contribute no
    deltas.
    """

    def _ckey(pt):
        return (round(pt[0], 7), round(pt[1], 7))

    segs = [list(line) for line in coord_lines if len(line) >= 2]
    used = [False] * len(segs)
    chains = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        chain = list(segs[i])
        extended = True
        while extended:
            extended = False
            for j in range(len(segs)):
                if used[j]:
                    continue
                s = segs[j]
                if _ckey(chain[-1]) == _ckey(s[0]):
                    chain.extend(s[1:])
                elif _ckey(chain[-1]) == _ckey(s[-1]):
                    chain.extend(reversed(s[:-1]))
                elif _ckey(chain[0]) == _ckey(s[-1]):
                    chain[:0] = s[:-1]
                elif _ckey(chain[0]) == _ckey(s[0]):
                    chain[:0] = reversed(s[1:])
                else:
                    continue
                used[j] = True
                extended = True
        chains.append(chain)
    return chains


def _subsample_route(coord_lines, target_interval_m, max_samples):
    """Subsample a multi-segment route at ~target_interval_m spacing.

    Each segment is sampled independently, with a ``None`` marker between
    segments so deltas across a gap are dropped: the end of one segment
    and the start of the next are not connected terrain. Without the
    markers, typical RAMBA routes (20-100 transitions) inflated gain
    2-3x with phantom climbs.

    The ``max_samples`` budget splits across segments by length. Segments
    under one sample interval get just their endpoints.

    Returns ``[lon, lat]`` pairs and ``None`` markers, which must be
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
    """Stable hash of a coords list + sampling parameters - used as
    the elevation cache key.

    Accepts the segment-aware shape from _subsample_route: a list of
    [lon, lat] pairs interspersed with None markers for segment breaks.
    The hash includes the breaks (the literal "|BREAK|"), because they
    affect the computed gain. It also includes every constant the result
    depends on plus an algorithm version token, so tuning a constant or
    changing the algorithm invalidates the cache. A missing input would
    silently keep returning old numbers until the next --refresh.
    """
    h = hashlib.sha1()
    h.update(
        f"algo=2,si={SAMPLE_INTERVAL_M},mx={MAX_SAMPLES_PER_ROUTE},"
        f"sw={ELEVATION_SMOOTH_WINDOW},nt={ELEVATION_NOISE_THRESHOLD_M}||".encode()
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
    couldn't resolve it - out-of-coverage points return ``NoData``)
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
        # ArcGIS multipoint geometry - points in [x, y] = [lon, lat]
        # order, EPSG:4326 (WGS84).
        geometry = json.dumps(
            {
                "points": [[lon, lat] for lon, lat in batch],
                "spatialReference": {"wkid": 4326},
            }
        )

        # After the last retry on a batch we raise, so the caller can stop
        # trying for the rest of the build.
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
        # "287.5", "resolution": 1}, ...]}, ``value`` a STRING or
        # "NoData". The service may reorder or OMIT points, so place each
        # sample by its locationId: an enumeration index would shift
        # every elevation after a gap onto the wrong coordinate.
        for s in result_data.get("samples") or []:
            try:
                local_idx = int(s.get("locationId"))
            except (TypeError, ValueError):
                continue  # malformed sample - leave its point missing
            if not (0 <= local_idx < len(batch)):
                continue  # defensive - id outside this batch
            value = s.get("value")
            if value is None or value == "NoData":
                continue  # elev_for_valid entry stays None
            try:
                elev_for_valid[i + local_idx] = float(value)
            except (TypeError, ValueError):
                pass  # unparseable value - leave point missing

    out = list(coords_with_breaks)  # length-matched template
    for idx, elev in zip(valid_indices, elev_for_valid):
        out[idx] = elev
    return out


def _smooth_elevations(elevations, window):
    """Centered moving average over the elevation profile.

    Reduces residual lidar noise before differencing, the biggest source
    of inflated gain. A k-point average cuts noise variance by ~1/k while
    keeping any signal spanning more than `window` samples.

    None values are hard boundaries, at any window size. A None stays
    None, or _gain_loss_from_samples would compute a delta across
    disconnected points. The window never reaches ACROSS a None, because
    samples on opposite sides of a break are disconnected terrain.

    Endpoints (of the array or of a segment) use whatever window fits.
    """
    if window <= 1 or len(elevations) <= 2:
        return list(elevations)
    half = window // 2
    n = len(elevations)
    out = []
    for i in range(n):
        if elevations[i] is None:
            out.append(None)
            continue
        vals = [elevations[i]]
        for j in range(i - 1, max(0, i - half) - 1, -1):  # walk left to break
            if elevations[j] is None:
                break
            vals.append(elevations[j])
        for j in range(i + 1, min(n, i + half + 1)):  # walk right to break
            if elevations[j] is None:
                break
            vals.append(elevations[j])
        out.append(sum(vals) / len(vals))
    return out


def _gain_loss_from_samples(elevations):
    """Compute (gain, loss) with an anchor-based hysteresis accumulator,
    the "total ascent" scheme GPS head units use.

    Pipeline: smooth, then accumulate against an anchor. The anchor is
    the last committed elevation. When the profile moves at least
    ELEVATION_NOISE_THRESHOLD_M away from it, the ENTIRE movement
    commits to gain or loss and the anchor jumps to the current sample.
    Noise inside the band never commits, and a long gentle grade
    accumulates until it clears the band, so any grade counts
    independent of sampling density. Hysteresis is symmetric, so closed
    loops converge to gain ≈ loss. Thresholding each per-sample delta
    instead discards sub-threshold descents and shows ↑big / ↓small on
    a loop that climbs steeply and descends gently.

    Both are reported because OSM carries no ride direction for MTB
    relations, and on one-way routes the asymmetry tells the rider
    whether the route is mostly climbing or mostly descending.

    None samples (segment-break markers, and points 3DEP couldn't
    resolve) reset the anchor so no movement is committed across a
    gap - those points aren't physically connected terrain.

    Returns ``(gain_m, loss_m)`` as integer meters, both ≥ 0 - or
    ``None`` if no two connected valid samples existed (e.g. every
    sample was out-of-coverage NoData). Callers must treat None as
    "no data", NOT as a flat route: attaching (0, 0) would make a
    no-data route indistinguishable from a genuinely flat one.
    """
    smoothed = _smooth_elevations(elevations, ELEVATION_SMOOTH_WINDOW)
    gain = 0.0
    loss = 0.0
    anchor = None
    saw_connected_pair = False
    for e in smoothed:
        if e is None:
            anchor = None
            continue
        if anchor is None:
            anchor = e
            continue
        saw_connected_pair = True
        delta = e - anchor
        if delta >= ELEVATION_NOISE_THRESHOLD_M:
            gain += delta
            anchor = e
        elif -delta >= ELEVATION_NOISE_THRESHOLD_M:
            loss += -delta
            anchor = e
    if not saw_connected_pair:
        return None
    return round(gain), round(loss)


def compute_elevations(trails_geojson, cache_dir, route_ids=None):
    """Return ``{route_id: (gain_m_int, loss_m_int)}`` (sparse - missing
    entries for routes whose elevation couldn't be computed).

    Uses USGS 3DEP getSamples. Per-route results are cached to
    ``cache/route_stats/elev_<route_id>_<coord_hash>.json``. Entries
    without both gain and loss count as cache misses, so the runtime can
    always render ``↑X / ↓Y``.

    On unrecoverable API failure, logs a warning and stops trying to
    fetch - already-cached results still flow through.

    ``route_ids`` (a set of id strings) limits the walk to those routes;
    None means every route.
    """
    metadata = trails_geojson.get("metadata") or {}
    routes = metadata.get("routes") or {}
    features = trails_geojson.get("features") or []

    os.makedirs(os.path.join(cache_dir, "route_stats"), exist_ok=True)
    out = {}
    api_failed = False

    for route_id in routes.keys():
        rid_str = str(route_id)
        if route_ids is not None and rid_str not in route_ids:
            continue
        coord_lines = _chain_segments(_coords_for_route(features, rid_str))
        if not coord_lines:
            continue
        sampled = _subsample_route(coord_lines, SAMPLE_INTERVAL_M, MAX_SAMPLES_PER_ROUTE)
        if len(sampled) < 2:
            continue

        coord_hash = _hash_coords(sampled)
        cache_path = _elev_cache_path(cache_dir, rid_str, coord_hash)
        # Claim the path whether this turns out to be a hit, a fresh
        # write, or a failed fetch: it is this build's key either way,
        # and pruning tolerates claims for files that don't exist.
        cache_manifest.record(cache_path)
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                # A cached null gain marks a known no-data route (every
                # sample NoData, e.g. non-US terrain): omit stats without
                # re-asking the API on every build.
                if (
                    isinstance(cached, dict)
                    and "elevation_gain_m" in cached
                    and "elevation_loss_m" in cached
                ):
                    if cached["elevation_gain_m"] is None:
                        continue
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
            gain_loss = _gain_loss_from_samples(elevations)
            if gain_loss is None:
                # No two connected valid samples. Omit stats and cache the
                # no-data marker; attaching (0, 0) would show "↑0 / ↓0"
                # for a route we know nothing about.
                console.warn(f"route {rid_str}: no usable elevation data; stats omitted")
                gain, loss = None, None
            else:
                gain, loss = gain_loss
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
            # Persistent API failure - stop hitting the API for the
            # rest of this build.
            console.warn(f"{e}")
            api_failed = True
        except requests.RequestException as e:
            # Transport error after retries - log but keep going, the
            # next route might succeed (a per-batch transient error
            # could have been the cause).
            console.warn(f"route {rid_str} elevation fetch failed: {e}")

    return out


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def compute_and_attach(trails_geojson, config, cache_dir):
    """Compute enabled stats and attach them to trails_geojson in place.

    Reads ``show_distance`` and ``show_elevation`` from
    config. Writes ``distance_m``, ``elevation_gain_m``, and
    ``elevation_loss_m`` (when available) into ``metadata.routes[<id>]``.
    Returns True if anything was attached (caller writes back to disk).

    MUST run on canonical geometry: one feature per route per run of
    way. Geometry that repeats a route's ways inflates its stats.
    """
    want_distance = bool(config.get("show_distance"))
    # A difficulty-mode relation shows no per-route stats, so the build
    # must not spend minutes on 3DEP samples nothing reads (the validator
    # warns about the key when no route-mode relation exists; this is what
    # makes the warning true).
    want_elevation = bool(config.get("show_elevation"))

    metadata = trails_geojson.setdefault("metadata", {})
    routes = metadata.setdefault("routes", {})
    if not routes:
        return False

    # With both gates off, stale distance_m / elevation_*_m fields from a
    # previous build must still be removed, or the runtime keeps rendering
    # them. Return early only when nothing is stale.
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

    route_mode_ids = {
        rid
        for rid, mode in resolve_color_modes(
            config, routes.keys(), metadata.get("super_relation_expansions")
        ).items()
        if mode == "route"
    }
    if want_elevation and route_mode_ids:
        console.info("computing per-route elevation gain + loss (via USGS 3DEP)...")
        elevations = compute_elevations(trails_geojson, cache_dir, route_mode_ids)
        # Strip stale entries on routes whose computation failed this run.
        # Gain and loss are written or cleared together; a partial state
        # would confuse the UI.
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
