"""Report genuine gaps in the OSM data a map is built from.

This is NOT a "make the render look better" checklist. ``docs/osm-mapping.md``
is explicit that adding tags to manipulate a renderer degrades the dataset for
every other consumer, so every check here has to stand on its own as a data
problem: a fact that is missing, self-contradictory, or geometrically
impossible. Anything whose only justification would be "then this framework
draws it more nicely" is deliberately absent.

What that rules out, concretely:

* Unnamed ways are not flagged. Connectors and spurs are legitimately
  nameless, and "name it so a label appears" is exactly the anti-pattern.
  Only *named* trails are checked for a missing difficulty rating, and only
  on maps that show difficulty at all.
* ``oneway:bicycle`` coverage is not checked, even though it would be useful.
  The snapshot stores the *resolved* ``oneway`` value, so whether it came from
  ``oneway`` or ``oneway:bicycle`` isn't recoverable without changing what
  ``fetch_trails`` emits. Better to omit a check than to imply coverage that
  isn't there.
* Parking, trailheads, and hubs are never checked for distance from a trail.
  They are curator-placed and being off-trail is the point of a parking lot.

A way listed more than once in a relation does qualify: it is a fact about
the relation itself, not a rendering preference, and it changes a
rider-facing distance, since each repeat counts as an extra pass.

Runs on every build from data already in memory; prints only when it finds
something, so an unremarkable build stays quiet.
"""

import json
import os

import console
from compute_route_stats import _chain_segments, _coords_for_route
from fetch_trails import gather_relation_ids
from geodesy import haversine_m, natural_key, point_to_polyline_m

# A pair of chain endpoints closer than this, but not identical, is very
# likely two ways that look joined on the map and aren't. Exact node sharing
# reads as 0 and is excluded by the lower bound, which is what keeps ordinary
# branch junctions (where the greedy chainer leaves a leftover branch) out of
# the results.
_GAP_MIN_M = 0.01
_GAP_MAX_M = 10.0

# A guidepost or emergency-access point farther than this from any trail
# vertex is either misplaced or attached to a trail this map doesn't include.
# Generous on purpose: the intent is to catch obvious outliers, not to police
# placement.
_ORPHAN_POI_M = 250.0

# POI types this checks. Guideposts and emergency-access points are
# definitionally ON the trail. Everything else is either curator-placed
# (parking / trailheads / hubs) or legitimately near a trailhead rather than a
# trail (toilets / drinking water / bicycle repair stations, which the
# runtime already scopes at 500 m).
_ON_TRAIL_POI_TYPES = ("trail_marker",)

_VALID_RATINGS = {"0", "1", "2", "3", "4", "5"}

_MAX_LIST = 40


def _route_names(trails_geojson):
    meta = trails_geojson.get("metadata") or {}
    return {str(k): (v or {}) for k, v in (meta.get("routes") or {}).items()}


def _check_routes(routes):
    missing_name = []
    missing_colour = []
    for rid in sorted(routes, key=natural_key):
        info = routes[rid]
        name = str(info.get("name") or "").strip()
        # osm_parser.relation_info names an untagged relation "Route <id>"
        # before the snapshot is written.
        if not name or name == f"Route {rid}":
            missing_name.append(rid)
        if not str(info.get("colour") or "").strip():
            missing_colour.append((rid, info.get("name") or ""))
    return missing_name, missing_colour


def _check_repeated_members(routes):
    """Relations whose fetch-time metadata records a way listed twice or more.

    A relation describes a signed route, so each way normally appears once.
    Out-and-back event courses repeat on purpose, which is why this only
    lists them for a human look rather than judging which are wrong.
    """
    found = []
    for rid in sorted(routes, key=natural_key):
        repeated = routes[rid].get("repeated_ways") or {}
        if not repeated:
            continue
        ways = sorted(((str(w), int(n)) for w, n in repeated.items()),
                      key=lambda t: natural_key(t[0]))
        found.append((rid, routes[rid].get("name") or "", ways))
    return found


