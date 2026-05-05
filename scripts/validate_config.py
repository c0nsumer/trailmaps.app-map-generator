#!/usr/bin/env python3
"""Strict validator/linter for trailmaps.app Map Generator YAML configs.

Run automatically at the top of `build.py`, or standalone:

    python scripts/validate_config.py configs/ramba/ramba.yaml [configs/dte/dte.yaml ...]

Catches the kinds of mistakes the build pipeline silently swallows:
unknown top-level keys (typos like `Default_Direction_Schedule`), wrong
types, out-of-range enum values, illegal cross-references between keys,
malformed colors, malformed bboxes, missing required keys, and missing
asset files.

All errors are collected then reported together — the goal is "fix the
config in one pass" rather than "fix one error, rerun, fix the next".
Exit code is 0 on success, 1 on any error. Warnings (e.g. file-not-found
for assets that may be populated later) print but do not fail the build.
"""

import difflib
import os
import re
import sys
from pathlib import Path

import yaml

# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------

# Top-level keys that are required. Empty values still fail.
# `relations` carries an additional non-empty check via _validate_relations
# below (an empty list is structurally a value but useless: the build has
# no source data to fetch).
REQUIRED_KEYS = {"name", "slug", "relations"}

# All known top-level keys with expected Python types. None means "no type
# check" (handled by a custom validator below). Using tuples for "any of".
#
# Keep this list in sync with:
#   - scripts/build.py CONFIG_SPEC
#   - scripts/build.py inject_config_into_template() custom-logic block
#   - scripts/fetch_trails.py / fetch_pois.py / fetch_basemap.py /
#     fetch_terrain.py / generate_icons.py config.get() lookups
KNOWN_KEYS = {
    # Identity / required
    "name":                          str,
    "slug":                          str,
    "title":                         str,

    # Map view / geometry
    "bbox":                          list,
    "pan_bbox":                      list,
    "pan_padding":                   (int, float),
    "center":                        list,
    "zoom":                          (int, float),
    "min_zoom":                      (int, float),
    "max_zoom":                      (int, float),
    "basemap_maxzoom":               (int, float),
    "terrain_maxzoom":               (int, float),

    # Data sources. `relations` is the unified source list: every entry
    # may be a leaf route relation OR a super-relation (auto-expanded
    # to its child routes one level deep). It replaces the historical
    # `root_relation_id` (scalar) + `extra_relations` (list) split.
    "relations":                     list,
    "osm_file":                      str,
    "clipped_relations":             list,
    "winter_relations":              list,
    "summer_relations":              list,
    "emergency_access_relations":    list,
    "custom_routes":                 list,

    # Per-relation overrides (dicts keyed by integer relation IDs)
    "relation_colors":               dict,
    "dashed_relations":              dict,
    "default_direction_schedule":    dict,
    "direction_schedules":           dict,

    # Display options
    "default_labels":                str,
    "color_by":                      str,
    "default_trail_color":           (str, dict),
    "marker_color":                  str,
    "marker_text_color":             str,
    "marker_border_color":           str,
    "parking_color":                 str,
    "parking_text_color":            str,
    "parking_border_color":          str,
    "trailhead_color":               str,
    "trailhead_text_color":          str,
    "trailhead_border_color":        str,
    "feature_color":                 str,
    "feature_ring_color":            str,

    # Show/hide toggles (gate data-fetching and build-time asset gen;
    # UI visibility lives in localStorage). show_markers covers the
    # merged guideposts + emergency-access-point layer.
    "show_markers":                  bool,
    "show_features":                 bool,
    "show_parking":                  bool,
    "show_trailheads":               bool,
    "show_toilets":                  bool,
    "show_drinking_water":           bool,
    "show_terrain":                  bool,
    "show_difficulty":               bool,
    "show_routes":                   bool,
    "show_trails":                   bool,
    "direction_arrows_required":     bool,
    "suppress_path_labels":          bool,
    "suppress_basemap_pois":         bool,
    "map_dim_on_highlight":          bool,
    "url_hash":                      bool,
    "poi_proximity_m":               (int, float),
    "show_route_distance":           bool,
    "show_route_elevation":          bool,
    "distance_units":                str,
    "share_button":                  bool,

    # User-supplied feature data
    "trailheads":                    list,
    "parking":                       list,
    "base_layers":                   list,

    # Branding / chrome
    "logo":                          str,
    "icon":                          str,
    "about":                         dict,
    "welcome":                       (dict, bool),
    "default_visible":               (list, str),
    "accent_color":                  str,
    "default_color_scheme":          str,
    "invert_logo_dark":              bool,
    "pwa":                           bool,
    "pwa_install_prompt":            bool,

    # Event mode: feature one or more routes prominently while every
    # other trail on the map renders as muted background context.
    # Build-time pre-pass translates this into per-route overrides
    # (relation_colors, dashed_relations, custom_routes mutation);
    # nothing flows directly to the runtime CONFIG.
    "event_mode":                    dict,

    # Output
    "output_dir":                    str,
}

# Keys that intentionally DO NOT flow through CONFIG_SPEC into the runtime
# JS CONFIG object. Each is consumed entirely at build time by one of the
# fetch_* scripts or by build.py's bbox math, and the runtime has no use
# for the value. The drift lint (`assert_spec_coverage`) accepts these as
# legitimate omissions.
BUILD_ONLY_KEYS = {
    # OSM data fetching (fetch_trails.py, fetch_pois.py, osm_parser.py)
    "osm_file",
    "relations",
    "clipped_relations",
    "winter_relations",
    "summer_relations",
    "emergency_access_relations",
    # Route stats: gates the build-time computation in
    # compute_route_stats.py. The values themselves flow into the
    # runtime via per-route metadata (CONFIG.routes[id].distance_m /
    # elevation_gain_m), not through CONFIG_SPEC. Both are pure
    # build-time gates from the config schema's perspective.
    "show_route_distance",
    "show_route_elevation",
    # Style overrides folded into per-route metadata at build time
    # (relation_colors / dashed_relations / *_direction_schedule are
    # consumed in inject_config_into_template's pre-pass and emerge
    # in CONFIG.routes / CONFIG.directionSchedules).
    "relation_colors",
    "dashed_relations",
    "default_direction_schedule",
    "direction_schedules",
    # Build-time bbox / tile-extract knobs
    "pan_padding",        # consumed by expand_bbox_for_pan; runtime sees pan_bbox
    "basemap_maxzoom",    # consumed by fetch_basemap.py
    "terrain_maxzoom",    # consumed by fetch_terrain.py
    # User-supplied points consumed by fetch_pois.py and baked into
    # pois.geojson; the runtime reads pois.geojson, not CONFIG.trailheads.
    "trailheads",
    # Build output destination
    "output_dir",
}

