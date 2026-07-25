"""Diff two OSM trail snapshots so a refresh can be vetted before deploy.

Production maps are never auto-regenerated: the curator re-fetches OSM data
only when they intend to, then personally vets it. Until now a
``--refresh-trails`` overwrote ``trails.src.geojson`` in place and reported
nothing, so "what actually changed upstream?" meant eyeballing the rendered
map. This module answers it directly.

Two deliberate choices about WHAT gets compared:

* **Keyed on OSM way IDs, never on merged features.**
  ``fetch_trails.merge_consecutive_ways`` fuses consecutive ways sharing a
  signature of ``(relation membership, name, mtb:scale:imba, resolved
  oneway)``. So a way that merely gains a name or a rating migrates between
  merged features, and a feature-level diff would report wholesale churn for
  a one-tag edit. ``way_ids`` is tracked through the merge and is stable.

* **Length is the only geometry signal; there is no vertex comparison.**
  OSM contributors nudge geometry constantly. Diffing coordinates would make
  every refresh look catastrophic while burying the tag and topology changes
  that actually matter. Per-trail length deltas below ``_LENGTH_NOISE_M`` are
  suppressed for the same reason.

Everything here except :func:`write_report` is pure: :func:`diff_snapshots`
takes two already-parsed snapshot dicts and returns a plain dict, which keeps
the test suite offline.
"""

import json
import os

import console
from geodesy import haversine_m, natural_key

# Per-trail length changes smaller than this are dropped as vertex noise
# rather than reported as edits. A contributor nudging a vertex a couple of
# metres moves a trail's length by well under this; a genuine extension or
# truncation clears it easily.
_LENGTH_NOISE_M = 20.0

# Caps on how many items any one section lists. Anything dropped is stated
# explicitly in the output — a silently truncated list reads as "that's
# everything" when it isn't.
_MAX_LIST = 40
_MAX_CONSOLE = 5

_ROUTE_FIELDS = ("name", "colour", "ref", "seasonal")
# Way-level tag fields, mapped to the label used in output.
_WAY_TAG_FIELDS = (("imba", "mtb:scale:imba"), ("oneway", "oneway"))


def _feature_length_m(feature):
    """Great-circle length of a Line/MultiLineString feature, in metres."""
    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "LineString":
        lines = [coords]
    elif gtype == "MultiLineString":
        lines = coords
    else:
        return 0.0
    total = 0.0
    for line in lines:
        for i in range(1, len(line)):
            try:
                total += haversine_m(line[i - 1][0], line[i - 1][1],
                                     line[i][0], line[i][1])
            except (TypeError, IndexError):
                # Malformed coordinate pair — skip the segment rather than
                # abort the whole report over one bad vertex.
                continue
    return total


def _index_snapshot(snap):
    """Reduce a snapshot to the comparable facts.

    Returns ``{routes, ways, trails, total_length_m, data_timestamp}`` where
    ``ways`` maps way id to its way-level tags plus the routes carrying it,
    and ``trails`` maps trail name to its way set and total length.
    """
    meta = snap.get("metadata") or {}
    routes = {str(k): (v or {}) for k, v in (meta.get("routes") or {}).items()}

    ways = {}
    trails = {}
    # A run shared by N routes is emitted once per route (that's how the
    # runtime draws parallel lanes), so length must be counted against the
    # way set, not per feature, or shared trails inflate the total.
    counted_geom = set()
    total_length = 0.0

    for f in snap.get("features") or []:
        props = f.get("properties") or {}
        way_ids = [str(w) for w in (props.get("way_ids") or [])]
        trail = str(props.get("trail_name") or "").strip()
        route_id = str(props.get("route_id") or "").strip()
        tags = {
            "trail": trail,
            "imba": str(props.get("imba_difficulty") or "").strip(),
            "oneway": str(props.get("oneway") or "").strip(),
        }

        for wid in way_ids:
            entry = ways.setdefault(wid, {**tags, "routes": set()})
            # Tags are way-level so every route's copy agrees; the first
            # write stands and only route membership accumulates.
            if route_id:
                entry["routes"].add(route_id)

        if trail:
            trails.setdefault(trail, {"ways": set(), "length_m": 0.0})
            trails[trail]["ways"].update(way_ids)

        geom_key = frozenset(way_ids)
        if way_ids and geom_key in counted_geom:
            continue
        if way_ids:
            counted_geom.add(geom_key)
        length = _feature_length_m(f)
        total_length += length
        if trail:
            trails[trail]["length_m"] += length

    return {
        "routes": routes,
        "ways": ways,
        "trails": trails,
        "total_length_m": total_length,
        "data_timestamp": meta.get("data_timestamp") or "",
    }


