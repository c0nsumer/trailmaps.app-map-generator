#!/usr/bin/env python3
"""Generated basemap paths.

The Protomaps basemap draws every path in the area as a pale line,
including the ones this map draws as routes, where the line shows
beside the lanes and through dashes. This module replaces the
basemap's `kind=path` features with ones generated here, from the same
OpenStreetMap data, in the same Protomaps `roads` schema, with one
difference: a stretch that a route draws is flagged with the
visibility buckets that draw it (`tm_s` summer, `tm_w` winter, `tm_e`
emergency), and the runtime hides a flagged stretch only while one of
its buckets is on. So a winter-only trail is still a path on the
summer map, and nothing shows under a route that is on.

Everything else in the basemap is left exactly as Protomaps made it:
`tile-join` strips `kind=path` from the extract and folds the
generated features into the same `roads` source-layer, so the style,
the service worker and the file name do not change.

Design record: .claude/plans/plugin-default-and-custom-basemaps.md,
"3a results".
"""

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile

import console
import overpass
from pmtiles_util import find_pmtiles_cli
from shapely.geometry import LineString, MultiLineString, box
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree

# Bump when the generated features change shape for the same input, so
# existing basemaps regenerate.
SCHEMA_VERSION = 2

# highway values Protomaps files under kind=path, with the min_zoom it
# gives each (read off live Protomaps extracts, 2026-09-20). A feature
# with min_zoom N first appears in tile zoom N - 1.
PATH_MIN_ZOOM = {
    "track": 13,
    "pedestrian": 13,
    "path": 14,
    "cycleway": 14,
    "footway": 14,
    "bridleway": 14,
    "steps": 14,
    "corridor": 15,
}
FOOTWAY_DETAIL_MIN_ZOOM = {"sidewalk": 15, "crossing": 15}
PIER_MIN_ZOOM = 14
RESTRICTED_MIN_ZOOM = 16

# The lowest tile zoom that carries any path (track, min_zoom 13).
PATHS_MIN_TILE_ZOOM = 12
# Protomaps carries name and ref from this tile zoom up (its minor-road
# labels start at 15), which lets the zooms below merge into long runs.
NAME_TILE_ZOOM = 14

# A stretch of basemap path is "drawn" where it lies this close to a
# route. Matches are geometrically exact in practice (same OSM way), so
# this only has to absorb coordinate rounding; measured on MFO, all of
# the 5.3 km that id matching misses is caught at 1 m.
DRAWN_TOLERANCE_M = 2.0
# ...and only where at least this much of it does. A footway crossing a
# road the route follows, or a side trail touching the route at a
# junction, is near the route for a few meters and is not the route.
DRAWN_MIN_LENGTH_M = 10.0

BUCKET_FLAGS = (("summer", "tm_s"), ("winter", "tm_w"), ("emergency", "tm_e"))

STRIP_PATHS_FILTER = '{"roads":["!=","kind","path"]}'


class BasemapPathsError(Exception):
    """Generation cannot proceed; the message says what to do."""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def find_tools():
    """Paths of tippecanoe and tile-join, or (None, None)."""
    return shutil.which("tippecanoe"), shutil.which("tile-join")


def require_tools():
    tippecanoe, tile_join = find_tools()
    if not tippecanoe or not tile_join:
        raise BasemapPathsError(
            "basemap_source: generated needs tippecanoe and tile-join on PATH "
            "(Debian/Ubuntu: apt install tippecanoe; others: "
            "https://github.com/felt/tippecanoe). To build without them, set "
            "basemap_source: protomaps in the config, which keeps the plain "
            "Protomaps basemap."
        )
    return tippecanoe, tile_join


# ---------------------------------------------------------------------------
# Area
# ---------------------------------------------------------------------------


def _tile_x(lon, z):
    return (lon + 180.0) / 360.0 * (1 << z)


def _tile_y(lat, z):
    r = math.radians(lat)
    return (1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * (1 << z)


def _lon_of(x, z):
    return x / (1 << z) * 360.0 - 180.0


def _lat_of(y, z):
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / (1 << z)))))