# Keys whose YAML name doesn't match a CONFIG_SPEC entry directly because
# build.py's inject_config_into_template runs custom logic on them before
# emitting a derived field (or set of fields) into CONFIG. Listed here so
# the drift lint accepts them as covered.
HANDLED_SPECIALLY = {
    "base_layers",          # → CONFIG.baseLayers
    "custom_routes",        # → CONFIG.customRoutes (subset of fields)
    "default_trail_color",  # → CONFIG.defaultTrailColor + dash + cap
    "about",                # → CONFIG.about (object passed through)
    "welcome",              # → CONFIG.welcome (object or false; passed through)
    "default_visible",      # → CONFIG.defaultVisible (list; "all" expanded at build time)
    "accent_color",         # → CONFIG.accentColor (hex; "auto" resolved from logo at build time)
    "logo",                 # → CONFIG.logoUrl (after asset pipeline)
    "icon",                 # → fallback for logoUrl
    "event_mode",           # → consumed by _apply_event_mode pre-pass; emits
                            #   per-route overrides into relation_colors /
                            #   dashed_relations / custom_routes mutations.
}

VALID_LABELS = {"routes", "trails", "none"}
VALID_COLOR_BY = {"relation", "trail"}
VALID_DISTANCE_UNITS = {"mi", "km"}
VALID_COLOR_SCHEMES = {"light", "dark", "auto"}
VALID_DAYS = {"sunday", "monday", "tuesday", "wednesday",
              "thursday", "friday", "saturday",
              # Parity tokens: reverse on even or odd calendar dates
              # (getDate()%2). Must stay in sync with build.py's VALID_DAYS
              # and app.js todaysReverseRoutes().
              "even_days", "odd_days"}

HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
# Colors can also be CSS named colors. We don't enumerate the full set —
# instead we accept anything matching a tame ASCII identifier alongside
# the hex form. This is permissive on purpose: CSS color names are stable
# and a typo like "wite" would render as transparent at the browser, not
# crash the build. The hex check above is the high-value catch.
NAMED_COLOR_RE = re.compile(r"^[a-zA-Z]+$")

# Valid GeoJSON geometry types for custom_routes.
VALID_CUSTOM_GEOMETRY_TYPES = {"LineString", "MultiLineString"}


# ----------------------------------------------------------------------
# Result helpers
# ----------------------------------------------------------------------