def _check_ratings(features, show_difficulty):
    """Named trails with no rating, and any out-of-range rating value.

    Invalid values are always reported: a rating outside 0-5 is wrong
    regardless of whether this map draws difficulty.

    The missing-rating check is narrower, and deliberately so. It requires
    both that the map shows difficulty AND that at least one trail is already
    rated - i.e. that rating coverage is PARTIAL. On a map where nobody has
    tagged difficulty at all, listing every named trail would be asking for
    tags purely so this renderer has something to draw (and the Difficulty
    control is auto-hidden on such maps anyway, so nothing is even missing
    from the UI). Partial coverage is the genuinely actionable case: someone
    rated most of the system and these ones were skipped.
    """
    invalid = []
    rated_trails = set()
    named_trails = {}
    for f in features:
        props = f.get("properties") or {}
        trail = str(props.get("trail_name") or "").strip()
        raw = str(props.get("imba_difficulty") or "").strip()
        if raw and raw not in _VALID_RATINGS:
            for wid in (props.get("way_ids") or []):
                invalid.append((str(wid), trail, raw))
        if not trail:
            continue
        named_trails.setdefault(trail, set()).update(
            str(w) for w in (props.get("way_ids") or []))
        if raw in _VALID_RATINGS:
            rated_trails.add(trail)

    unrated = []
    if show_difficulty and rated_trails:
        unrated = sorted(t for t in named_trails if t not in rated_trails)
    # Dedupe invalid entries (a way can appear once per parent relation).
    invalid = sorted(set(invalid), key=lambda t: natural_key(t[0]))
    return unrated, invalid


def _clipped_route_ids(trails_geojson, config):
    """Route ids drawn from ``clipped_relations``, through the snapshot's
    super-relation expansions. A relation also listed as a source route
    stays a source route, as in osm_parser.resolve_relations."""
    meta = trails_geojson.get("metadata") or {}
    expansions = meta.get("super_relation_expansions") or {}

    def _resolve(ids):
        return {str(c) for rid in ids for c in (expansions.get(str(rid)) or [rid])}

    source_ids, clipped_ids = gather_relation_ids(config)
    return _resolve(clipped_ids) - _resolve(source_ids)


def _check_route_gaps(features, routes, skip_ids=()):
    """Chain each route's segments and look for near-miss endpoint pairs.

    Uses compute_route_stats' chainer, so "connected" means exact node
    identity to ~1 cm, never a proximity heuristic. Leftover chains from a
    genuine branch junction share an endpoint exactly, so they fall below
    _GAP_MIN_M and drop out.

    ``skip_ids`` are clipped routes. The clip cuts a route that leaves the
    bbox and comes back into pieces ending on the bbox edge, a gap of the
    clip's making. The snapshot does not record the clip bbox, so the
    whole route is skipped rather than its edge points guessed.
    """
    gaps = []
    for rid in sorted(routes, key=natural_key):
        if rid in skip_ids:
            continue
        chains = _chain_segments(_coords_for_route(features, rid))
        if len(chains) < 2:
            continue
        # Endpoints only: an interior vertex passing near another way is a
        # crossing, not a broken connection.
        ends = []
        for idx, chain in enumerate(chains):
            ends.append((idx, chain[0]))
            ends.append((idx, chain[-1]))
        seen = set()
        for i in range(len(ends)):
            ci, pi = ends[i]
            for j in range(i + 1, len(ends)):
                cj, pj = ends[j]
                if ci == cj:
                    continue
                d = haversine_m(pi[0], pi[1], pj[0], pj[1])
                if _GAP_MIN_M < d <= _GAP_MAX_M:
                    key = (round(pi[0], 6), round(pi[1], 6),
                           round(pj[0], 6), round(pj[1], 6))
                    rkey = (key[2], key[3], key[0], key[1])
                    if key in seen or rkey in seen:
                        continue
                    seen.add(key)
                    gaps.append({
                        "route_id": rid,
                        "route_name": routes[rid].get("name") or "",
                        "distance_m": d,
                        "lon": pi[0],
                        "lat": pi[1],
                    })
    gaps.sort(key=lambda g: g["distance_m"])
    return gaps


def _trail_lines(features):
    lines = []
    for f in features:
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "LineString":
            if len(coords) >= 2:
                lines.append(coords)
        elif gtype == "MultiLineString":
            for line in coords:
                if len(line) >= 2:
                    lines.append(line)
    return lines


def _check_orphan_pois(pois_geojson, features):
    """On-trail POI types sitting implausibly far from the trail network.

    Measures to the nearest SEGMENT, not the nearest vertex. Vertex distance
    looked cheaper but is wrong on exactly the ways most likely to trip it: a
    rail trail drawn as a few long straight runs can place a guidepost sitting
    squarely on the trail several hundred metres from any vertex, which would
    report as an orphan. Early exit keeps the segment scan cheap for the
    common case of a POI that is genuinely on the network.
    """
    if not pois_geojson:
        return []
    lines = _trail_lines(features)
    if not lines:
        return []
    orphans = []
    for f in (pois_geojson.get("features") or []):
        props = f.get("properties") or {}
        if props.get("poi_type") not in _ON_TRAIL_POI_TYPES:
            continue
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        best = None
        for line in lines:
            d = point_to_polyline_m(lon, lat, line, stop_below=_ORPHAN_POI_M)
            if d is None:
                continue
            if best is None or d < best:
                best = d
            if best <= _ORPHAN_POI_M:
                break  # on the network; no need for the true minimum
        if best is not None and best > _ORPHAN_POI_M:
            orphans.append({
                "name": props.get("name") or props.get("ref") or "(unnamed)",
                "distance_m": best,
                "lon": lon,
                "lat": lat,
            })
    orphans.sort(key=lambda o: o["distance_m"], reverse=True)
    return orphans


