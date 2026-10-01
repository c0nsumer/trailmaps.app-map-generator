"""Shared Overpass API query helper with caching and retry."""

import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import cache_manifest
import console
import requests

# Single canonical Overpass endpoint. The bare `overpass-api.de` host
# load-balances across the lz4 and z backends, which is what we want; we
# don't fan out to public mirrors because they replicate independently and
# silently serve snapshots that can be weeks behind upstream (see
# _check_snapshot_freshness). The main host is usually within minutes of
# OSM and, even when overloaded, eventually responds. Patience beats
# correctness-roulette.
OVERPASS_API = "https://overpass-api.de/api/interpreter"

# The front host's WAF rejects `python-requests/*` User-Agents with 406.
# A descriptive UA gets through and lets the operator contact us about a
# misbehaving query.
USER_AGENT = "mtb-map-framework (+https://nuxx.net)"

MAX_RETRIES = 10
# Client-side timeout per HTTP request. MUST be >= the largest server-side
# [timeout:N] any caller's query grants itself (currently 300 in
# fetch_trails' bulk ways query) - the server budget is meaningless if the
# client hangs up first. When these disagreed (120 client vs 300 server), a
# legitimately slow query on a large map was killed client-side, retried
# 10x with each retry restarting the server-side work from scratch, then
# failed the build after ~35 minutes.
REQUEST_TIMEOUT = 310  # seconds per HTTP request

# Backoff between retries, in seconds: ramps to ~2 min, then plateaus.
# Length must be >= MAX_RETRIES - 1 (the last attempt has no following
# sleep). Worst-case wait is ≈ 14.5 minutes, plus per-request timeouts.
RETRY_BACKOFF = [5, 10, 20, 40, 60, 90, 120, 120, 120]

# Empty responses tolerated before accepting the result as legitimately
# empty (a typo'd relation ID, say). An empty response looks like a
# transient failure to the retry loop, so without this limit a bad ID
# would burn the full MAX_RETRIES schedule (~14 min). Two attempts ride
# out a brief hiccup.
EMPTY_RETRY_LIMIT = 2

# Maximum replication lag of a response's `osm3s.timestamp_osm_base` vs.
# wall clock. Overpass instances can fall days behind without raising an
# error, so an older response raises StaleSnapshotError and retries on
# the backoff schedule. 24h is generous. Applies to LIVE responses only;
# cached responses are served regardless of age.
MAX_OSM_BASE_LAG = timedelta(hours=24)