class _Report:
    """Collect errors and warnings during a single validation run."""

    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append(f"  [error] {where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"  [warn]  {where}: {msg}")


# ----------------------------------------------------------------------
# Type & value helpers
# ----------------------------------------------------------------------

def _type_name(t):
    if isinstance(t, tuple):
        return " or ".join(_type_name(x) for x in t)
    return {int: "int", float: "float", str: "str",
            bool: "bool", list: "list", dict: "dict"}.get(t, t.__name__)


def _check_type(report, where, value, expected):
    """Type-check that handles the bool-is-int gotcha.

    YAML loads `true`/`false` as Python bool, which is a subclass of int.
    A field declared `int` should NOT accept a bool, so we filter it
    explicitly. The reverse isn't a concern (bool fields never get ints).
    """
    if isinstance(value, bool) and expected is not bool and (
        not isinstance(expected, tuple) or bool not in expected
    ):
        report.err(where, f"expected {_type_name(expected)}, got bool")
        return False
    if not isinstance(value, expected):
        report.err(where,
                   f"expected {_type_name(expected)}, "
                   f"got {type(value).__name__}")
        return False
    return True


def _is_color(value):
    if not isinstance(value, str):
        return False
    return bool(HEX_COLOR_RE.match(value) or NAMED_COLOR_RE.match(value))


# ----------------------------------------------------------------------
# Per-key validators
# ----------------------------------------------------------------------

_LEGACY_KEYS = {"root_relation_id", "extra_relations"}


def _validate_unknown_keys(report, config):
    """Catch typos in top-level keys via fuzzy match against KNOWN_KEYS."""
    for key in config.keys():
        if key in KNOWN_KEYS:
            continue
        # Internal fields populated by the build pipeline shouldn't error
        # if they leak in (defensive — users won't write these).
        if key.startswith("_"):
            continue
        # Legacy keys handled by `_validate_legacy_keys` get a pointed
        # migration message there; suppress the generic "unknown key"
        # warning so the curator sees one clear error per key, not two.
        if key in _LEGACY_KEYS:
            continue
        suggestions = difflib.get_close_matches(key, KNOWN_KEYS.keys(), n=2)
        hint = f" — did you mean {' or '.join(repr(s) for s in suggestions)}?" \
               if suggestions else ""
        report.err(key, f"unknown top-level key{hint}")


def _validate_required(report, config):
    for key in REQUIRED_KEYS:
        if key not in config or config[key] in (None, ""):
            report.err(key, "required key is missing or empty")


def _validate_types(report, config):
    for key, value in config.items():
        if key not in KNOWN_KEYS or value is None:
            continue
        expected = KNOWN_KEYS[key]
        _check_type(report, key, value, expected)


def _validate_enums(report, config):
    if "default_labels" in config and config["default_labels"] not in VALID_LABELS:
        report.err("default_labels",
                   f"must be one of {sorted(VALID_LABELS)}, "
                   f"got {config['default_labels']!r}")

    if "color_by" in config and config["color_by"] not in VALID_COLOR_BY:
        report.err("color_by",
                   f"must be one of {sorted(VALID_COLOR_BY)}, "
                   f"got {config['color_by']!r}")

    if "distance_units" in config and config["distance_units"] not in VALID_DISTANCE_UNITS:
        report.err("distance_units",
                   f"must be one of {sorted(VALID_DISTANCE_UNITS)}, "
                   f"got {config['distance_units']!r}")

    if ("default_color_scheme" in config
            and config["default_color_scheme"] not in VALID_COLOR_SCHEMES):
        report.err("default_color_scheme",
                   f"must be one of {sorted(VALID_COLOR_SCHEMES)}, "
                   f"got {config['default_color_scheme']!r}")

    # (Historical note: an earlier draft cross-checked
    # show_route_elevation against show_terrain because the original
    # plan was to sample our own terrain raster for elevation gain.
    # The shipping implementation uses USGS 3DEP's getSamples HTTP
    # endpoint instead, which is independent of the hillshade layer
    # (Mapterhorn). The two settings are orthogonal — elevation stats
    # can be enabled with terrain off, and vice versa. No cross-key
    # check needed.)


def _validate_geometry(report, config):
    """Bbox / center / zoom sanity. Loose ranges so users with edge-case
    setups (Antarctica, antimeridian crossings, etc.) aren't blocked."""
    for bbox_key in ("bbox", "pan_bbox"):
        if bbox_key in config and isinstance(config[bbox_key], list):
            b = config[bbox_key]
            if len(b) != 4:
                report.err(bbox_key, f"must be 4 values [w,s,e,n], got {len(b)}")
            elif not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                         for x in b):
                report.err(bbox_key, "all 4 values must be numbers")
            else:
                w, s, e, n = b
                if not (-180 <= w <= 180 and -180 <= e <= 180):
                    report.err(bbox_key, f"longitudes must be in [-180,180]: {b}")
                if not (-90 <= s <= 90 and -90 <= n <= 90):
                    report.err(bbox_key, f"latitudes must be in [-90,90]: {b}")
                if w >= e:
                    report.err(bbox_key, f"west ({w}) must be < east ({e})")
                if s >= n:
                    report.err(bbox_key, f"south ({s}) must be < north ({n})")

    # pan_padding: negative values would shrink maxBounds below bbox which
    # makes no sense; huge values (>5) mean the pan envelope is 10× the
    # trail extent, almost certainly a typo. Warn rather than error so
    # power users can override for special cases.
    if "pan_padding" in config and isinstance(config["pan_padding"], (int, float)) \
            and not isinstance(config["pan_padding"], bool):
        pp = config["pan_padding"]
        if pp < 0:
            report.err("pan_padding",
                       f"must be >= 0 (0 = no pan room beyond bbox), got {pp}")
        elif pp > 5:
            report.warn("pan_padding",
                        f"unusually large ({pp}); pan envelope will be ~{1+2*pp:.0f}x "
                        "the bbox extent and basemap PMTiles will balloon to match")

    # poi_proximity_m: distance (meters) from the nearest visible trail
    # within which a feature/trail-marker POI is allowed to render.
    # Negative is nonsense; very large values defeat the filter and may
    # surface bbox-incidental POIs the trail map shouldn't claim.
    if "poi_proximity_m" in config \
            and isinstance(config["poi_proximity_m"], (int, float)) \
            and not isinstance(config["poi_proximity_m"], bool):
        pm = config["poi_proximity_m"]
        if pm < 0:
            report.err("poi_proximity_m",
                       f"must be >= 0 (meters from nearest visible trail), got {pm}")
        elif pm > 500:
            report.warn("poi_proximity_m",
                        f"unusually large ({pm} m); the proximity filter is "
                        "effectively disabled and unrelated bbox POIs may render")

    if "center" in config and isinstance(config["center"], list):
        c = config["center"]
        if len(c) != 2:
            report.err("center", f"must be 2 values [lon,lat], got {len(c)}")
        elif not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                     for x in c):
            report.err("center", "both values must be numbers")
        else:
            lon, lat = c
            if not -180 <= lon <= 180:
                report.err("center", f"longitude must be in [-180,180]: {lon}")
            if not -90 <= lat <= 90:
                report.err("center", f"latitude must be in [-90,90]: {lat}")

    for k in ("zoom", "min_zoom", "max_zoom", "basemap_maxzoom", "terrain_maxzoom"):
        if k in config and isinstance(config[k], (int, float)) \
                and not isinstance(config[k], bool):
            if not 0 <= config[k] <= 22:
                report.err(k, f"zoom must be in [0,22], got {config[k]}")

    if "min_zoom" in config and "max_zoom" in config:
        try:
            if config["min_zoom"] > config["max_zoom"]:
                report.err("min_zoom",
                           f"min_zoom ({config['min_zoom']}) "
                           f"> max_zoom ({config['max_zoom']})")
        except TypeError:
            pass  # already reported as a type error above


def _validate_colors(report, config):
    color_keys = ("marker_color", "marker_text_color", "marker_border_color",
                  "parking_color", "parking_text_color", "parking_border_color",
                  "trailhead_color", "trailhead_text_color", "trailhead_border_color",
                  "feature_color", "feature_ring_color")
    for k in color_keys:
        if k in config and not _is_color(config[k]):
            report.err(k, f"not a valid color: {config[k]!r} "
                          f"(expected #RRGGBB, #RGB, #RRGGBBAA, or a CSS color name)")

    dtc = config.get("default_trail_color")
    if dtc is not None:
        if isinstance(dtc, str):
            if not _is_color(dtc):
                report.err("default_trail_color",
                           f"not a valid color: {dtc!r}")
        elif isinstance(dtc, dict):
            if "color" in dtc and not _is_color(dtc["color"]):
                report.err("default_trail_color.color",
                           f"not a valid color: {dtc['color']!r}")

    rc = config.get("relation_colors")
    if isinstance(rc, dict):
        for rid, color in rc.items():
            if not _is_color(color):
                report.err(f"relation_colors[{rid}]",
                           f"not a valid color: {color!r}")


def _validate_relation_id_dicts(report, config):
    """Keys of *_relations dicts must be int (or int-coercible str)."""
    for key in ("relation_colors", "dashed_relations",
                "direction_schedules"):
        d = config.get(key)
        if not isinstance(d, dict):
            continue
        for rid in d.keys():
            if isinstance(rid, int) and not isinstance(rid, bool):
                continue
            if isinstance(rid, str) and rid.lstrip("-").isdigit():
                continue
            report.err(f"{key}", f"key {rid!r} is not an OSM relation ID (int)")

    for key in ("relations", "clipped_relations", "winter_relations",
                "summer_relations", "emergency_access_relations"):
        lst = config.get(key)
        if not isinstance(lst, list):
            continue
        for i, rid in enumerate(lst):
            if isinstance(rid, int) and not isinstance(rid, bool):
                continue
            report.err(f"{key}[{i}]",
                       f"must be an OSM relation ID (int), got {rid!r}")


