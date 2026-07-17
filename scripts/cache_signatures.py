"""Build cache signatures and staleness checks.

Sidecar bbox/zoom signatures for PMTiles, plus the trail-data fetch
fingerprint and content hash that decide whether trails need re-fetching.
Pure I/O + hashing; extracted from build.py so the orchestrator stays lean.
"""

import hashlib
import json
import os

import console


def _bbox_signature(bbox, maxzoom):
    """Stable text signature for an (bbox, maxzoom) extraction request."""
    return f"bbox={','.join(f'{v:.4f}' for v in bbox)};maxzoom={maxzoom}"


def _signature_path(output_path):
    return output_path + ".sig"


def _load_signature(output_path):
    sig_path = _signature_path(output_path)
    if not os.path.exists(sig_path):
        return None
    try:
        with open(sig_path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _save_signature(output_path, signature):
    try:
        with open(_signature_path(output_path), "w", encoding="utf-8") as f:
            f.write(signature + "\n")
    except OSError as e:
        console.warn(f"could not write {_signature_path(output_path)}: {e}")


def _clear_signature(output_path):
    """Remove the sidecar. Call at the START of a regen, before any
    fetching: a signature must only ever vouch for the file the SAME
    run wrote. Without this, an interrupted regen could leave the
    previous build's sidecar next to whatever state the interruption
    left behind — e.g. the user deletes basemap.pmtiles to force a
    refetch (sidecar remains), the regen dies, and the next build sees
    file-plus-matching-sig and accepts it indefinitely."""
    try:
        os.remove(_signature_path(output_path))
    except FileNotFoundError:
        pass
    except OSError as e:
        console.warn(f"could not remove {_signature_path(output_path)}: {e}")


def _trails_fetch_fingerprint(config):
    """Stable hash of every config key fetch_trails() consumes. When
    this changes between builds, the cached trails.geojson is stale
    even though it exists on disk — adding a relation to
    clipped_relations, swapping osm_file, or editing direction_schedule
    all flip the hash and force a refetch.

    custom_routes is intentionally NOT in the fingerprint:
    _enrich_trails_geojson runs idempotently on every build and folds
    custom routes into the (cached or fresh) trails.geojson, so adding
    a custom route doesn't require an Overpass refetch.

    File-path-based inputs (osm_file) hash the path string only, not
    file content; the curator's --refresh-trails flag remains the explicit
    tool when a file's contents change without the path changing.
    """
    inputs = {
        "relations": sorted(config.get("relations") or []),
        "clipped_relations": sorted(config.get("clipped_relations") or []),
        "winter_relations": sorted(config.get("winter_relations") or []),
        "summer_relations": sorted(config.get("summer_relations") or []),
        "emergency_access_relations": sorted(config.get("emergency_access_relations") or []),
        "osm_file": config.get("osm_file") or "",
        "direction_schedule": config.get("direction_schedule") or {},
    }
    blob = json.dumps(inputs, sort_keys=True, default=str)
    return "trails-fp=" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _trails_content_hash(trails_path):
    """SHA-256 of trails.geojson exactly as it sits on disk.

    Recorded in the sidecar at the end of every successful build and
    re-checked at the start of the next one. Lets the build notice when
    trails.geojson was changed out from under it (truncated, reverted by
    a backup/sync restore, hand-edited, half-written) and refetch instead
    of silently reusing a bad file. We only ever compare a build's output
    against what THAT build recorded, so enrichment's between-build
    rewrite non-determinism is irrelevant: the stored hash always tracks
    the bytes the previous build actually left on disk.
    """
    h = hashlib.sha256()
    try:
        with open(trails_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _trails_needs_refetch(trails_path, config):
    """True iff the cached trails.geojson must be refetched. Returns
    (needs_refetch, reason).

    Two independent triggers:
      1. Config inputs changed since the file was fetched (the sidecar's
         config-fingerprint line no longer matches the current config).
      2. The file's bytes no longer match the `trails-content` hash the
         last build recorded, meaning trails.geojson was modified,
         truncated, or reverted out from under us. This guard stops a
         reverted/partial build/site from being silently reused and
         shipped (the failure that dropped half of Addison's trails).

    A missing sidecar (legacy build), or one without the content line,
    is treated as a backfill: reuse the file and write a full sidecar on
    the next save, rather than forcing a surprise refetch.
    """
    if not os.path.exists(trails_path):
        return True, "file missing"
    raw = _load_signature(trails_path)
    if raw is None:
        return False, "fingerprint sidecar missing (legacy build, backfilling)"

    # Sidecar layout (newest format, oldest is just line 0):
    #   trails-fp=<config fingerprint>
    #   trails-content=<sha256 of trails.geojson as last written>
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    stored_fp = lines[0] if lines else ""
    expected_fp = _trails_fetch_fingerprint(config)
    if stored_fp != expected_fp:
        return True, f"config inputs changed since last fetch ({stored_fp!r} → {expected_fp!r})"

    stored_content = None
    for ln in lines[1:]:
        if ln.startswith("trails-content="):
            stored_content = ln[len("trails-content=") :]
            break
    if stored_content:
        actual_content = _trails_content_hash(trails_path)
        if actual_content and actual_content != stored_content:
            return True, (
                f"trails.geojson changed on disk since it was built "
                f"(content {stored_content[:12]}... vs {actual_content[:12]}...); "
                f"refetching so a truncated or reverted file isn't reused"
            )
    return False, None


def _pmtiles_needs_regen(output_path, bbox, maxzoom):
    """True if the cached PMTiles is missing OR its (bbox, maxzoom)
    signature doesn't match what's being requested. Returns a tuple
    (needs_regen: bool, reason: str | None) so the caller can log why."""
    if not os.path.exists(output_path):
        return True, "file missing"
    expected = _bbox_signature(bbox, maxzoom)
    actual = _load_signature(output_path)
    if actual is None:
        return True, "signature sidecar missing (legacy build)"
    if actual != expected:
        return True, f"signature changed (was {actual!r}, want {expected!r})"
    return False, None