def diff_snapshots(prev, cur):
    """Compare two parsed snapshots. Pure; returns a plain dict."""
    a = _index_snapshot(prev)
    b = _index_snapshot(cur)

    ra, rb = a["routes"], b["routes"]
    routes_added = [(r, rb[r].get("name") or "")
                    for r in sorted(set(rb) - set(ra), key=natural_key)]
    routes_removed = [(r, ra[r].get("name") or "")
                      for r in sorted(set(ra) - set(rb), key=natural_key)]
    route_changes = []
    for rid in sorted(set(ra) & set(rb), key=natural_key):
        for field in _ROUTE_FIELDS:
            old = str(ra[rid].get(field) or "")
            new = str(rb[rid].get(field) or "")
            if old != new:
                route_changes.append({
                    "id": rid,
                    "name": rb[rid].get("name") or "",
                    "field": field,
                    "old": old,
                    "new": new,
                })

    wa, wb = a["ways"], b["ways"]
    ways_added = sorted(set(wb) - set(wa), key=natural_key)
    ways_removed = sorted(set(wa) - set(wb), key=natural_key)
    surviving = sorted(set(wa) & set(wb), key=natural_key)

    # Trail renames read off surviving ways, which is what makes them
    # renames rather than a coincidental add plus remove: the same physical
    # way changed its name tag.
    renames = {}
    tag_changes = []
    for wid in surviving:
        old_trail = wa[wid].get("trail") or ""
        new_trail = wb[wid].get("trail") or ""
        if old_trail != new_trail:
            renames[(old_trail, new_trail)] = \
                renames.get((old_trail, new_trail), 0) + 1
        for key, label in _WAY_TAG_FIELDS:
            old = wa[wid].get(key) or ""
            new = wb[wid].get(key) or ""
            if old != new:
                tag_changes.append({
                    "way_id": wid,
                    "trail": new_trail or old_trail,
                    "tag": label,
                    "old": old,
                    "new": new,
                })

    renamed_from = {old for old, _new in renames}
    renamed_to = {new for _old, new in renames}
    ta, tb = a["trails"], b["trails"]
    trails_added = sorted(n for n in set(tb) - set(ta) if n not in renamed_to)
    trails_removed = sorted(n for n in set(ta) - set(tb)
                            if n not in renamed_from)

    length_changes = []
    for name in sorted(set(ta) & set(tb)):
        delta = tb[name]["length_m"] - ta[name]["length_m"]
        if abs(delta) >= _LENGTH_NOISE_M:
            length_changes.append({
                "trail": name,
                "delta_m": delta,
                "old_m": ta[name]["length_m"],
                "new_m": tb[name]["length_m"],
            })
    length_changes.sort(key=lambda c: abs(c["delta_m"]), reverse=True)

    diff = {
        "routes_added": routes_added,
        "routes_removed": routes_removed,
        "route_changes": route_changes,
        "ways_added": ways_added,
        "ways_removed": ways_removed,
        "way_count_old": len(wa),
        "way_count_new": len(wb),
        "trails_added": trails_added,
        "trails_removed": trails_removed,
        "trail_renames": [
            {"old": old, "new": new, "ways": n}
            for (old, new), n in sorted(renames.items())
        ],
        "tag_changes": tag_changes,
        "length_changes": length_changes,
        "total_length_old_m": a["total_length_m"],
        "total_length_new_m": b["total_length_m"],
        "data_timestamp_old": a["data_timestamp"],
        "data_timestamp_new": b["data_timestamp"],
    }
    diff["changed"] = any(
        diff[k] for k in (
            "routes_added", "routes_removed", "route_changes",
            "ways_added", "ways_removed", "trails_added", "trails_removed",
            "trail_renames", "tag_changes", "length_changes",
        )
    )
    return diff