def _write_cache(cache_path, data):
    """Write a cache file atomically (temp sibling + os.replace).

    A Ctrl-C or crash mid-write would otherwise leave truncated JSON
    that crashes every later build. The rename makes the file either
    complete or absent, and the guarded read in query() cleans up any
    truncated leftovers.
    """
    tmp = cache_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, cache_path)
    except OSError as e:
        console.warn(f"could not write Overpass cache {cache_path}: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)


class EmptyResponseError(Exception):
    """Raised when a server returns valid JSON but with no elements."""


class StaleSnapshotError(Exception):
    """Raised when a mirror's osm_base timestamp is too far behind wall clock."""


class PartialResponseError(Exception):
    """Raised when Overpass returns HTTP 200 with a runtime-error remark.

    Overpass signals a resource overrun (query timed out, or ran out of
    memory) by setting a top-level `remark` beginning "runtime error: ..."
    and returning whatever elements it managed to produce. The payload is
    truncated but otherwise well-formed, so the element-count checks treat
    it as success. We raise this instead so the caller retries and never
    caches the partial result.
    """


def cache_path(query_str, cache_dir):
    """Where the response to `query_str` is cached in `cache_dir`."""
    h = hashlib.md5(query_str.encode()).hexdigest()[:12]
    return os.path.join(cache_dir, f"overpass_{h}.json")


def _check_snapshot_freshness(data):
    """Raise StaleSnapshotError if the response's osm_base is too old.

    No-op if the response lacks an osm3s/timestamp_osm_base field (older
    Overpass versions, or non-standard responses).
    """
    osm_base_str = data.get("osm3s", {}).get("timestamp_osm_base")
    if not osm_base_str:
        return
    try:
        # Overpass formats: "2026-04-19T13:46:35Z"
        osm_base = datetime.strptime(osm_base_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        return  # unrecognized format - don't reject on parse failure
    lag = datetime.now(UTC) - osm_base
    if lag > MAX_OSM_BASE_LAG:
        raise StaleSnapshotError(
            f"snapshot is {lag.days}d{lag.seconds // 3600}h behind "
            f"(osm_base={osm_base_str}); mirror replication is lagging"
        )


def query(query_str, cache_dir=None, label="", require_elements=False, refresh=False):
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
        refresh: If True, skip reading any cached response (the fresh
            result still overwrites the cache entry). This is how
            `build.py --refresh` re-fetches ONE map's data - the cache
            directory is shared by every map, so deleting it wholesale
            would throw away the other maps' responses (and it used to).

    Returns:
        Parsed JSON response from the Overpass API.
    """
    label_suffix = f" for {label}" if label else ""

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cp = cache_path(query_str, cache_dir)
        # Before the hit/miss/refresh branches, so every outcome records
        # this build's claim on the entry (see cache_manifest docstring).
        cache_manifest.record(cp)
        if refresh:
            if os.path.exists(cp):
                console.info(f"Bypassing cached response (refresh requested): {cp}")
        elif os.path.exists(cp):
            mtime = os.path.getmtime(cp)
            age = datetime.now() - datetime.fromtimestamp(mtime)
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            if age.days > 0:
                age_str = f"{age.days}d ago"
            else:
                hours = age.seconds // 3600
                age_str = f"{hours}h ago" if hours > 0 else "just now"
            try:
                with open(cp, encoding="utf-8") as f:
                    cached = json.load(f)
            except (OSError, ValueError) as e:
                # Truncated/corrupt cache file (e.g. written before cache
                # writes were atomic). Delete it and fall through to a
                # fresh fetch - a bad cache entry must never wedge builds.
                console.warn(f"Discarding unreadable cache ({type(e).__name__}): {cp}")
                try:
                    os.remove(cp)
                except OSError:
                    pass
            else:
                # No freshness check here: a readable cache entry is served
                # regardless of age, so an unflagged rebuild is fully
                # offline and reproducible. Re-querying OSM is an explicit
                # act (--refresh / --refresh-trails / --refresh-pois), not
                # a side effect of the cache aging past a threshold.
                console.info(f"Using cached response ({date_str}, {age_str}): {cp}")
                return cached
    else:
        cp = None

    server = OVERPASS_API.split("/")[2]
    last_error = None
    empty_attempts = 0
    for attempt in range(MAX_RETRIES):
        try:
            console.info(
                f"Querying {server}{label_suffix}... (attempt {attempt + 1}/{MAX_RETRIES})"
            )
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
            # be cached, silently dropping trail geometry from every later
            # build. Treat it as a transient failure: retry, never cache.
            remark = data.get("remark")
            if remark and "runtime error" in remark.lower():
                raise PartialResponseError(remark.strip())

            if require_elements and not data.get("elements"):
                # Empty response is ambiguous - it may be a transient
                # server hiccup, OR the query is correctly formed but
                # matches no data (e.g. typo'd relation ID, deleted
                # relation). Retry up to EMPTY_RETRY_LIMIT to ride out
                # the hiccup case; after that, accept the empty payload
                # and let the caller decide what to do. It is not cached:
                # with no TTL, a cached hiccup would never be retried.
                empty_attempts += 1
                if empty_attempts > EMPTY_RETRY_LIMIT:
                    console.warn(f"{server} returned 0 elements {empty_attempts} times in a row.")
                    console.info(
                        "This usually means the query is "
                        "correct but no data exists - check your "
                        "relation IDs for typos."
                    )
                    console.info("Continuing with empty data; downstream may produce an empty map.")
                    _check_snapshot_freshness(data)
                    return data
                raise EmptyResponseError(
                    f"0 elements returned (attempt {empty_attempts}/"
                    f"{EMPTY_RETRY_LIMIT + 1}; may be transient)"
                )

            _check_snapshot_freshness(data)

            console.info(f"Response from {server} ({len(data.get('elements', []))} elements)")

            if cp:
                _write_cache(cp, data)

            return data
        except (EmptyResponseError, StaleSnapshotError, PartialResponseError) as e:
            last_error = e
            console.info(f"  {server}: {e}")
        except (requests.RequestException, ValueError) as e:
            last_error = e
            console.info(f"  {server}: {type(e).__name__}: {e}")

        if attempt < MAX_RETRIES - 1:
            delay = RETRY_BACKOFF[attempt]
            console.info(f"Retrying in {delay}s...")
            time.sleep(delay)

    console.blank()
    console.error(f"{server} failed after {MAX_RETRIES} attempts.")
    if last_error is not None:
        console.error(f"last error: {type(last_error).__name__}: {last_error}")
    console.blank()
    console.error("The server may be overloaded. Try again in a few minutes.")
    console.info("Tip: Re-run without the --refresh flags to reuse any")
    console.info("cached responses from earlier successful queries.\n")
    sys.exit(1)