def _validate_weekdays(report, config):
    """Validate any reverse_days lists. Mirrors build.py's normalise_days()
    accept-prefix logic so the validator agrees with the runtime."""

    def _check_days(where, days):
        if not isinstance(days, list):
            report.err(where, f"reverse_days must be a list, got {type(days).__name__}")
            return
        for d in days:
            dl = str(d).strip().lower()
            match = next((full for full in VALID_DAYS
                          if full == dl or (len(dl) >= 3 and full.startswith(dl))),
                         None)
            if match is None:
                report.err(where,
                           f"unknown day token {d!r}; valid: {sorted(VALID_DAYS)}")

    dds = config.get("default_direction_schedule")
    if isinstance(dds, dict) and "reverse_days" in dds:
        _check_days("default_direction_schedule.reverse_days",
                    dds["reverse_days"])

    ds = config.get("direction_schedules")
    if isinstance(ds, dict):
        for rid, spec in ds.items():
            if not isinstance(spec, dict):
                report.err(f"direction_schedules[{rid}]",
                           f"expected dict, got {type(spec).__name__}")
                continue
            if "reverse_days" in spec:
                _check_days(f"direction_schedules[{rid}].reverse_days",
                            spec["reverse_days"])


def _validate_dashed_relations(report, config):
    dr = config.get("dashed_relations")
    if not isinstance(dr, dict):
        return
    for rid, spec in dr.items():
        where = f"dashed_relations[{rid}]"
        if isinstance(spec, list):
            for i, n in enumerate(spec):
                if not isinstance(n, (int, float)) or isinstance(n, bool):
                    report.err(f"{where}[{i}]",
                               f"dash pattern values must be numbers, got {n!r}")
        elif isinstance(spec, dict):
            if "pattern" in spec:
                p = spec["pattern"]
                if not isinstance(p, list) or not all(
                    isinstance(n, (int, float)) and not isinstance(n, bool)
                    for n in p
                ):
                    report.err(f"{where}.pattern",
                               f"must be a list of numbers, got {p!r}")
            if "cap" in spec and spec["cap"] not in ("butt", "round", "square"):
                report.err(f"{where}.cap",
                           f"must be one of [butt, round, square], "
                           f"got {spec['cap']!r}")
        else:
            report.err(where,
                       f"expected list or dict, got {type(spec).__name__}")


def _validate_point_lists(report, config):
    """trailheads / parking are list-of-dicts with name + coordinates."""
    for key in ("trailheads", "parking"):
        lst = config.get(key)
        if not isinstance(lst, list):
            continue
        for i, item in enumerate(lst):
            where = f"{key}[{i}]"
            if not isinstance(item, dict):
                report.err(where, f"expected dict, got {type(item).__name__}")
                continue
            if "coordinates" not in item:
                report.err(where, "missing required 'coordinates' [lon, lat]")
            else:
                c = item["coordinates"]
                if not (isinstance(c, list) and len(c) == 2
                        and all(isinstance(x, (int, float))
                                and not isinstance(x, bool) for x in c)):
                    report.err(f"{where}.coordinates",
                               f"must be [lon, lat] numbers, got {c!r}")
                else:
                    lon, lat = c
                    if not -180 <= lon <= 180:
                        report.err(f"{where}.coordinates",
                                   f"longitude must be in [-180,180]: {lon}")
                    if not -90 <= lat <= 90:
                        report.err(f"{where}.coordinates",
                                   f"latitude must be in [-90,90]: {lat}")
            if "name" in item and not isinstance(item["name"], str):
                report.err(f"{where}.name",
                           f"must be a string, got {type(item['name']).__name__}")


def _validate_paths(report, config, config_dir):
    """Asset path existence. logo / icon / osm_file and every
    custom_routes[].geometry path are checked via os.path.isfile. Relative
    paths resolve against ``config_dir`` (the directory holding the YAML
    file) — every per-map asset lives next to its config. Missing paths
    are errors; surfacing them here gives a clear single message naming
    the config and field rather than an opaque build failure."""
    def _full(p):
        if os.path.isabs(p):
            return p
        return os.path.join(config_dir, p) if config_dir else p

    for key in ("logo", "icon", "osm_file"):
        p = config.get(key)
        if not p:
            continue
        full = _full(p)
        if not os.path.isfile(full):
            report.err(key, f"file not found: {p} (resolved to {full})")

    cr = config.get("custom_routes")
    if isinstance(cr, list):
        for i, entry in enumerate(cr):
            if not isinstance(entry, dict):
                continue
            p = entry.get("geometry")
            if not isinstance(p, str) or not p:
                continue
            full = _full(p)
            if not os.path.isfile(full):
                report.err(f"custom_routes[{i}].geometry",
                           f"file not found: {p} (resolved to {full})")

    # Inline event_mode.routes share the geometry-path semantics with
    # top-level custom_routes (relative to the config YAML's directory).
    em = config.get("event_mode")
    if isinstance(em, dict):
        em_routes = em.get("routes")
        if isinstance(em_routes, list):
            for i, entry in enumerate(em_routes):
                if not isinstance(entry, dict):
                    continue
                p = entry.get("geometry")
                if not isinstance(p, str) or not p:
                    continue
                full = _full(p)
                if not os.path.isfile(full):
                    report.err(f"event_mode.routes[{i}].geometry",
                               f"file not found: {p} (resolved to {full})")


def _collect_osm_relation_ids(config):
    """Return a set of stringified OSM relation IDs referenced anywhere
    in the config's relation-id lists. Used by validators that need to
    detect string-id-vs-int-id collisions.
    """
    osm_ids = set()
    for key in ("relations", "clipped_relations", "winter_relations",
                "summer_relations", "emergency_access_relations"):
        lst = config.get(key)
        if isinstance(lst, list):
            for rid in lst:
                if isinstance(rid, int) and not isinstance(rid, bool):
                    osm_ids.add(str(rid))
    return osm_ids