def audit(trails_geojson, pois_geojson, config):
    """Run every check. Pure apart from reading the passed-in dicts."""
    features = trails_geojson.get("features") or []
    routes = _route_names(trails_geojson)
    missing_name, missing_colour = _check_routes(routes)
    show_difficulty = config.get("show_difficulty", True)
    unrated, invalid = _check_ratings(features, show_difficulty)
    findings = {
        "routes_missing_name": missing_name,
        "routes_missing_colour": missing_colour,
        "named_trails_missing_rating": unrated,
        "invalid_ratings": invalid,
        "probable_gaps": _check_route_gaps(
            features, routes, _clipped_route_ids(trails_geojson, config)),
        "orphan_pois": _check_orphan_pois(pois_geojson, features),
        "repeated_members": _check_repeated_members(routes),
        "difficulty_checked": bool(show_difficulty),
    }
    findings["total"] = sum(len(findings[k]) for k in _LIST_KEYS)
    return findings


def summarize(findings):
    """Console lines. Empty list when there's nothing worth saying."""
    if not findings.get("total"):
        return []
    lines = []
    # (key, singular, plural) - a report that says "1 relations" reads as a bug
    # in the report rather than a finding worth acting on.
    labels = [
        ("probable_gaps",
         "possible unconnected way pair", "possible unconnected way pairs"),
        ("invalid_ratings",
         "out-of-range mtb:scale:imba value", "out-of-range mtb:scale:imba values"),
        ("named_trails_missing_rating",
         "named trail with no difficulty rating",
         "named trails with no difficulty rating"),
        ("routes_missing_name", "relation with no name", "relations with no name"),
        ("routes_missing_colour",
         "relation with no colour", "relations with no colour"),
        ("orphan_pois",
         "trail marker far from any trail", "trail markers far from any trail"),
        ("repeated_members",
         "relation with a way listed more than once",
         "relations with a way listed more than once"),
    ]
    for key, singular, plural in labels:
        n = len(findings[key])
        if n:
            lines.append(f"{n} {singular if n == 1 else plural}")
    return lines


def _relation_link(rid):
    """openstreetmap.org link for a relation. A negative id is a JOSM
    object not yet uploaded, which has no page to link to."""
    if str(rid).startswith("-"):
        return f"relation `{rid}` (not uploaded)"
    return f"https://www.openstreetmap.org/relation/{rid}"


def _way_link(wid):
    """openstreetmap.org link for a way, with the same not-uploaded rule
    as _relation_link."""
    if str(wid).startswith("-"):
        return f"way `{wid}`"
    return f"https://www.openstreetmap.org/way/{wid}"


def _render_repeated(item):
    rid, name, ways = item
    # Twice is the expected out-and-back shape; only a higher count is
    # worth spelling out.
    links = ", ".join(
        _way_link(w) + (f" ({n}x)" if n > 2 else "") for w, n in ways)
    return f"{_relation_link(rid)} {name or '(unnamed)'}: {links}"


