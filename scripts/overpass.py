"""Shared Overpass API query helper with caching and retry."""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# Single canonical Overpass endpoint. The bare `overpass-api.de` host
# load-balances across the lz4 and z backends, which is what we want; we
# don't fan out to public mirrors because they replicate independently and
# silently serve snapshots that can be weeks behind upstream (see
# _check_snapshot_freshness). The main host is usually within minutes of
# OSM and, even when overloaded, eventually responds. Patience beats
# correctness-roulette.
OVERPASS_API = "https://overpass-api.de/api/interpreter"

# The front host's WAF rejects requests with `python-requests/*` style
# User-Agents with 406 Not Acceptable. A descriptive UA gets through and
# is also good Overpass etiquette (lets the operator contact us if a query
# is misbehaving). The lz4/z backends don't have this filter, but going
# through the LB is preferred so we benefit from whichever backend is
# healthier at request time.
USER_AGENT = "mtb-map-framework (+https://nuxx.net)"

MAX_RETRIES = 10
REQUEST_TIMEOUT = 120  # seconds per HTTP request

# Backoff schedule between retries, in seconds. Ramps up to ~2 min and
# then plateaus, so a stuck server gets steady polling without the gap
# growing unbounded. Length must be >= MAX_RETRIES - 1 (last attempt has
# no following sleep). Total worst-case wait ≈ 14.5 minutes across 10
# attempts, plus per-request timeouts.
RETRY_BACKOFF = [5, 10, 20, 40, 60, 90, 120, 120, 120]

# How many empty responses we tolerate before treating the query result
# as legitimately empty (e.g. a typo'd relation ID, or a relation that
# really has no member ways). Empty-but-successful responses look like
# transient server failures to the retry loop, so without a separate
# limit a bad relation ID would burn the full MAX_RETRIES schedule (~14
# min) before failing. Two attempts is enough to ride out a brief server
# hiccup; after that, we accept the empty payload, log a warning, and
# let the caller deal with it (typically: build an empty trails.geojson
# the user notices is empty when they open the map).
EMPTY_RETRY_LIMIT = 2

# Maximum acceptable replication lag for an Overpass mirror's snapshot,
# measured by the response's `osm3s.timestamp_osm_base` vs. wall clock.
# Overpass mirrors replicate independently and can fall days behind without
# raising any error — they'll cheerfully serve a stale snapshot. If a
# response is older than this threshold, treat the mirror as unhealthy and
# fall through to the next one. 24h is generous; the main mirror is usually
# within minutes of upstream.
MAX_OSM_BASE_LAG = timedelta(hours=24)


def _server_name(url):
    """Extract a short server name from a URL."""
    return url.split("/")[2]


class EmptyResponseError(Exception):
    """Raised when a server returns valid JSON but with no elements."""
    pass


class StaleSnapshotError(Exception):
    """Raised when a mirror's osm_base timestamp is too far behind wall clock."""
    pass


class PartialResponseError(Exception):
    """Raised when Overpass returns HTTP 200 with a runtime-error remark.

    Overpass signals a resource overrun (query timed out, or ran out of
    memory) by setting a top-level `remark` beginning "runtime error: ..."
    and returning whatever elements it managed to produce. The payload is
    truncated but otherwise well-formed, so the element-count checks treat
    it as success. We raise this instead so the caller retries and never
    caches the partial result.
    """
    pass