def _validate_custom_route_entry(report, where, entry, seen_ids, osm_ids):
    """Validate a single custom-route-shaped dict. Used by both
    `_validate_custom_routes` (for top-level `custom_routes:` entries)
    and `_validate_event_mode` (for inline `event_mode.routes:`
    entries — they share the schema).

    Updates `seen_ids` in place so the caller can carry duplicate-id
    detection across multiple lists.
    """
    if not isinstance(entry, dict):
        report.err(where, f"expected dict, got {type(entry).__name__}")
        return

    # Required: id
    cid = entry.get("id")
    if not isinstance(cid, str) or not cid:
        report.err(f"{where}.id", "required: non-empty string")
    else:
        if cid in seen_ids:
            report.err(f"{where}.id",
                       f"duplicate id {cid!r} "
                       f"(already used by an earlier custom-route entry)")
        else:
            seen_ids.add(cid)
        # Collision with OSM relation ids — compare stringified.
        if cid in osm_ids:
            report.err(f"{where}.id",
                       f"id {cid!r} collides with an OSM relation id "
                       f"used elsewhere in this config")
        # Reject ids that parse as ints (would collide with any OSM
        # relation with that numeric id).
        if cid.lstrip("-").isdigit():
            report.err(f"{where}.id",
                       f"id {cid!r} is purely numeric; custom-route ids "
                       f"must be non-numeric to avoid colliding with OSM "
                       f"relation ids")

    # Required: name
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        report.err(f"{where}.name", "required: non-empty string")

    # Required: color
    color = entry.get("color")
    if color is None:
        report.err(f"{where}.color", "required: hex or CSS named color")
    elif not _is_color(color):
        report.err(f"{where}.color",
                   f"not a valid color: {color!r}")

    # Required: geometry (path existence checked in _validate_paths)
    geom = entry.get("geometry")
    if not isinstance(geom, str) or not geom:
        report.err(f"{where}.geometry",
                   "required: path to a GeoJSON file "
                   "(LineString or MultiLineString)")

    # Bucket flags. Default: if all three omitted, summer=true.
    flags_present = any(k in entry for k in ("summer", "winter", "emergency"))
    for flag_key in ("summer", "winter", "emergency"):
        if flag_key in entry and not isinstance(entry[flag_key], bool):
            report.err(f"{where}.{flag_key}",
                       f"must be bool, got {type(entry[flag_key]).__name__}")

    if flags_present:
        resolved = [entry.get(k, False) is True for k in
                    ("summer", "winter", "emergency")]
        if not any(resolved):
            report.err(f"{where}",
                       "at least one of summer/winter/emergency must be "
                       "true (an all-false custom route would never render)")

    # Optional bool
    if "dashed" in entry and not isinstance(entry["dashed"], bool):
        report.err(f"{where}.dashed",
                   f"must be bool, got {type(entry['dashed']).__name__}")

    # Optional strings
    for sk in ("description", "trail_name_field"):
        if sk in entry and not isinstance(entry[sk], str):
            report.err(f"{where}.{sk}",
                       f"must be string, got {type(entry[sk]).__name__}")

    # Optional `oneway`: drives the existing direction-arrow renderer.
    # Accepts the same vocabulary as the OSM `oneway=` tag.
    if "oneway" in entry:
        ow = entry["oneway"]
        if ow not in ("yes", "-1", "reversible", ""):
            report.err(f"{where}.oneway",
                       f"must be one of 'yes', '-1', 'reversible', "
                       f"or '' (empty for no arrows), got {ow!r}")

    # Reject unknown keys in the custom route entry (catch typos).
    allowed = {"id", "name", "color", "geometry", "summer", "winter",
               "emergency", "dashed", "description", "trail_name_field",
               "oneway"}
    for k in entry.keys():
        if k not in allowed:
            suggestions = difflib.get_close_matches(k, allowed, n=2)
            hint = (f" — did you mean {' or '.join(repr(s) for s in suggestions)}?"
                    if suggestions else "")
            report.err(f"{where}.{k}",
                       f"unknown key in custom route entry{hint}")


def _validate_custom_routes(report, config):
    """Validate custom_routes entries.

    Each entry is a dict with:
      - required: id (string), name (string), color (valid color),
                  geometry (string path)
      - optional: summer, winter, emergency (bool; default
                  summer=true if all three omitted), dashed (bool),
                  description (string), trail_name_field (string)

    Cross-checks:
      - ids are unique across custom_routes
      - ids don't collide (stringified) with any OSM relation id in
        relations / clipped_relations / winter_relations /
        summer_relations / emergency_access_relations
      - at least one of summer/winter/emergency resolves to true
    """
    cr = config.get("custom_routes")
    if cr is None:
        return
    if not isinstance(cr, list):
        # already reported as a type error by _validate_types
        return

    osm_ids = _collect_osm_relation_ids(config)

    seen_ids = set()
    for i, entry in enumerate(cr):
        _validate_custom_route_entry(
            report, f"custom_routes[{i}]", entry, seen_ids, osm_ids)


DEFAULT_VISIBLE_LAYERS = {
    "parking",
    "trailheads",
    "features",
    "trail_markers",
    "toilets",
    "drinking_water",
    "difficulty",
    "emergency",
    "direction_arrows",
}


_ACCENT_COLOR_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_accent_color(report, config):
    """Validate the optional `accent_color` key.

    Three forms accepted:
      - omitted: framework default blue (#2980b9)
      - 6-digit hex string: explicit accent (e.g. "#FF5733")
      - the literal string "auto": derive from the logo at build time

    Anything else errors with a clear message.
    """
    val = config.get("accent_color")
    if val is None:
        return
    if not isinstance(val, str):
        report.err("accent_color",
                   f"expected string, got {type(val).__name__}")
        return
    if val == "auto":
        return
    if not _ACCENT_COLOR_HEX_RE.match(val):
        report.err("accent_color",
                   f"must be a 6-digit hex (e.g. '#FF5733') or 'auto', "
                   f"got {val!r}")


def _validate_default_visible(report, config):
    """Validate the optional `default_visible` key.

    Three forms accepted:
      - omitted: every layer toggle defaults to OFF on first visit
      - the literal string "all": every supported layer defaults to ON
      - list of layer names: those layers default to ON; everything
        else defaults to OFF

    Layer names are validated against DEFAULT_VISIBLE_LAYERS with a
    fuzzy suggestion on typos so a misspelled "parkings" doesn't
    silently produce a map with no parking visible.
    """
    val = config.get("default_visible")
    if val is None:
        return
    if isinstance(val, str):
        if val != "all":
            report.err("default_visible",
                       f"string form must be 'all', got {val!r}")
        return
    if not isinstance(val, list):
        report.err("default_visible",
                   f"expected list or 'all', got {type(val).__name__}")
        return
    seen = set()
    for i, item in enumerate(val):
        if not isinstance(item, str):
            report.err(f"default_visible[{i}]",
                       f"expected string, got {type(item).__name__}")
            continue
        if item not in DEFAULT_VISIBLE_LAYERS:
            suggestions = difflib.get_close_matches(
                item, DEFAULT_VISIBLE_LAYERS, n=2)
            hint = (f" (did you mean: {', '.join(suggestions)}?)"
                    if suggestions else "")
            report.err(f"default_visible[{i}]",
                       f"unknown layer name {item!r}{hint}")
            continue
        if item in seen:
            report.warn(f"default_visible[{i}]",
                        f"{item!r} listed more than once")
        seen.add(item)