def format_report(findings, slug):
    out = [f"# OSM data notes - {slug}", ""]
    out.append("These are gaps and inconsistencies in the underlying OSM data,")
    out.append("not rendering preferences. Nothing here asks you to tag for the")
    out.append("renderer: every item is a fact that is missing, contradictory,")
    out.append("or geometrically implausible on its own terms. Fixing them")
    out.append("upstream in OSM improves the data for every consumer, not just")
    out.append("this map.")
    out.append("")

    if not findings.get("total"):
        out.append("No issues found.")
        out.append("")
        return "\n".join(out)

    def section(title, items, render, note=None):
        if not items:
            return
        out.append(f"## {title} ({len(items)})")
        out.append("")
        if note:
            out.append(note)
            out.append("")
        shown = list(items)[:_MAX_LIST]
        for item in shown:
            out.append(f"- {render(item)}")
        dropped = len(items) - len(shown)
        if dropped:
            out.append(f"- …and {dropped} more not listed (cap {_MAX_LIST}).")
        out.append("")

    section(
        "Possible unconnected ways", findings["probable_gaps"],
        lambda g: f"{g['route_name'] or g['route_id']}: endpoints "
                  f"{g['distance_m']:.2f} m apart at "
                  f"https://www.openstreetmap.org/#map=19/{g['lat']:.6f}/{g['lon']:.6f}",
        note="Two of a route's ways end within 10 m of each other without "
             "sharing a node, so they look joined but aren't. This breaks "
             "routing for every data consumer.",
    )
    section(
        "Out-of-range mtb:scale:imba values", findings["invalid_ratings"],
        lambda t: f"way `{t[0]}` ({t[1] or 'unnamed'}): `{t[2]}` - "
                  f"valid values are 0-5",
    )
    if findings.get("difficulty_checked"):
        section(
            "Named trails with no difficulty rating",
            findings["named_trails_missing_rating"],
            lambda n: n,
            note="This map displays difficulty, so a named trail with no "
                 "mtb:scale:imba is a genuine gap rather than a style choice. "
                 "Unnamed connectors are deliberately not listed.",
        )
    section(
        "Relations with no name", findings["routes_missing_name"],
        _relation_link,
    )
    section(
        "Relations with no colour", findings["routes_missing_colour"],
        lambda t: f"`{t[0]}` {t[1] or '(unnamed)'} - {_relation_link(t[0])}",
        note="These route relations have no `colour` tag in OSM, so a "
             "consumer drawing them from OSM data alone has no route color.",
    )
    section(
        "Trail markers far from any trail", findings["orphan_pois"],
        lambda o: f"{o['name']}: {o['distance_m']:.0f} m from the nearest "
                  f"trail at "
                  f"https://www.openstreetmap.org/#map=18/{o['lat']:.6f}/{o['lon']:.6f}",
        note="Either misplaced, or attached to a trail this map's relations "
             "don't include.",
    )
    section(
        "Relations that list a way more than once",
        findings["repeated_members"],
        _render_repeated,
        note="A route relation describes the signed route, so each way "
             "normally appears once. A repeat is counted toward the route's "
             "distance as an extra pass. Confirm each one is intended, such "
             "as an out-and-back course, and remove the rest.",
    )
    return "\n".join(out)


def report_path(cache_dir, slug):
    return os.path.join(cache_dir, "osm_diff", slug, "data-notes.md")


# The checks whose findings are lists, in report order. Shared by the total,
# the JSON sidecar and summarize's labels so a new check cannot be counted in
# one place and missed in another.
_LIST_KEYS = (
    "routes_missing_name", "routes_missing_colour",
    "named_trails_missing_rating", "invalid_ratings",
    "probable_gaps", "orphan_pois", "repeated_members",
)


def sidecar_path(cache_dir, slug):
    return os.path.join(cache_dir, "osm_diff", slug, "data-notes.json")


def sidecar(findings, slug, lines):
    """The machine-readable twin of the markdown report.

    An orchestrator building many maps in one run reads this after each
    engine subprocess and prints one aggregated block at the end, where a
    colored heading scrolled off twenty maps ago cannot. Written on every
    audit, clean maps included, so "no file" means "not audited" and
    "total 0" means clean. ``summary`` repeats the console lines verbatim
    so the aggregator never re-derives wording; ``counts`` is per check for
    anyone who wants to filter.
    """
    return {
        "slug": slug,
        "total": findings.get("total", 0),
        "summary": list(lines),
        "counts": {k: len(findings.get(k) or ()) for k in _LIST_KEYS},
        "report": "data-notes.md",
    }


def report_tagging_quality(trails_geojson, pois_geojson, config, cache_dir):
    """Audit, print only if something turned up, always write the report.

    Never raises: a data-quality note must not be able to fail a build.
    """
    if not trails_geojson:
        # No readable OSM snapshot to audit. Silence, not a warning. A
        # route-only map's snapshot is an empty skeleton, which audits clean.
        # Drop any report left by an earlier build, or an orchestrator
        # aggregating the sidecars would keep repeating findings this
        # map no longer has.
        slug = config.get("slug", "map")
        for stale in (report_path(cache_dir, slug), sidecar_path(cache_dir, slug)):
            try:
                os.remove(stale)
            except OSError:
                pass
        return
    try:
        findings = audit(trails_geojson, pois_geojson, config)
    except Exception as e:
        console.warn(f"OSM data notes failed: {e}")
        return

    lines = summarize(findings)
    slug = config.get("slug", "map")
    path = report_path(cache_dir, slug)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(format_report(findings, slug))
        with open(sidecar_path(cache_dir, slug), "w", encoding="utf-8") as f:
            json.dump(sidecar(findings, slug, lines), f, indent=2)
            f.write("\n")
    except OSError as e:
        console.warn(f"could not write OSM data notes: {e}")
        return

    if lines:
        console.step("OSM data notes", attention=True)
        for line in lines:
            console.info(f"  {line}")
        console.info(f"  details: {console.rel_path(path)}")