def tile_cover_bounds(bounds, z=PATHS_MIN_TILE_ZOOM):
    """`bounds` (w, s, e, n) grown outward to whole tiles at zoom `z`.

    The Protomaps extract holds whole tiles, so its edge tiles reach
    past the requested bbox, and stripping removes Protomaps' paths
    from all of them. Paths generated for the bbox alone would stop at
    a straight line inside those tiles, in view at low zoom near the
    pan limit. Whole tiles at the lowest zoom that carries paths cover
    every higher zoom's edge tiles too.
    """
    w, s, e, n = bounds
    x0, x1 = math.floor(_tile_x(w, z)), math.floor(_tile_x(e, z)) + 1
    y0, y1 = math.floor(_tile_y(n, z)), math.floor(_tile_y(s, z)) + 1
    return (_lon_of(x0, z), _lat_of(y1, z), _lon_of(x1, z), _lat_of(y0, z))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def is_path_class(tags):
    return tags.get("highway") in PATH_MIN_ZOOM or tags.get("man_made") == "pier"


def overpass_query(bounds):
    w, s, e, n = bounds
    highways = "|".join(sorted(PATH_MIN_ZOOM))
    area = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    return (
        "[out:json][timeout:180];"
        f'(way["highway"~"^({highways})$"]{area};'
        f'way["man_made"="pier"]{area};);'
        "out tags geom;"
    )


def overpass_cache_path(bounds, cache_dir):
    """Where overpass.query caches this area's response (same keying)."""
    h = hashlib.md5(overpass_query(bounds).encode()).hexdigest()[:12]
    return os.path.join(cache_dir, f"overpass_{h}.json")


def fetch_ways(bounds, cache_dir, refresh=False):
    """Every path-class way in `bounds`: {id: (tags, [(lon, lat), ...])}."""
    data = overpass.query(
        overpass_query(bounds), cache_dir=cache_dir, label="basemap paths", refresh=refresh
    )
    ways = {}
    for el in data.get("elements", []):
        geom = el.get("geometry") or []
        if el.get("type") == "way" and len(geom) >= 2:
            ways[el["id"]] = (el.get("tags", {}), [(p["lon"], p["lat"]) for p in geom])
    return ways


def local_file_ways(nodes, ways):
    """Path-class ways of a parsed local .osm file, same shape as fetch_ways."""
    out = {}
    for way_id, way in ways.items():
        if not is_path_class(way["tags"]):
            continue
        coords = [nodes[ref][:2] for ref in way["nd_refs"] if ref in nodes]
        if len(coords) >= 2:
            out[way_id] = (way["tags"], coords)
    return out


def merge_sources(fetched, local):
    """The local file wins per way.

    A map built from a local .osm save draws its routes from that file,
    which can hold trails moved, split or added since the last upload.
    Taking each way the file contains from the file makes the basemap
    agree with the routes by construction along the trail corridors;
    every other way keeps live OSM's coverage, which a save (a patchwork
    of download rectangles, 21 percent of the area on MFO) does not
    have. JOSM downloads ways whole, so there is no seam to stitch.
    """
    merged = dict(fetched)
    merged.update(local)
    return merged


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def to_props(tags):
    """OSM tags to the Protomaps `roads` properties the style reads."""
    highway = tags.get("highway")
    if highway in PATH_MIN_ZOOM:
        detail, min_zoom = highway, PATH_MIN_ZOOM[highway]
        if highway == "footway" and tags.get("footway") in FOOTWAY_DETAIL_MIN_ZOOM:
            detail = tags["footway"]
            min_zoom = FOOTWAY_DETAIL_MIN_ZOOM[detail]
    else:
        detail, min_zoom = "pier", PIER_MIN_ZOOM
    props = {"kind": "path", "kind_detail": detail}
    if tags.get("access") in ("private", "no"):
        props["access"] = tags["access"]
        min_zoom = max(min_zoom, RESTRICTED_MIN_ZOOM)
    props["min_zoom"] = min_zoom
    for key in ("name", "ref"):
        if tags.get(key):
            props[key] = tags[key]
    if tags.get("oneway") == "yes":
        props["oneway"] = "yes"
    if tags.get("bridge") not in (None, "no"):
        props["is_bridge"] = True
    if tags.get("tunnel") not in (None, "no"):
        props["is_tunnel"] = True
    return props


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _lines(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, LineString)]