def _validate_welcome(report, config):
    """Validate the optional `welcome` key.

    Three forms are accepted:
      - omitted: framework default welcome modal renders
      - false: welcome modal is suppressed entirely
      - dict with optional title/body/show_controls_hint
    """
    welcome = config.get("welcome")
    if welcome is None:
        return
    if isinstance(welcome, bool):
        # Only `false` is meaningful (disable). `true` is harmless
        # but redundant — the welcome already renders by default —
        # so accept silently.
        return
    if not isinstance(welcome, dict):
        report.err("welcome",
                   f"expected dict or false, got {type(welcome).__name__}")
        return
    if "title" in welcome and not isinstance(welcome["title"], str):
        report.err("welcome.title",
                   f"must be a string, got {type(welcome['title']).__name__}")
    if "body" in welcome and not isinstance(welcome["body"], str):
        report.err("welcome.body",
                   f"must be a string, got {type(welcome['body']).__name__}")
    if "show_controls_hint" in welcome and not isinstance(
            welcome["show_controls_hint"], bool):
        report.err("welcome.show_controls_hint",
                   f"must be a boolean, got "
                   f"{type(welcome['show_controls_hint']).__name__}")
    # Catch typos in welcome's sub-keys.
    allowed = {"title", "body", "show_controls_hint"}
    for k in welcome:
        if k not in allowed:
            suggestions = difflib.get_close_matches(k, allowed, n=2)
            hint = (f" (did you mean: {', '.join(suggestions)}?)"
                    if suggestions else "")
            report.err(f"welcome.{k}", f"unknown key in welcome{hint}")


def _validate_about(report, config):
    about = config.get("about")
    if about is None:
        return
    if not isinstance(about, dict):
        report.err("about", f"expected dict, got {type(about).__name__}")
        return
    if "description" in about and not isinstance(about["description"], str):
        report.err("about.description",
                   f"must be a string, got {type(about['description']).__name__}")
    # Legacy-key migration error. The previous schema split the
    # "more info" links into two arrays (more_information,
    # extra_links) rendered as separate sections; the new schema
    # consolidates them into one `links:` array rendered as a single
    # "More info" section. Hard-cut rather than aliased — the framework
    # has one curator and a small, known config set, so cleaner end
    # state beats deprecation-period cruft.
    for legacy in ("more_information", "extra_links"):
        if legacy in about:
            report.err(f"about.{legacy}",
                       f"`{legacy}` was removed; rename it to `links:` "
                       f"(append `extra_links` items into the same list "
                       f"if both keys were used).")
    if "links" in about:
        v = about["links"]
        if not isinstance(v, list):
            report.err("about.links",
                       f"must be a list, got {type(v).__name__}")
        else:
            for i, link in enumerate(v):
                if not isinstance(link, dict) or "label" not in link or "url" not in link:
                    report.err(f"about.links[{i}]",
                               "each entry must be {label, url}")
    if "author" in about:
        a = about["author"]
        if not isinstance(a, dict) or "name" not in a:
            report.err("about.author",
                       "must be {name, [url]}")


_BACKGROUND_STYLE_ALLOWED = {"color", "pattern", "cap"}
_BACKGROUND_STYLE_VALID_CAPS = {"butt", "round", "square"}


