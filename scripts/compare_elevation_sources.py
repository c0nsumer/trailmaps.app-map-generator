"""Compare per-route elevation gain/loss from opentopodata vs USGS 3DEP.

Standalone investigation tool — does NOT touch the build pipeline, the
runtime, the production cache, or trails.geojson. Reads built
trails.geojson files, samples each route, queries both elevation
sources at the SAME points, runs both through the SAME smoothing +
threshold pipeline, and prints a side-by-side comparison.

Why this exists:
  We currently use opentopodata.org's free SRTM30m endpoint for
  per-route elevation stats. SRTM30m is ~25 yr old global 30m DEM.
  USGS 3DEP serves multi-resolution lidar-derived data: 1m where
  available (most of the US, including all UP MI counties), falling
  through 10m and 30m. Switching to 3DEP would in principle give
  better numbers in lidar-covered areas — but "better" is a hypothesis
  to validate, not assume. This script produces the data that lets
  the migration decision be made on evidence rather than analysis.

What it CAN tell you:
  - Whether the two sources agree within X% per route
  - The distribution of disagreement across maps and route types
  - Loop self-consistency (gain ≈ loss for closed loops) per source
  - Per-point resolution mix from 3DEP (proves whether you're getting
    1m lidar or just the 10m/30m fallback in your area)

What it CANNOT tell you:
  - Which source is "right" — both are model estimates of the
    underlying landform; ground truth needs Garmin barometric tracks
    or trail-association published profiles, which we don't have.
  - Whether dense sampling would help — that's a separate experiment.
    See scripts/compute_route_stats.py docstring for the noise math.

Usage:
  # Compare a single map (uses build/<slug>/trails.geojson by default)
  python scripts/compare_elevation_sources.py --config configs/example/example.yaml

  # Compare every built map
  python scripts/compare_elevation_sources.py --all

  # Override sampling/smoothing/threshold params (defaults match the
  # production compute_route_stats values)
  python scripts/compare_elevation_sources.py --all --spacing 50 --smoothing 3 --threshold 2.0

  # Limit to one source (useful for tuning one side without re-running both)
  python scripts/compare_elevation_sources.py --config configs/example/example.yaml --source 3dep

  # Bypass the comparison cache (force re-fetch from APIs)
  python scripts/compare_elevation_sources.py --all --no-cache

Cache:
  cache/comparison/<source>_<route_id>_<coord_hash>.json
  Stores raw fetched elevations + per-point resolutions (3DEP only).
  Re-running with the same spacing hits the cache and skips API calls;
  re-running with different smoothing/threshold params reuses cached
  elevations and just re-runs the post-processing. Different spacing
  changes the coord hash and triggers re-fetch.

  Lives in its own directory so that if anything goes wrong with this
  script's caching, the production opentopodata cache in
  cache/route_stats/ is untouched.

Rate limiting & politeness:
  - opentopodata: 1 req/sec, 100 pts/req, retry-with-backoff on 429
  - USGS 3DEP: no documented limit, but it's a public ArcGIS service;
    we cap at 1 req/sec across both sources combined and use
    2000-pt batches.

  A typical 7-map RAMBA-class run is ~70 routes × ~100 points each
  per source = ~70 batches of opentopodata (1 req/route) + ~70 batches
  of 3DEP (1 req/route) = 140 API calls total at 1/sec ≈ 2.5 min.
  Most runs after the first hit cache and finish in seconds.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict

import requests
import yaml

# Reuse the production helpers — the comparison should use the same
# subsampling logic the build uses, so any disagreement is purely about
# the elevation data, not the sampling.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compute_route_stats import (  # noqa: E402
    MAX_SAMPLES_PER_ROUTE,
    _coords_for_route,
    _haversine_m,
    _subsample_route,
)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# OpenTopoData (current production source)
OPENTOPO_URL = "https://api.opentopodata.org/v1/srtm30m"
OPENTOPO_BATCH = 100
OPENTOPO_TIMEOUT = 30

# USGS 3DEP — multi-resolution mosaic via ArcGIS ImageServer.
# getSamples accepts a multipoint geometry and returns elevation +
# source resolution at each point.
USGS_3DEP_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
)
USGS_3DEP_BATCH = 2000  # service hard limit per request
USGS_3DEP_TIMEOUT = 60  # service can be slow under load

# Inter-request delay (applies across BOTH sources — the goal is to
# be polite to both APIs without burning real time).
INTER_REQUEST_DELAY_S = 1.0

# Backoff for 429 / 5xx (same shape as production)
RETRY_BACKOFF_SECONDS = [60, 90, 120]

# Module-scope rate-limit token (across all sources)
_last_api_call = 0.0


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


def _hash_coords_with_spacing(coords_with_breaks, spacing_m):
    """Cache key for fetched elevations.

    Identical to compute_route_stats._hash_coords but also incorporates
    the sample spacing — different spacings produce different point
    sets, so they must not collide in cache.
    """
    h = hashlib.sha1()
    h.update(f"spacing={spacing_m}|".encode())
    for item in coords_with_breaks:
        if item is None:
            h.update(b"|BREAK|")
            continue
        lon, lat = item
        h.update(f"{lon:.6f},{lat:.6f}|".encode())
    return h.hexdigest()[:16]


def _cache_path(cache_dir, source, route_id, coord_hash):
    return os.path.join(
        cache_dir,
        "comparison",
        f"{source}_{route_id}_{coord_hash}.json",
    )


def _load_cached(cache_dir, source, route_id, coord_hash):
    path = _cache_path(cache_dir, source, route_id, coord_hash)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        elevations = data.get("elevations")
        resolutions = data.get("resolutions")
        if not isinstance(elevations, list):
            return None
        return elevations, resolutions
    except (OSError, ValueError, TypeError):
        return None


def _save_cached(cache_dir, source, route_id, coord_hash, elevations, resolutions):
    path = _cache_path(cache_dir, source, route_id, coord_hash)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "source": source,
                    "elevations": elevations,
                    "resolutions": resolutions,
                },
                fh,
            )
    except OSError as e:
        print(f"  warn: couldn't write cache for {source}/{route_id}: {e}")


# ----------------------------------------------------------------------
# Rate-limited fetch helpers
# ----------------------------------------------------------------------


def _wait_for_rate_limit():
    """Sleep enough to ensure ≥INTER_REQUEST_DELAY_S since the last call."""
    global _last_api_call
    elapsed = time.time() - _last_api_call
    if elapsed < INTER_REQUEST_DELAY_S:
        time.sleep(INTER_REQUEST_DELAY_S - elapsed)
    _last_api_call = time.time()


# ----------------------------------------------------------------------
# OpenTopoData
# ----------------------------------------------------------------------


def _fetch_opentopo(coords_with_breaks, log_prefix=""):
    """Fetch elevations from opentopodata for valid coords.

    Returns (elevations_with_breaks, resolutions_with_breaks) where
    resolutions is all None (opentopodata doesn't expose resolution).

    Raises RuntimeError on unrecoverable rate-limit; bubbles up
    transport errors as requests.RequestException.
    """
    valid_indices = [i for i, item in enumerate(coords_with_breaks) if item is not None]
    valid_coords = [coords_with_breaks[i] for i in valid_indices]

    elev_for_valid = []
    total_batches = (len(valid_coords) + OPENTOPO_BATCH - 1) // OPENTOPO_BATCH
    for batch_index, i in enumerate(range(0, len(valid_coords), OPENTOPO_BATCH)):
        batch = valid_coords[i : i + OPENTOPO_BATCH]
        # opentopodata wants "lat,lon|lat,lon|..."
        locations = "|".join(f"{lat},{lon}" for lon, lat in batch)

        max_attempts = len(RETRY_BACKOFF_SECONDS) + 1
        result_data = None
        for attempt in range(max_attempts):
            _wait_for_rate_limit()
            resp = requests.get(
                OPENTOPO_URL,
                params={"locations": locations},
                timeout=OPENTOPO_TIMEOUT,
            )
            if resp.status_code == 429:
                if attempt < max_attempts - 1:
                    backoff = RETRY_BACKOFF_SECONDS[attempt]
                    print(
                        f"{log_prefix}opentopo batch "
                        f"{batch_index + 1}/{total_batches} rate-limited; "
                        f"waiting {backoff}s before retry "
                        f"{attempt + 2}/{max_attempts}..."
                    )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(
                    f"{log_prefix}opentopodata rate-limited after {max_attempts} attempts"
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                raise RuntimeError(
                    f"{log_prefix}opentopodata status={data.get('status')!r}: {data.get('error')}"
                )
            result_data = data
            break

        for r in result_data.get("results") or []:
            elev = r.get("elevation")
            elev_for_valid.append(float(elev) if elev is not None else None)

    out_elev = list(coords_with_breaks)
    out_res = list(coords_with_breaks)
    for idx, elev in zip(valid_indices, elev_for_valid):
        out_elev[idx] = elev
        out_res[idx] = None  # opentopodata doesn't expose resolution
    # Replace coord placeholders that are still [lon, lat] with None
    # (this happens for the indices that ARE coords; we set elev/res
    # above, so just normalize the segment break markers).
    for i, item in enumerate(coords_with_breaks):
        if item is None:
            out_elev[i] = None
            out_res[i] = None
    return out_elev, out_res


# ----------------------------------------------------------------------
# USGS 3DEP
# ----------------------------------------------------------------------


def _fetch_usgs_3dep(coords_with_breaks, log_prefix=""):
    """Fetch elevations from USGS 3DEP getSamples.

    Returns (elevations_with_breaks, resolutions_with_breaks). The
    resolution at each point indicates which underlying source raster
    served the value (1, 10, 30 meters typically).

    Points outside the US return no value (handled as None elevation
    + None resolution).

    Raises RuntimeError on unrecoverable failure; bubbles up transport
    errors as requests.RequestException.
    """
    valid_indices = [i for i, item in enumerate(coords_with_breaks) if item is not None]
    valid_coords = [coords_with_breaks[i] for i in valid_indices]

    elev_for_valid = [None] * len(valid_coords)
    res_for_valid = [None] * len(valid_coords)

    total_batches = (len(valid_coords) + USGS_3DEP_BATCH - 1) // USGS_3DEP_BATCH
    for batch_index, i in enumerate(range(0, len(valid_coords), USGS_3DEP_BATCH)):
        batch = valid_coords[i : i + USGS_3DEP_BATCH]
        # ArcGIS multipoint geometry — note: points in [x,y] = [lon,lat]
        geometry = json.dumps(
            {
                "points": [[lon, lat] for lon, lat in batch],
                "spatialReference": {"wkid": 4326},
            }
        )

        max_attempts = len(RETRY_BACKOFF_SECONDS) + 1
        result_data = None
        for attempt in range(max_attempts):
            _wait_for_rate_limit()
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
                    print(
                        f"{log_prefix}3DEP batch "
                        f"{batch_index + 1}/{total_batches} transport error; "
                        f"waiting {backoff}s before retry..."
                    )
                    time.sleep(backoff)
                    continue
                raise

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < max_attempts - 1:
                    backoff = RETRY_BACKOFF_SECONDS[attempt]
                    print(
                        f"{log_prefix}3DEP batch "
                        f"{batch_index + 1}/{total_batches} HTTP "
                        f"{resp.status_code}; waiting {backoff}s before "
                        f"retry {attempt + 2}/{max_attempts}..."
                    )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(
                    f"{log_prefix}3DEP HTTP {resp.status_code} after {max_attempts} attempts"
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

        # Response shape: {"samples": [{"locationId": 0, "value": "287.5",
        # "resolution": 1}, ...]}
        # Order may not match input order; sort by locationId.
        samples = result_data.get("samples") or []
        samples_sorted = sorted(samples, key=lambda s: int(s.get("locationId", 0)))

        # Index into the batch's slice of the global valid arrays.
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
            res = s.get("resolution")
            try:
                res_for_valid[global_idx] = float(res) if res is not None else None
            except (TypeError, ValueError):
                res_for_valid[global_idx] = None

    out_elev = list(coords_with_breaks)
    out_res = list(coords_with_breaks)
    for idx, elev, res in zip(valid_indices, elev_for_valid, res_for_valid):
        out_elev[idx] = elev
        out_res[idx] = res
    for i, item in enumerate(coords_with_breaks):
        if item is None:
            out_elev[i] = None
            out_res[i] = None
    return out_elev, out_res


# ----------------------------------------------------------------------
# Stats pipeline (parameterized — same shape as production)
# ----------------------------------------------------------------------


def _smooth(elevations, window):
    """Centered moving average; preserves None values as gaps."""
    if window <= 1 or len(elevations) <= 2:
        return list(elevations)
    half = window // 2
    n = len(elevations)
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        vals = [v for v in elevations[lo:hi] if v is not None]
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def _gain_loss(elevations, smoothing, threshold):
    """Smooth → difference → threshold → split positives from negatives."""
    smoothed = _smooth(elevations, smoothing)
    gain = 0.0
    loss = 0.0
    prev = None
    for e in smoothed:
        if e is None:
            prev = None
            continue
        if prev is not None:
            delta = e - prev
            if delta >= threshold:
                gain += delta
            elif -delta >= threshold:
                loss += -delta
        prev = e
    return round(gain), round(loss)


def _resolution_summary(resolutions):
    """Return a dict {resolution_meters: pct_of_points}.

    Only counts non-None entries (skips break markers and out-of-coverage).
    """
    valid = [r for r in resolutions if r is not None]
    if not valid:
        return {}
    out = defaultdict(int)
    for r in valid:
        out[r] += 1
    total = len(valid)
    return {k: round(100 * v / total) for k, v in sorted(out.items())}


# ----------------------------------------------------------------------
# Per-route comparison
# ----------------------------------------------------------------------


def _route_length_m(coord_lines):
    total = 0.0
    for line in coord_lines:
        for (lon1, lat1), (lon2, lat2) in zip(line, line[1:]):
            total += _haversine_m(lon1, lat1, lon2, lat2)
    return total


def compare_route(route_id, route_info, features, params, cache_dir, sources, no_cache):
    """Compare opentopo + 3DEP for a single route.

    Returns a dict:
        {
          "route_id": str,
          "name": str,
          "length_m": float,
          "samples": int,
          "results": {
            "opentopo": {"gain": int, "loss": int, "resolutions": {30:100}},
            "3dep":     {"gain": int, "loss": int, "resolutions": {1:92, 10:8}},
          }
        }
    Missing source entries indicate that source failed; "results" is
    always present.
    """
    coord_lines = _coords_for_route(features, route_id)
    length_m = _route_length_m(coord_lines)
    if not coord_lines:
        return {
            "route_id": route_id,
            "name": route_info.get("name", ""),
            "length_m": 0.0,
            "samples": 0,
            "results": {},
        }

    sampled = _subsample_route(coord_lines, params["spacing"], MAX_SAMPLES_PER_ROUTE)
    valid_count = sum(1 for s in sampled if s is not None)
    if valid_count < 2:
        return {
            "route_id": route_id,
            "name": route_info.get("name", ""),
            "length_m": length_m,
            "samples": valid_count,
            "results": {},
        }

    coord_hash = _hash_coords_with_spacing(sampled, params["spacing"])
    log_prefix = f"  {route_info.get('name', route_id)[:30]:32s} "

    results = {}

    fetchers = {
        "opentopo": _fetch_opentopo,
        "3dep": _fetch_usgs_3dep,
    }
    for src in sources:
        cached = None if no_cache else _load_cached(cache_dir, src, route_id, coord_hash)
        if cached is not None:
            elevations, resolutions = cached
            cache_marker = " [cached]"
        else:
            try:
                print(f"{log_prefix}fetching {src}...", flush=True)
                elevations, resolutions = fetchers[src](sampled, log_prefix=log_prefix)
                _save_cached(cache_dir, src, route_id, coord_hash, elevations, resolutions)
                cache_marker = ""
            except (RuntimeError, requests.RequestException) as e:
                print(f"{log_prefix}{src} FAILED: {e}")
                continue

        gain, loss = _gain_loss(elevations, params["smoothing"], params["threshold"])
        res_mix = _resolution_summary(resolutions)
        results[src] = {"gain": gain, "loss": loss, "resolutions": res_mix}
        print(f"{log_prefix}{src:8s}: ↑{gain}m ↓{loss}m{cache_marker}")

    return {
        "route_id": route_id,
        "name": route_info.get("name", route_id),
        "length_m": length_m,
        "samples": valid_count,
        "results": results,
    }


# ----------------------------------------------------------------------
# Output formatting
# ----------------------------------------------------------------------


def _fmt_dist_mi(meters):
    if meters is None or meters <= 0:
        return "—"
    mi = meters / 1609.344
    return f"{mi:.2f} mi"


def _fmt_elev_ft(meters):
    if meters is None:
        return "—"
    ft = meters * 3.28084
    return f"{int(round(ft))} ft"


def _fmt_gain_loss(gain_m, loss_m):
    return f"↑{_fmt_elev_ft(gain_m)} ↓{_fmt_elev_ft(loss_m)}"


def _fmt_res_mix(res_mix):
    if not res_mix:
        return "—"
    return ", ".join(f"{int(k)}m:{v}%" for k, v in sorted(res_mix.items()))


def _loop_consistency(gain, loss):
    """Internal-consistency metric: min/max — 1.0 means perfectly balanced.

    For a closed loop, the rider returns to start, so gain ≈ loss is
    physically required. A value of 1.0 means the source is internally
    consistent for that route. Lower values indicate either:
      - The route isn't a loop (one-way / lollipop)
      - Sampling/integration error
      - Source noise
    """
    if gain == 0 or loss == 0:
        return None
    return min(gain, loss) / max(gain, loss)


def print_per_route_table(per_map_results, sources):
    """Big per-route table across all maps."""
    has_both = "opentopo" in sources and "3dep" in sources
    print()
    print("=" * 110)
    print("PER-ROUTE COMPARISON")
    print("=" * 110)

    headers = ["Map", "Route", "Length"]
    if "opentopo" in sources:
        headers.append("OpenTopoData")
    if "3dep" in sources:
        headers.append("USGS 3DEP")
    if has_both:
        headers.extend(["Δ gain", "Δ %"])
    if "3dep" in sources:
        headers.append("3DEP res")
    print(("  " + "{:<10s} {:<28s} {:>9s} ").format(*headers[:3]), end="")
    col_widths = []
    if "opentopo" in sources:
        print("{:<22s} ".format("OpenTopoData"), end="")
        col_widths.append(22)
    if "3dep" in sources:
        print("{:<22s} ".format("USGS 3DEP"), end="")
        col_widths.append(22)
    if has_both:
        print("{:>9s} {:>7s} ".format("Δ gain", "Δ %"), end="")
    if "3dep" in sources:
        print("{:<14s}".format("3DEP res"), end="")
    print()
    print("  " + "-" * 108)

    for map_slug, route_results in per_map_results.items():
        for r in route_results:
            results = r.get("results", {})
            if not results:
                continue  # skip routes where neither source returned

            row = [
                map_slug,
                (r["name"] or r["route_id"])[:28],
                _fmt_dist_mi(r["length_m"]),
            ]
            print("  {:<10s} {:<28s} {:>9s} ".format(*row), end="")

            if "opentopo" in sources:
                ot = results.get("opentopo")
                cell = _fmt_gain_loss(ot["gain"], ot["loss"]) if ot else "(failed)"
                print(f"{cell:<22s} ", end="")
            if "3dep" in sources:
                td = results.get("3dep")
                cell = _fmt_gain_loss(td["gain"], td["loss"]) if td else "(failed)"
                print(f"{cell:<22s} ", end="")

            if has_both and "opentopo" in results and "3dep" in results:
                ot_gain = results["opentopo"]["gain"]
                td_gain = results["3dep"]["gain"]
                delta = td_gain - ot_gain
                delta_ft = round(delta * 3.28084)
                pct = (100 * delta / ot_gain) if ot_gain else 0
                sign = "+" if delta >= 0 else ""
                print(
                    "{:>9s} {:>7s} ".format(
                        f"{sign}{delta_ft} ft",
                        f"{sign}{pct:.0f}%",
                    ),
                    end="",
                )
            elif has_both:
                print("{:>9s} {:>7s} ".format("—", "—"), end="")

            if "3dep" in sources:
                td = results.get("3dep")
                if td:
                    print("{:<14s}".format(_fmt_res_mix(td["resolutions"])), end="")
                else:
                    print("{:<14s}".format("—"), end="")
            print()


def print_aggregate(per_map_results, sources):
    """Aggregate stats per map + global."""
    has_both = "opentopo" in sources and "3dep" in sources
    print()
    print("=" * 110)
    print("AGGREGATE STATISTICS")
    print("=" * 110)

    all_routes = []
    for route_results in per_map_results.values():
        all_routes.extend(route_results)

    # Per-map breakdown
    print()
    print(f"  {'Map':<14s} {'Routes':>7s} ", end="")
    if has_both:
        print(f"{'Mean Δ%':>9s} {'Median Δ%':>11s} ", end="")
    if "opentopo" in sources:
        print(f"{'OT loop≈':>10s} ", end="")
    if "3dep" in sources:
        print(f"{'3DEP loop≈':>11s} ", end="")
    print()
    print("  " + "-" * 70)

    for map_slug, route_results in per_map_results.items():
        usable = [r for r in route_results if r.get("results")]
        deltas = []
        ot_loops = []
        td_loops = []
        for r in usable:
            results = r["results"]
            if has_both and "opentopo" in results and "3dep" in results:
                ot_g = results["opentopo"]["gain"]
                td_g = results["3dep"]["gain"]
                if ot_g > 0:
                    deltas.append(100 * (td_g - ot_g) / ot_g)
            if "opentopo" in results:
                lc = _loop_consistency(results["opentopo"]["gain"], results["opentopo"]["loss"])
                if lc is not None:
                    ot_loops.append(lc)
            if "3dep" in results:
                lc = _loop_consistency(results["3dep"]["gain"], results["3dep"]["loss"])
                if lc is not None:
                    td_loops.append(lc)

        print(f"  {map_slug:<14s} {len(usable):>7d} ", end="")
        if has_both:
            mean_d = sum(deltas) / len(deltas) if deltas else 0
            med_d = sorted(deltas)[len(deltas) // 2] if deltas else 0
            sign_m = "+" if mean_d >= 0 else ""
            sign_md = "+" if med_d >= 0 else ""
            print(f"{sign_m}{mean_d:>7.0f}% {sign_md}{med_d:>9.0f}% ", end="")
        if "opentopo" in sources:
            avg_lc = sum(ot_loops) / len(ot_loops) if ot_loops else 0
            print(f"{avg_lc * 100:>9.0f}% ", end="")
        if "3dep" in sources:
            avg_lc = sum(td_loops) / len(td_loops) if td_loops else 0
            print(f"{avg_lc * 100:>10.0f}% ", end="")
        print()

    # Global summary
    if has_both:
        all_deltas = []
        outliers = []
        for r in all_routes:
            results = r.get("results", {})
            if "opentopo" in results and "3dep" in results:
                ot_g = results["opentopo"]["gain"]
                td_g = results["3dep"]["gain"]
                if ot_g > 0:
                    pct = 100 * (td_g - ot_g) / ot_g
                    all_deltas.append(pct)
                    if abs(pct) > 30:
                        outliers.append((r["name"], pct, ot_g, td_g))

        print()
        print("  Global summary (across all maps):")
        if all_deltas:
            mean_d = sum(all_deltas) / len(all_deltas)
            sorted_d = sorted(all_deltas)
            med_d = sorted_d[len(sorted_d) // 2]
            sign_m = "+" if mean_d >= 0 else ""
            sign_md = "+" if med_d >= 0 else ""
            print(f"    Total comparable routes:   {len(all_deltas)}")
            print(f"    Mean   Δ gain (3DEP - OT): {sign_m}{mean_d:.1f}%")
            print(f"    Median Δ gain (3DEP - OT): {sign_md}{med_d:.1f}%")
            print(f"    Outliers (|Δ| > 30%):      {len(outliers)}")
            for name, pct, ot_g, td_g in outliers[:10]:
                sign = "+" if pct >= 0 else ""
                print(
                    f"      {name[:30]:32s} {sign}{pct:>5.0f}%  "
                    f"(OT ↑{_fmt_elev_ft(ot_g)}, 3DEP ↑{_fmt_elev_ft(td_g)})"
                )
            if len(outliers) > 10:
                print(f"      ... ({len(outliers) - 10} more)")

    if "3dep" in sources:
        all_res = defaultdict(int)
        total_pts = 0
        for r in all_routes:
            results = r.get("results", {})
            td = results.get("3dep")
            if td:
                # res_mix is %, convert back to counts is messy; instead
                # accumulate weighted by sample count
                for res, pct in td["resolutions"].items():
                    all_res[res] += pct * r.get("samples", 0) / 100
                total_pts += r.get("samples", 0)
        if total_pts > 0:
            print()
            print("  3DEP resolution mix (weighted by sample count):")
            for res in sorted(all_res):
                pct = 100 * all_res[res] / total_pts
                print(f"    {int(res)}m: {pct:.1f}%")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_all_configs():
    """Return a list of all per-map config paths (excluding example)."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs")
    base = os.path.normpath(base)
    out = []
    for entry in sorted(os.listdir(base)):
        if entry == "example":
            continue
        full = os.path.join(base, entry)
        if not os.path.isdir(full):
            continue
        # Look for <slug>.yaml inside
        for fname in os.listdir(full):
            if fname.endswith(".yaml"):
                out.append(os.path.join(full, fname))
                break
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Compare opentopodata vs USGS 3DEP elevation per route.",
    )
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--config", help="Path to a single config YAML")
    src_group.add_argument("--all", action="store_true", help="Process every config in configs/")
    parser.add_argument(
        "--trails", help="Override trails.geojson path (default: build/<slug>/trails.geojson)"
    )
    parser.add_argument(
        "--spacing", type=float, default=50.0, help="Sample spacing in meters (default: 50)"
    )
    parser.add_argument(
        "--smoothing", type=int, default=3, help="Smoothing window size in samples (default: 3)"
    )
    parser.add_argument(
        "--threshold", type=float, default=2.0, help="Noise threshold in meters (default: 2.0)"
    )
    parser.add_argument(
        "--source",
        choices=["opentopo", "3dep", "both"],
        default="both",
        help="Which sources to query (default: both)",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Bypass the comparison cache (force re-fetch)"
    )
    parser.add_argument(
        "--cache-dir", default="cache", help="Cache root directory (default: cache)"
    )
    args = parser.parse_args()

    sources = ["opentopo", "3dep"] if args.source == "both" else [args.source]
    params = {
        "spacing": args.spacing,
        "smoothing": args.smoothing,
        "threshold": args.threshold,
    }

    print("Comparison parameters:")
    print(f"  spacing:   {params['spacing']:.0f} m")
    print(f"  smoothing: {params['smoothing']}-point centered moving average")
    print(f"  threshold: {params['threshold']:.1f} m")
    print(f"  sources:   {', '.join(sources)}")
    print(f"  cache:     {'BYPASSED' if args.no_cache else args.cache_dir}/comparison/")

    if args.config:
        config_paths = [args.config]
    else:
        config_paths = _find_all_configs()

    per_map_results = {}
    for config_path in config_paths:
        config = _load_config(config_path)
        slug = config.get("slug") or os.path.splitext(os.path.basename(config_path))[0]

        if args.trails:
            trails_path = args.trails
        else:
            trails_path = os.path.join("build", slug, "trails.geojson")

        if not os.path.isfile(trails_path):
            print(f"\n[{slug}] SKIP — {trails_path} not found (build the map first)")
            continue

        print(f"\n[{slug}] Loading {trails_path}...")
        with open(trails_path, encoding="utf-8") as f:
            trails = json.load(f)
        routes = (trails.get("metadata") or {}).get("routes") or {}
        features = trails.get("features") or []
        print(f"[{slug}] {len(routes)} routes, {len(features)} features")

        results = []
        for route_id, info in routes.items():
            results.append(
                compare_route(
                    str(route_id),
                    info,
                    features,
                    params,
                    args.cache_dir,
                    sources,
                    args.no_cache,
                )
            )
        per_map_results[slug] = results

    if not per_map_results:
        print("\nNo maps processed. Did you build any first?")
        sys.exit(1)

    print_per_route_table(per_map_results, sources)
    print_aggregate(per_map_results, sources)
    print()


if __name__ == "__main__":
    main()
