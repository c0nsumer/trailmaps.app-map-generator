"""Per-map cache ownership manifests and stale-entry pruning.

The shared cache/ directory has no ownership records: an Overpass entry
is keyed by a hash of the query text, so a config edit that changes the
query orphans the old entry forever, and nothing can tell which map an
entry belongs to after the fact. This module gives each map a manifest
(cache/manifests/<slug>.json) listing the cache files its last build
referenced. Paths are recorded at the source, where each fetcher
computes its cache path, because the keys cannot be safely re-derived
offline: the member-ways query text depends on the relations response's
dict order, the POI query on the build-time-resolved bbox, and the
relations query on YAML list order.

Pruning deletes only entries that appear in THIS map's previous
manifest, no longer appear in its new one, and are claimed by no other
map's manifest, and only when the filename matches a known cache-entry
pattern under the cache dir. Cross-map deletion is therefore impossible
by construction, which is the invariant that got the old --refresh
rmtree removed (see build.py's cache_dir comment).

No locking: concurrent builds sharing one cache dir can at worst prune
an entry another build just read, and that build already holds the data
in memory. Its next fetch-needing build re-queries; nothing corrupts.
"""

import json
import os
import re

import console

_VERSION = 1

# Mirrors validate_config's slug rule; re-checked here because the slug
# becomes a filename inside the shared cache dir.
_SLUG_RE = re.compile(r"[a-z0-9_-]+")

# Deletion allowlist over cache-dir-relative paths. Anything not
# matching one of these is never deleted, which keeps manifests/,
# osm_diff/, vendor/, and anything a corrupt manifest might name
# structurally out of reach.
_PRUNABLE = (
    re.compile(r"overpass_[0-9a-f]{12}\.json"),
    re.compile(r"route_stats/elev_[^/]+_[0-9a-f]{16}\.json"),
    re.compile(r"derive_accent/[0-9a-f]{16}\.json"),
)

CATEGORIES = ("overpass_trails", "overpass_pois", "route_stats", "derive_accent")

# Module-level collector. Every cache-path computation site records
# into it and build.py drains it at stage boundaries; all recorders run
# on the main thread (the only threaded build work, basemap/terrain
# extraction, touches no manifest-tracked cache).
_touched: list[str] = []


def record(path):
    """Record an absolute cache path as referenced by the current build."""
    _touched.append(path)


def drain():
    """Return recorded paths (deduped, order kept) and clear the collector."""
    paths = list(dict.fromkeys(_touched))
    _touched.clear()
    return paths


def _manifest_path(cache_dir, slug):
    return os.path.join(cache_dir, "manifests", f"{slug}.json")


def load(cache_dir, slug):
    """Load a map's manifest. Returns the categories dict (relative
    paths), or None when the manifest is missing, unreadable, or not a
    recognized shape. None means "no manifest": callers prune nothing
    and write a fresh one.
    """
    try:
        with open(_manifest_path(cache_dir, slug), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return None
    cats = data.get("categories")
    if not isinstance(cats, dict):
        return None
    out = {}
    for key in CATEGORIES:
        vals = cats.get(key)
        if not isinstance(vals, list) or not all(isinstance(p, str) for p in vals):
            return None
        out[key] = vals
    return out


def save(cache_dir, slug, categories):
    """Write a map's manifest atomically. Takes absolute paths,
    relativizes them against cache_dir (dropping, with a warning,
    anything outside it), and returns the relativized dict so prune()
    operates on uniform relative paths. Returns None on failure: the
    caller must then skip pruning, because deleting claims whose
    replacement record failed to persist would leave the old manifest
    as the only record of entries already gone.
    """
    if not _SLUG_RE.fullmatch(slug):
        console.warn(f"cache manifest: refusing unsafe slug {slug!r}")
        return None
    cache_dir = os.path.normpath(os.path.abspath(cache_dir))
    rel_cats = {}
    for key in CATEGORIES:
        rels = []
        for p in categories.get(key, []):
            rel = os.path.relpath(os.path.normpath(os.path.abspath(p)), cache_dir)
            if rel.startswith(".."):
                console.warn(f"cache manifest: dropping path outside cache dir: {p}")
                continue
            rels.append(rel.replace(os.sep, "/"))
        rel_cats[key] = sorted(set(rels))

    path = _manifest_path(cache_dir, slug)
    tmp = path + ".tmp"
    # Atomic write, same rationale as overpass._write_cache: a crash
    # mid-write must leave the previous manifest intact, never a
    # truncated one (load() treats truncation as "no manifest", which
    # would silently orphan every claim).
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": _VERSION, "slug": slug, "categories": rel_cats}, f, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        console.warn(f"could not write cache manifest {path}: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return None
    return rel_cats


def _other_claims(cache_dir, slug):
    """Union of every OTHER map's manifest claims, or None if any
    sibling manifest is unreadable. An incomplete protection set must
    abort the prune, not narrow it.
    """
    manifests_dir = os.path.join(cache_dir, "manifests")
    claimed = set()
    try:
        names = os.listdir(manifests_dir)
    except OSError:
        return claimed
    for name in names:
        if not name.endswith(".json") or name == f"{slug}.json":
            continue
        other = load(cache_dir, name[: -len(".json")])
        if other is None:
            console.warn(
                f"cache manifest: {name} is unreadable; skipping prune "
                "(cannot know which entries it claims)"
            )
            return None
        for vals in other.values():
            claimed.update(vals)
    return claimed


def prune(cache_dir, slug, old_categories, new_categories):
    """Delete cache entries this map's previous manifest claimed that
    its new manifest no longer does, sparing anything another map's
    manifest claims. Both category dicts hold cache-dir-relative paths
    (old from load(), new from save()). Returns (removed, freed_bytes).
    """
    cache_dir = os.path.normpath(os.path.abspath(cache_dir))
    old_set = {p for vals in old_categories.values() for p in vals}
    new_set = {p for vals in new_categories.values() for p in vals}
    protected = _other_claims(cache_dir, slug)
    if protected is None:
        return 0, 0

    removed = 0
    freed = 0
    for rel in sorted(old_set - new_set - protected):
        if not any(pat.fullmatch(rel) for pat in _PRUNABLE):
            continue
        abs_p = os.path.normpath(os.path.join(cache_dir, rel))
        # Redundant with the allowlist, but the invariant is cheap to
        # enforce twice: nothing outside cache_dir is ever unlinked.
        if not abs_p.startswith(cache_dir + os.sep):
            continue
        try:
            size = os.path.getsize(abs_p)
            os.remove(abs_p)
        except FileNotFoundError:
            # Shared entry another map already pruned, or a claim for a
            # key whose write never happened. Either way, nothing to do.
            continue
        except OSError as e:
            console.warn(f"cache manifest: could not remove {abs_p}: {e}")
            continue
        removed += 1
        freed += size
        console.info(f"Cache: pruned stale entry {rel}")
    return removed, freed