def _validate_event_mode(report, config):
    """Validate the optional `event_mode` block.

    Schema:
      event_mode:
        routes:                 # optional list of inline featured routes
          - id: ...              (same shape as a custom_routes entry)
            name: ...
            color: ...
            geometry: ...
            ...
        featured: [...]          # optional list of references; each
                                 # entry is a string (matches a top-
                                 # level custom_routes id) or an int
                                 # (matches an OSM relation id present
                                 # somewhere in this config).
        background_style:        # optional; default is grey dashed.
          color: "#888888"
          pattern: [2, 2]
          cap: round

    At least one of `routes` or `featured` must be non-empty.
    """
    em = config.get("event_mode")
    if em is None:
        return
    if not isinstance(em, dict):
        report.err("event_mode",
                   f"expected dict, got {type(em).__name__}")
        return

    # Reject unknown sub-keys.
    allowed = {"routes", "featured", "background_style",
               "direction_arrows", "pois", "poi_color"}
    for k in em:
        if k not in allowed:
            suggestions = difflib.get_close_matches(k, allowed, n=2)
            hint = (f" (did you mean: {', '.join(suggestions)}?)"
                    if suggestions else "")
            report.err(f"event_mode.{k}", f"unknown key in event_mode{hint}")

    routes = em.get("routes")
    featured = em.get("featured")

    if routes is not None and not isinstance(routes, list):
        report.err("event_mode.routes",
                   f"expected list, got {type(routes).__name__}")
        routes = None
    if featured is not None and not isinstance(featured, list):
        report.err("event_mode.featured",
                   f"expected list, got {type(featured).__name__}")
        featured = None

    routes_nonempty = isinstance(routes, list) and len(routes) > 0
    featured_nonempty = isinstance(featured, list) and len(featured) > 0
    if not (routes_nonempty or featured_nonempty):
        report.err("event_mode",
                   "at least one of `routes` or `featured` must be "
                   "non-empty (event mode needs something to feature)")

    # Validate inline routes via the shared custom-route entry checker.
    # Carry the seen_ids set across BOTH event_mode.routes and the
    # top-level custom_routes so the duplicate-id check spans both.
    osm_ids = _collect_osm_relation_ids(config)
    seen_ids = set()
    # Pre-load top-level custom_routes IDs so an inline event_mode
    # route can't shadow one declared at top level.
    cr = config.get("custom_routes")
    if isinstance(cr, list):
        for entry in cr:
            if isinstance(entry, dict):
                cid = entry.get("id")
                if isinstance(cid, str) and cid:
                    seen_ids.add(cid)

    if isinstance(routes, list):
        for i, entry in enumerate(routes):
            _validate_custom_route_entry(
                report, f"event_mode.routes[{i}]", entry, seen_ids, osm_ids)

    # Validate `featured` entries: each must be a string (matching a
    # top-level custom_routes id) OR an int (matching an OSM relation
    # id present somewhere in this config).
    if isinstance(featured, list):
        # Build the set of valid string IDs (top-level custom_routes
        # only — inline event_mode.routes are featured by definition,
        # so referencing one in `featured` would be redundant).
        valid_string_ids = set()
        if isinstance(cr, list):
            for entry in cr:
                if isinstance(entry, dict):
                    cid = entry.get("id")
                    if isinstance(cid, str) and cid:
                        valid_string_ids.add(cid)
        # Build the set of valid int IDs from every relation list.
        valid_int_ids = set()
        for key in ("relations", "clipped_relations", "winter_relations",
                    "summer_relations", "emergency_access_relations"):
            lst = config.get(key)
            if isinstance(lst, list):
                for rid in lst:
                    if isinstance(rid, int) and not isinstance(rid, bool):
                        valid_int_ids.add(rid)

        for i, ref in enumerate(featured):
            where = f"event_mode.featured[{i}]"
            if isinstance(ref, bool):
                report.err(where,
                           f"expected string (custom-route id) or int "
                           f"(OSM relation id), got bool")
                continue
            if isinstance(ref, int):
                if ref not in valid_int_ids:
                    report.err(where,
                               f"OSM relation id {ref} is not present in "
                               f"`relations`, `clipped_relations`, or any "
                               f"of the *_relations bucket lists")
                continue
            if isinstance(ref, str):
                if ref not in valid_string_ids:
                    report.err(where,
                               f"string id {ref!r} does not match any "
                               f"top-level custom_routes entry. (Inline "
                               f"event_mode.routes are featured by "
                               f"definition; do not reference them here.)")
                continue
            report.err(where,
                       f"expected string (custom-route id) or int "
                       f"(OSM relation id), got {type(ref).__name__}")

    # Validate background_style.
    bg = em.get("background_style")
    if bg is not None:
        if not isinstance(bg, dict):
            report.err("event_mode.background_style",
                       f"expected dict, got {type(bg).__name__}")
        else:
            for k in bg:
                if k not in _BACKGROUND_STYLE_ALLOWED:
                    suggestions = difflib.get_close_matches(
                        k, _BACKGROUND_STYLE_ALLOWED, n=2)
                    hint = (f" (did you mean: {', '.join(suggestions)}?)"
                            if suggestions else "")
                    report.err(f"event_mode.background_style.{k}",
                               f"unknown key{hint}")
            if "color" in bg and not _is_color(bg["color"]):
                report.err("event_mode.background_style.color",
                           f"not a valid color: {bg['color']!r}")
            if "pattern" in bg:
                p = bg["pattern"]
                if not isinstance(p, list) or not p or not all(
                        isinstance(n, (int, float)) and not isinstance(n, bool)
                        for n in p):
                    report.err("event_mode.background_style.pattern",
                               f"must be a non-empty list of numbers, "
                               f"got {p!r}")
            if "cap" in bg and bg["cap"] not in _BACKGROUND_STYLE_VALID_CAPS:
                report.err("event_mode.background_style.cap",
                           f"must be one of "
                           f"{sorted(_BACKGROUND_STYLE_VALID_CAPS)}, "
                           f"got {bg['cap']!r}")

    # direction_arrows: optional bool. When true, inline event_mode.routes
    # render arrows along their geometry AND the rider toggle is hidden
    # (always-on for event-mode arrows).
    da = em.get("direction_arrows")
    if da is not None and not isinstance(da, bool):
        report.err("event_mode.direction_arrows",
                   f"expected bool, got {type(da).__name__}")

    # pois: optional list of {name, coordinates, description?} entries.
    # Always-on at runtime (no rider toggle). Used for event-specific
    # locations: start / finish, aid stations, support areas, etc.
    pois = em.get("pois")
    if pois is not None:
        if not isinstance(pois, list):
            report.err("event_mode.pois",
                       f"expected list, got {type(pois).__name__}")
        else:
            for i, entry in enumerate(pois):
                where = f"event_mode.pois[{i}]"
                if not isinstance(entry, dict):
                    report.err(where,
                               f"expected dict, got {type(entry).__name__}")
                    continue
                # name required
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    report.err(f"{where}.name",
                               "required: non-empty string")
                # coordinates required: [lon, lat]
                coords = entry.get("coordinates")
                if not (isinstance(coords, list) and len(coords) == 2
                        and all(isinstance(x, (int, float))
                                and not isinstance(x, bool) for x in coords)):
                    report.err(f"{where}.coordinates",
                               f"required: [lon, lat] numbers, got {coords!r}")
                else:
                    lon, lat = coords
                    if not -180 <= lon <= 180:
                        report.err(f"{where}.coordinates",
                                   f"longitude must be in [-180,180]: {lon}")
                    if not -90 <= lat <= 90:
                        report.err(f"{where}.coordinates",
                                   f"latitude must be in [-90,90]: {lat}")
                # description optional string
                if "description" in entry and not isinstance(
                        entry["description"], str):
                    report.err(f"{where}.description",
                               f"must be string, got "
                               f"{type(entry['description']).__name__}")
                # Reject unknown keys in entry.
                allowed_pkeys = {"name", "coordinates", "description"}
                for k in entry.keys():
                    if k not in allowed_pkeys:
                        suggestions = difflib.get_close_matches(
                            k, allowed_pkeys, n=2)
                        hint = (f" (did you mean: {', '.join(suggestions)}?)"
                                if suggestions else "")
                        report.err(f"{where}.{k}",
                                   f"unknown key in event POI{hint}")

    # poi_color: optional CSS color (hex or named). Default: deep red.
    pc = em.get("poi_color")
    if pc is not None and not _is_color(pc):
        report.err("event_mode.poi_color",
                   f"not a valid color: {pc!r}")


def _validate_relations(report, config):
    """`relations` must be a non-empty list of OSM relation IDs.

    The required-key check upstream already ensures the key is present;
    this catches the structural-but-useless `relations: []` form. Type
    and per-element int checks live in `_validate_types` and
    `_validate_relation_id_dicts` respectively, so this validator only
    asserts non-emptiness.
    """
    rels = config.get("relations")
    if rels is None:
        # Already reported by _validate_required.
        return
    if isinstance(rels, list) and not rels:
        report.err("relations",
                   "must be a non-empty list of OSM relation IDs "
                   "(at least one entry required)")