class _Plane:
    """Lon/lat to local meters and back; ample for a map-sized area."""

    def __init__(self, lat0):
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110540.0

    def to_m(self, coords):
        return [(x * self.kx, y * self.ky) for x, y in coords]

    def to_deg(self, coords):
        return [(x / self.kx, y / self.ky) for x, y in coords]


def drawn_covers(trails_geojson, plane):
    """Per bucket flag, the area within DRAWN_TOLERANCE_M of what it draws.

    Returns {flag: (STRtree, [buffered polygons])}, only for buckets
    that draw anything.
    """
    routes = (trails_geojson.get("metadata") or {}).get("routes") or {}
    by_flag = {flag: [] for _, flag in BUCKET_FLAGS}
    for feature in trails_geojson.get("features", []):
        info = routes.get(str(feature["properties"].get("route_id")), {})
        geom = feature.get("geometry") or {}
        parts = geom.get("coordinates") or []
        if geom.get("type") == "LineString":
            parts = [parts]
        elif geom.get("type") != "MultiLineString":
            continue
        for bucket, flag in BUCKET_FLAGS:
            # Buckets are absent from old snapshots; a route with none
            # named is a summer route, as it is in the app.
            on = info.get(bucket) if any(b in info for b, _ in BUCKET_FLAGS) else bucket == "summer"
            if not on:
                continue
            for part in parts:
                if len(part) >= 2:
                    by_flag[flag].append(LineString(plane.to_m(part)).buffer(DRAWN_TOLERANCE_M))
    return {flag: (STRtree(polys), polys) for flag, polys in by_flag.items() if polys}


def split_by_drawn(line, covers):
    """Cut `line` (meters) where routes draw it: [(LineString, flags), ...].

    `flags` is the sorted tuple of bucket flags drawing that piece, empty
    for the part no route draws.
    """
    pieces = [(line, ())]
    for flag, (tree, polys) in covers.items():
        nxt = []
        for piece, flags in pieces:
            near = [polys[i] for i in tree.query(piece)]
            if not near:
                nxt.append((piece, flags))
                continue
            cover = unary_union(near)
            inside = [
                g for g in _lines(piece.intersection(cover)) if g.length >= DRAWN_MIN_LENGTH_M
            ]
            if not inside:
                nxt.append((piece, flags))
                continue
            drawn = unary_union(inside)
            nxt += [(g, tuple(sorted(flags + (flag,)))) for g in inside]
            # buffer(0.01): the difference of a line with a piece of
            # itself leaves zero-length slivers at the cut points
            nxt += [
                (g, flags) for g in _lines(piece.difference(drawn.buffer(0.01))) if g.length > 0.05
            ]
        pieces = nxt
    return pieces


def build_features(ways, trails_geojson, bounds, minzoom, maxzoom, merge=True):
    """GeoJSON features for tippecanoe, plus counters for the build log.

    `bounds` is the extract's requested bbox. Features are emitted one
    tile zoom at a time, each zoom clipped to the whole tiles that bbox
    touches at that zoom, which is exactly the extract's tile set. A
    path left running past it would make tippecanoe write a tile the
    extract does not have, holding paths and nothing else, and MapLibre
    would show that in place of the overscaled parent tile: white lines
    on bare background along the map's edge.
    """
    w, s, e, n = bounds
    plane = _Plane((s + n) / 2.0)
    covers = drawn_covers(trails_geojson, plane)
    first_zoom = max(minzoom, PATHS_MIN_TILE_ZOOM)

    def frame_at(z):
        fw, fs, fe, fn = tile_cover_bounds(bounds, z)
        return box(fw * plane.kx, fs * plane.ky, fe * plane.kx, fn * plane.ky)

    outer = frame_at(first_zoom)
    stats = {"ways": len(ways), "drawn_m": 0.0, "pieces": 0, "lines": 0}
    pieces = []  # (props, LineString in meters)
    for way_id in sorted(ways):
        tags, coords = ways[way_id]
        for part in _lines(LineString(plane.to_m(coords)).intersection(outer)):
            for piece, flags in split_by_drawn(part, covers) if covers else [(part, ())]:
                props = to_props(tags)
                for flag in flags:
                    props[flag] = 1
                if flags:
                    stats["drawn_m"] += piece.length
                pieces.append((props, piece))

    features = []
    for z in range(first_zoom, maxzoom + 1):
        frame = frame_at(z)
        groups = {}
        for props, piece in pieces:
            # maxzoom tiles also serve every zoom above them
            if props["min_zoom"] - 1 > z and z < maxzoom:
                continue
            shown = props
            if z < NAME_TILE_ZOOM:
                shown = {k: v for k, v in props.items() if k not in ("name", "ref")}
            for g in _lines(piece.intersection(frame)):
                groups.setdefault(json.dumps(shown, sort_keys=True), []).append(g)
        for key in sorted(groups):
            lines = groups[key]
            # Exclusion has already happened, so merging cannot hide it;
            # line labels need the run, not the fragments.
            merged = _lines(linemerge(lines)) if merge and len(lines) > 1 else lines
            # One multi-line feature per attribute group, which is how
            # Protomaps packs its own roads: a tile then carries the
            # group's tags once, not once per trail. A feature per line
            # made River Bends' basemap 9.3 percent larger than the
            # plain extract; this makes it 1.9. No feature id either:
            # nothing reads one, and it is bytes per feature per tile.
            features.append(
                {
                    "type": "Feature",
                    "properties": json.loads(key),
                    "tippecanoe": {"layer": "roads", "minzoom": z, "maxzoom": z},
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [
                            [[round(x, 7), round(y, 7)] for x, y in plane.to_deg(g.coords)]
                            for g in merged
                        ],
                    },
                }
            )
            stats["lines"] += len(merged)
    stats["pieces"] = len(features)
    return features, stats


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------