def _km_or_mi(metres, units):
    if units == "km":
        return f"{metres / 1000.0:+.2f} km"
    return f"{metres / 1609.344:+.2f} mi"


def _abs_km_or_mi(metres, units):
    if units == "km":
        return f"{metres / 1000.0:.2f} km"
    return f"{metres / 1609.344:.2f} mi"


def _capped(items, cap=_MAX_LIST):
    """Yield up to `cap` items, plus a trailing note when any were dropped."""
    shown = list(items)[:cap]
    dropped = len(items) - len(shown)
    return shown, dropped


def summarize(diff, units="mi"):
    """Short console summary: a few lines, no item dumps."""
    if not diff.get("changed"):
        return ["OSM data is unchanged since the previous snapshot."]
    lines = []

    def _count(label, key):
        n = len(diff[key])
        if n:
            lines.append(f"{label}: {n}")

    _count("routes added", "routes_added")
    _count("routes removed", "routes_removed")
    _count("route field changes", "route_changes")
    _count("ways added", "ways_added")
    _count("ways removed", "ways_removed")
    _count("trails added", "trails_added")
    _count("trails removed", "trails_removed")
    _count("trails renamed", "trail_renames")
    _count("tag changes", "tag_changes")
    _count("trails with length changes", "length_changes")

    lines.append(
        f"ways {diff['way_count_old']} → {diff['way_count_new']}, "
        f"total length "
        f"{_abs_km_or_mi(diff['total_length_old_m'], units)} → "
        f"{_abs_km_or_mi(diff['total_length_new_m'], units)} "
        f"({_km_or_mi(diff['total_length_new_m'] - diff['total_length_old_m'], units)})"
    )

    # A couple of concrete examples so the counts mean something without
    # opening the report.
    for name in diff["trails_added"][:_MAX_CONSOLE]:
        lines.append(f"  + trail {name}")
    for name in diff["trails_removed"][:_MAX_CONSOLE]:
        lines.append(f"  - trail {name}")
    for r in diff["trail_renames"][:_MAX_CONSOLE]:
        lines.append(f"  ~ trail {r['old'] or '(unnamed)'} → "
                     f"{r['new'] or '(unnamed)'} ({r['ways']} ways)")
    return lines