def _validate_legacy_keys(report, config):
    """Reject the pre-collapse `root_relation_id` / `extra_relations` keys
    with a pointed migration message.

    Both keys were removed when the unified `relations:` list shipped.
    A YAML still carrying them would otherwise produce a generic
    "unknown top-level key" warning that doesn't tell the curator what
    the fix is. This validator runs before `_validate_unknown_keys`
    short-circuits the message, so the migration prompt wins."""
    if "root_relation_id" in config:
        report.err("root_relation_id",
                   "renamed to `relations:` (now a list). Replace "
                   "`root_relation_id: <id>` with `relations: [<id>]`. "
                   "Fold any `extra_relations:` entries into the same list.")
    if "extra_relations" in config:
        report.err("extra_relations",
                   "merged into `relations:`. Move every entry into "
                   "the `relations:` list (alongside the former "
                   "`root_relation_id` value).")


def _validate_slug(report, config):
    slug = config.get("slug")
    if isinstance(slug, str) and not re.match(r"^[a-z0-9_-]+$", slug):
        report.err("slug",
                   f"must match [a-z0-9_-]+ (lowercase, no spaces): got {slug!r}")


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------

def validate_config(config, *, config_path=None, project_root=None):
    """Validate a loaded config dict.

    Returns (errors, warnings) — both lists of pre-formatted strings.
    Caller decides whether warnings should print or be silent.

    ``config_path`` (or ``config["_config_dir"]`` set by build.py's
    ``load_config``) is used as the base for resolving user-supplied
    asset paths (logo, icon, osm_file, custom_routes[].geometry). The
    ``project_root`` parameter is retained for backwards compat but no
    longer used for asset-path resolution.
    """
    if not isinstance(config, dict):
        return ([f"  [error] root: YAML did not parse to a mapping/dict "
                 f"(got {type(config).__name__})"], [])

    # Derive the config's directory from (in order): an explicit
    # config_path arg, the _config_dir stashed by build.py's load_config,
    # or None (which disables the path-exists check since we can't tell
    # where to look).
    if config_path:
        config_dir = os.path.dirname(os.path.abspath(config_path))
    else:
        config_dir = config.get("_config_dir")

    report = _Report()
    # Run the legacy-keys check FIRST so the migration message (e.g.
    # "root_relation_id: ... renamed to `relations:`") wins over the
    # generic "unknown top-level key" warning that follows.
    _validate_legacy_keys(report, config)
    _validate_unknown_keys(report, config)
    _validate_required(report, config)
    _validate_types(report, config)
    _validate_relations(report, config)
    _validate_enums(report, config)
    _validate_geometry(report, config)
    _validate_colors(report, config)
    _validate_relation_id_dicts(report, config)
    _validate_weekdays(report, config)
    _validate_dashed_relations(report, config)
    _validate_point_lists(report, config)
    _validate_paths(report, config, config_dir)
    _validate_custom_routes(report, config)
    _validate_event_mode(report, config)
    _validate_about(report, config)
    _validate_welcome(report, config)
    _validate_default_visible(report, config)
    _validate_accent_color(report, config)
    _validate_slug(report, config)
    return report.errors, report.warnings


def validate_config_file(path):
    """Load + validate a single YAML file. Returns (errors, warnings)."""
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return ([f"  [error] root: YAML parse error: {e}"], [])
    except OSError as e:
        return ([f"  [error] root: cannot open {path}: {e}"], [])
    return validate_config(config, config_path=path)


def assert_spec_coverage():
    """Drift check: every key the validator KNOWS about must be accounted
    for either in CONFIG_SPEC (flows automatically into the JS runtime),
    BUILD_ONLY_KEYS (intentionally consumed only at build time), or
    HANDLED_SPECIALLY (built into the runtime via custom logic).

    Catches the common failure where a new config key is added with a
    validator entry but never reaches the frontend — silent breakage that
    used to ship to production. Run via `validate_config.py --check-spec`
    and any time CONFIG_SPEC or KNOWN_KEYS changes.
    """
    # Lazy import: build.py imports validate_config at top, so a top-level
    # `from build import CONFIG_SPEC` would be a circular import. The
    # function-level import only runs when the lint is invoked, by which
    # time both modules are fully loaded.
    from build import CONFIG_SPEC

    spec_keys = {yaml_key for yaml_key, _, _ in CONFIG_SPEC}
    accounted = spec_keys | BUILD_ONLY_KEYS | HANDLED_SPECIALLY
    missing = set(KNOWN_KEYS) - accounted
    extra_spec = spec_keys - set(KNOWN_KEYS)

    problems = []
    if missing:
        problems.append(
            f"KNOWN_KEYS not covered: {sorted(missing)}\n"
            f"  Add to CONFIG_SPEC if the runtime needs it,\n"
            f"  to BUILD_ONLY_KEYS if it's consumed only at build time,\n"
            f"  or to HANDLED_SPECIALLY if inject_config_into_template "
            f"transforms it before emitting."
        )
    if extra_spec:
        problems.append(
            f"CONFIG_SPEC has keys not in KNOWN_KEYS: {sorted(extra_spec)}\n"
            f"  Add them to KNOWN_KEYS so the validator can type-check them."
        )

    if problems:
        for p in problems:
            print(f"  [error] spec-coverage: {p}", file=sys.stderr)
        return False
    print(f"  spec coverage OK ({len(spec_keys)} in CONFIG_SPEC, "
          f"{len(BUILD_ONLY_KEYS)} build-only, "
          f"{len(HANDLED_SPECIALLY)} handled specially)")
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: validate_config.py [--check-spec] "
              "<config.yaml> [<config.yaml> ...]",
              file=sys.stderr)
        sys.exit(2)

    if args[0] == "--check-spec":
        sys.exit(0 if assert_spec_coverage() else 1)

    overall_errors = 0
    for path in args:
        errors, warnings = validate_config_file(path)
        status = "FAIL" if errors else "OK"
        print(f"{status}: {path}")
        for line in errors:
            print(line)
        for line in warnings:
            print(line)
        overall_errors += len(errors)

    sys.exit(1 if overall_errors else 0)


if __name__ == "__main__":
    main()