def _run(cmd, cwd):
    # TMPDIR too: tippecanoe and tile-join keep their own scratch files
    # wherever it points, and that must be the work directory's disk,
    # not /tmp (see tile_and_join).
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, "TMPDIR": cwd}
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise BasemapPathsError(f"{os.path.basename(cmd[0])} failed: " + " | ".join(tail))


def archive_ok(path):
    """True when `pmtiles verify` accepts the archive at `path`.

    tile-join exits 0 after running out of disk, leaving a complete
    header over a body that stops short (drvg and riverbends,
    2026-09-21, 1.0 of 7.2 MB and 3.3 of 4.7 MB). Nothing downstream
    notices until a tile past the cut is read, which for a rider is a
    basemap that goes blank partway across the map. The check reads the
    directories, not the tiles: 14 ms on a 20 MB archive.
    """
    cli = find_pmtiles_cli()
    if not cli or not os.path.exists(path):
        return False
    return subprocess.run([cli, "verify", path], capture_output=True).returncode == 0


def tile_and_join(features, extract_path, output_path, bounds, minzoom, maxzoom, work_root=None):
    """Fold `features` into a copy of the Protomaps extract at `output_path`.

    Runs in a temp directory with bare file names, because tippecanoe
    and tile-join write their command lines into the archive metadata
    and would otherwise publish this machine's paths in every map.

    `work_root` is where that directory is made, and build.py points it
    into the cache dir. The system temp dir is the wrong place: on many
    Linux boxes /tmp is a RAM disk of a few GB shared with everything
    else, and this step writes several copies of a basemap there.
    """
    tippecanoe, tile_join = require_tools()
    paths_min = max(minzoom, PATHS_MIN_TILE_ZOOM)
    if work_root:
        os.makedirs(work_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="basemap-paths-", dir=work_root) as tmp:
        with open(os.path.join(tmp, "paths.geojson"), "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
        shutil.copy2(extract_path, os.path.join(tmp, "protomaps.pmtiles"))
        # The feature and size limits drop and thin features to meet a
        # tile budget, which is wrong for a layer this small.
        _run(
            [
                tippecanoe,
                "-q",
                "-f",
                "-o",
                "paths.pmtiles",
                f"-Z{paths_min}",
                f"-z{maxzoom}",
                "-l",
                "roads",
                "--no-feature-limit",
                "--no-tile-size-limit",
                "paths.geojson",
            ],
            tmp,
        )
        # tippecanoe also writes the neighbors of every edge tile, for
        # the sliver of path inside their margin. Those tiles are not in
        # the Protomaps extract, and a tile holding paths and nothing
        # else is what MapLibre would then show out there in place of
        # the overscaled parent. The same `pmtiles extract --bbox` that
        # cut the Protomaps extract cuts this one to the same tile set.
        _run(
            [
                find_pmtiles_cli(),
                "extract",
                "paths.pmtiles",
                "paths-cut.pmtiles",
                "--bbox=" + ",".join(f"{v}" for v in bounds),
                f"--minzoom={paths_min}",
                f"--maxzoom={maxzoom}",
            ],
            tmp,
        )
        # Two steps: -j applies to every input, so stripping in the same
        # call as the join would strip the generated paths too.
        _run(
            [
                tile_join,
                "-q",
                "-f",
                "-pk",
                "-j",
                STRIP_PATHS_FILTER,
                "-o",
                "stripped.pmtiles",
                "protomaps.pmtiles",
            ],
            tmp,
        )
        _run(
            [
                tile_join,
                "-q",
                "-f",
                "-pk",
                "-n",
                "Protomaps Basemap with generated paths",
                "-N",
                "Protomaps basemap layers; kind=path features generated from OpenStreetMap",
                "-o",
                "joined.pmtiles",
                "stripped.pmtiles",
                "paths-cut.pmtiles",
            ],
            tmp,
        )
        staged = output_path + ".tmp"
        shutil.copy2(os.path.join(tmp, "joined.pmtiles"), staged)
        # Verified where it will be served from, after the last copy,
        # and only then moved into place.
        if not archive_ok(staged):
            os.remove(staged)
            raise BasemapPathsError(
                "the generated basemap failed `pmtiles verify` (a truncated write, "
                f"usually a full disk under {work_root or tempfile.gettempdir()}); "
                "nothing was replaced. Free some space and build again."
            )
        os.replace(staged, output_path)


# ---------------------------------------------------------------------------
# Entry point for build.py
# ---------------------------------------------------------------------------


def trails_digest(trails_geojson):
    """What the flags depend on: each route's line and its buckets.

    Not the file on disk, which also carries build dates and stats that
    change without moving a trail.
    """
    routes = (trails_geojson.get("metadata") or {}).get("routes") or {}
    h = hashlib.sha256()
    for rid in sorted(routes, key=str):
        h.update(json.dumps([rid, [bool(routes[rid].get(b)) for b, _ in BUCKET_FLAGS]]).encode())
    for feature in trails_geojson.get("features", []):
        h.update(
            json.dumps([feature["properties"].get("route_id"), feature.get("geometry")]).encode()
        )
    return h.hexdigest()[:16]


def is_generated_signature(signature):
    """True for a basemap this module wrote, False for a plain extract's."""
    return isinstance(signature, str) and signature.startswith("paths-v")


def input_signature(bbox_signature, trails_geojson, ways_cache_path, osm_file_path=None):
    """What the generated basemap depends on, as one string.

    The Overpass snapshot and the local file are identified by size and
    mtime rather than content: they are large, and both only change
    when something rewrites them.
    """
    parts = [f"paths-v{SCHEMA_VERSION}", str(bbox_signature), trails_digest(trails_geojson)]
    for path in (ways_cache_path, osm_file_path):
        if path and os.path.exists(path):
            st = os.stat(path)
            parts.append(f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}")
    return "|".join(parts)


def generate(
    config,
    trails_geojson,
    extract_path,
    output_path,
    cache_dir,
    bounds,
    minzoom,
    maxzoom,
    refresh=False,
    osm_file_path=None,
):
    """Write the basemap with generated paths to `output_path`."""
    require_tools()
    cover = tile_cover_bounds(bounds)
    ways = fetch_ways(cover, cache_dir, refresh=refresh)
    fetched = len(ways)
    local = 0
    if osm_file_path:
        import osm_parser

        nodes, file_ways, _ = osm_parser.parse_osm_file(osm_file_path)
        from_file = local_file_ways(nodes, file_ways)
        local = len(from_file)
        ways = merge_sources(ways, from_file)
    features, stats = build_features(ways, trails_geojson, bounds, minzoom, maxzoom)
    tile_and_join(
        features,
        extract_path,
        output_path,
        bounds,
        minzoom,
        maxzoom,
        work_root=os.path.join(cache_dir, "basemap", "work"),
    )
    source = f"{fetched} ways from Overpass" + (
        f", {local} from {os.path.basename(osm_file_path)}" if local else ""
    )
    console.info(
        f"Basemap paths: {source}; {stats['lines']} lines in {stats['pieces']} features, "
        f"{stats['drawn_m'] / 1000:.1f} km flagged as drawn by this map"
    )
    return stats