def format_report(diff, slug, units="mi"):
    """Full Markdown report. Pure."""
    out = [f"# OSM refresh diff — {slug}", ""]
    out.append(f"- Previous data timestamp: `{diff['data_timestamp_old'] or 'unknown'}`")
    out.append(f"- New data timestamp: `{diff['data_timestamp_new'] or 'unknown'}`")
    out.append(f"- Ways: {diff['way_count_old']} → {diff['way_count_new']}")
    out.append(
        f"- Total mapped length: "
        f"{_abs_km_or_mi(diff['total_length_old_m'], units)} → "
        f"{_abs_km_or_mi(diff['total_length_new_m'], units)} "
        f"({_km_or_mi(diff['total_length_new_m'] - diff['total_length_old_m'], units)})"
    )
    out.append("")

    if not diff.get("changed"):
        out.append("No route, way, trail, or tag changes.")
        out.append("")
        return "\n".join(out)

    out.append("Geometry is compared by length only; there is no vertex")
    out.append("comparison, and per-trail changes under "
               f"{_LENGTH_NOISE_M:.0f} m are treated as noise.")
    out.append("")

    def section(title, items, render):
        if not items:
            return
        shown, dropped = _capped(items)
        out.append(f"## {title} ({len(items)})")
        out.append("")
        for item in shown:
            out.append(f"- {render(item)}")
        if dropped:
            out.append(f"- …and {dropped} more not listed "
                       f"(cap {_MAX_LIST}).")
        out.append("")

    section("Routes added", diff["routes_added"],
            lambda t: f"`{t[0]}` {t[1] or '(unnamed)'}")
    section("Routes removed", diff["routes_removed"],
            lambda t: f"`{t[0]}` {t[1] or '(unnamed)'}")
    section("Route field changes", diff["route_changes"],
            lambda c: f"`{c['id']}` {c['name'] or '(unnamed)'} — "
                      f"{c['field']}: `{c['old']}` → `{c['new']}`")
    section("Trails added", diff["trails_added"], lambda n: n)
    section("Trails removed", diff["trails_removed"], lambda n: n)
    section("Trails renamed", diff["trail_renames"],
            lambda r: f"{r['old'] or '(unnamed)'} → "
                      f"{r['new'] or '(unnamed)'} ({r['ways']} ways)")
    section("Tag changes", diff["tag_changes"],
            lambda c: f"way `{c['way_id']}` "
                      f"({c['trail'] or 'unnamed'}) — {c['tag']}: "
                      f"`{c['old'] or '(none)'}` → `{c['new'] or '(none)'}`")
    section("Length changes", diff["length_changes"],
            lambda c: f"{c['trail']}: "
                      f"{_abs_km_or_mi(c['old_m'], units)} → "
                      f"{_abs_km_or_mi(c['new_m'], units)} "
                      f"({_km_or_mi(c['delta_m'], units)})")
    section("Ways added", diff["ways_added"],
            lambda w: f"https://www.openstreetmap.org/way/{w}")
    section("Ways removed", diff["ways_removed"],
            lambda w: f"https://www.openstreetmap.org/way/{w}")

    return "\n".join(out)


def snapshot_backup_path(cache_dir, slug):
    """Where the pre-refresh snapshot copy lives.

    The cache dir, deliberately, not the output dir: everything under
    ``build/<slug>/`` is deployed unless explicitly excluded, and adding a
    fourth exclusion pattern that has to stay in sync between
    ``_is_build_only_artifact`` and the rsync flags in
    ``tools/build_and_deploy.sh`` is a standing footgun for an internal file
    no rider ever fetches. The snapshot also tracks "the OSM data this map
    last fetched", which is per-config rather than per-output-dir.
    """
    return os.path.join(cache_dir, "osm_diff", slug, "trails.prev.geojson")


def report_path(cache_dir, slug):
    """Where the full Markdown report is written (overwritten per refresh)."""
    return os.path.join(cache_dir, "osm_diff", slug, "last-refresh.md")


def stash_previous_snapshot(trails_src_path, cache_dir, slug):
    """Copy the current snapshot aside before a refetch overwrites it.

    Returns the parsed previous snapshot, or None when there's nothing to
    compare against (first build, or an unreadable/absent prior snapshot).
    Never raises: a diff is a convenience and must not be able to fail a
    build.
    """
    if not os.path.exists(trails_src_path):
        return None
    try:
        with open(trails_src_path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        dest = snapshot_backup_path(cache_dir, slug)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(prev, f)
    except OSError as e:
        console.warn(f"could not stash previous trail snapshot: {e}")
    return prev


def report_refresh_diff(prev, cur, cache_dir, slug, units="mi"):
    """Print a summary and write the full report. Never raises."""
    if prev is None:
        return
    try:
        diff = diff_snapshots(prev, cur)
    except Exception as e:  # noqa: BLE001 - a diff must never fail a build
        console.warn(f"OSM diff failed: {e}")
        return

    console.step("OSM data diff vs previous snapshot")
    for line in summarize(diff, units):
        console.info(f"  {line}")

    try:
        path = report_path(cache_dir, slug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(format_report(diff, slug, units))
        if diff.get("changed"):
            console.info(f"  full report: {path}")
    except OSError as e:
        console.warn(f"could not write OSM diff report: {e}")