def _check_snapshot_freshness(data, server):
    """Raise StaleSnapshotError if the response's osm_base is too old.

    No-op if the response lacks an osm3s/timestamp_osm_base field (older
    Overpass versions, or non-standard responses).
    """
    osm_base_str = data.get("osm3s", {}).get("timestamp_osm_base")
    if not osm_base_str:
        return
    try:
        # Overpass formats: "2026-04-19T13:46:35Z"
        osm_base = datetime.strptime(
            osm_base_str, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return  # unrecognised format — don't reject on parse failure
    lag = datetime.now(timezone.utc) - osm_base
    if lag > MAX_OSM_BASE_LAG:
        raise StaleSnapshotError(
            f"snapshot is {lag.days}d{lag.seconds // 3600}h behind "
            f"(osm_base={osm_base_str}); mirror replication is lagging"
        )


def query(query_str, cache_dir=None, label="", require_elements=False):
    """Execute an Overpass API query with caching and retry.

    Hits the canonical overpass-api.de endpoint with a progressive backoff
    schedule (RETRY_BACKOFF). The main mirror is occasionally rate-limited
    or overloaded but consistently fresh; mirrors are not used because they
    can serve weeks-old snapshots without flagging it.

    Args:
        query_str: The Overpass QL query string.
        cache_dir: Optional directory path for caching responses.
        label: Optional label for log messages (e.g. "POIs", "trails").
        require_elements: If True, reject responses with an empty 'elements'
            array (treats it as a transient server failure). Use for queries
            that should always return results (e.g. relation lookups). Leave
            False for queries that may legitimately return nothing (e.g.
            POIs in an area with none).

    Returns:
        Parsed JSON response from the Overpass API.
    """
    label_suffix = f" for {label}" if label else ""

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        h = hashlib.md5(query_str.encode()).hexdigest()[:12]
        cp = os.path.join(cache_dir, f"overpass_{h}.json")
        if os.path.exists(cp):
            mtime = os.path.getmtime(cp)
            age = datetime.now() - datetime.fromtimestamp(mtime)
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            if age.days > 0:
                age_str = f"{age.days}d ago"
            else:
                hours = age.seconds // 3600
                age_str = f"{hours}h ago" if hours > 0 else "just now"
            with open(cp) as f:
                cached = json.load(f)
            try:
                _check_snapshot_freshness(cached, "cache")
            except StaleSnapshotError as e:
                print(f"  Discarding stale cache ({date_str}): {e}")
                os.remove(cp)
            else:
                print(f"  Using cached response ({date_str}, {age_str}): {cp}")
                return cached
    else:
        cp = None

    server = _server_name(OVERPASS_API)
    last_error = None
    empty_attempts = 0
    for attempt in range(MAX_RETRIES):
        try:
            print(f"  Querying {server}{label_suffix}... "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})")
            resp = requests.post(
                OVERPASS_API,
                data={"data": query_str},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            # A runtime-error remark means Overpass aborted mid-query (timeout
            # or OOM) and returned a TRUNCATED element list with HTTP 200. That
            # partial payload passes the empty-check below and would otherwise
            # be cached and frozen into the build's trails.geojson via its .sig
            # fingerprint, silently dropping trail geometry. Treat it as a
            # transient failure: retry on the backoff schedule, never cache.
            remark = data.get("remark")
            if remark and "runtime error" in remark.lower():
                raise PartialResponseError(remark.strip())

            if require_elements and not data.get("elements"):
                # Empty response is ambiguous — it may be a transient
                # server hiccup, OR the query is correctly formed but
                # matches no data (e.g. typo'd relation ID, deleted
                # relation). Retry up to EMPTY_RETRY_LIMIT to ride out
                # the hiccup case; after that, accept the empty payload
                # and let the caller decide what to do.
                empty_attempts += 1
                if empty_attempts > EMPTY_RETRY_LIMIT:
                    print(f"  warn: {server} returned 0 elements "
                          f"{empty_attempts} times in a row.")
                    print(f"           This usually means the query is "
                          f"correct but no data exists — check your "
                          f"relation IDs for typos.")
                    print(f"           Continuing with empty data; "
                          f"downstream may produce an empty map.")
                    _check_snapshot_freshness(data, server)
                    if cp:
                        with open(cp, "w") as f:
                            json.dump(data, f)
                    return data
                raise EmptyResponseError(
                    f"0 elements returned (attempt {empty_attempts}/"
                    f"{EMPTY_RETRY_LIMIT + 1}; may be transient)"
                )

            _check_snapshot_freshness(data, server)

            print(f"  Response from {server} "
                  f"({len(data.get('elements', []))} elements)")

            if cp:
                with open(cp, "w") as f:
                    json.dump(data, f)

            return data
        except (EmptyResponseError, StaleSnapshotError, PartialResponseError) as e:
            last_error = e
            print(f"    {server}: {e}")
        except (requests.RequestException, ValueError) as e:
            last_error = e
            print(f"    {server}: {type(e).__name__}: {e}")

        if attempt < MAX_RETRIES - 1:
            delay = RETRY_BACKOFF[attempt]
            print(f"  Retrying in {delay}s...")
            time.sleep(delay)

    print(f"\n  ERROR: {server} failed after {MAX_RETRIES} attempts.")
    if last_error is not None:
        print(f"    last error: {type(last_error).__name__}: {last_error}")
    print(f"\n  The server may be overloaded. Try again in a few minutes.")
    print(f"  Tip: Re-run with --trails instead of --force to reuse any")
    print(f"  cached responses from earlier successful queries.\n")
    sys.exit(1)
