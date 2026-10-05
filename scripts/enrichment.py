"""Trail-GeoJSON enrichment.

Flags features with season / difficulty / route-bucket metadata, appends
custom routes, and attaches per-route stats. The features stay canonical:
one per route per run of way, shared paths sharing vertices, each in its
route's own travel direction. The browser lays out the parallel lanes
from exactly that (maplibre-gl-lanes), so nothing here reorders,
realigns or expands geometry.
"""

import json
import sys

import console


def resolve_color_modes(config, route_ids, super_expansions=None):
    """Return {route_id_str: "route" | "difficulty"} for every id given.

    `color_by` is the default mode; `color_by_route` and
    `color_by_difficulty` are the exceptions. Both lists accept leaf ids,
    super-relation ids and custom-route ids. A super-relation fans out
    through the same `super_relation_expansions` table the season buckets
    use, and an id listed directly beats a fan-out from a super-relation
    so one child can differ from the rest of its system. An id in both
    lists is a validator error; route wins here so a build never crashes
    on it.
    """
    super_expansions = super_expansions or {}
    default = config.get("color_by", "route")

    def _split(key):
        leaves, fanned = set(), set()
        for x in config.get(key) or []:
            sx = str(x)
            if sx in super_expansions:
                fanned.update(str(c) for c in super_expansions[sx])
            else:
                leaves.add(sx)
        return leaves, fanned

    route_leaf, route_fan = _split("color_by_route")
    diff_leaf, diff_fan = _split("color_by_difficulty")

    modes = {}
    for rid in route_ids:
        r = str(rid)
        if r in route_leaf:
            modes[r] = "route"
        elif r in diff_leaf:
            modes[r] = "difficulty"
        elif r in route_fan:
            modes[r] = "route"
        elif r in diff_fan:
            modes[r] = "difficulty"
        else:
            modes[r] = default
    return modes


def _enrich_trails_geojson(config, trails_geojson, cache_dir=None):
    """Enrich trails.geojson in-place with bucket flags + custom routes.

    Runs after trails have been fetched or loaded from cache, and is
    idempotent: previously appended custom-route features and metadata
    entries are stripped first. The bucket rules are in the comment at
    the flag loop below. Also applies ``relation_names`` overrides,
    warns about override keys that match no route, and appends each
    ``custom_routes`` GeoJSON file in the shape fetch_trails.py emits.

    Returns True if anything was changed (caller writes back to disk).
    """
    features = trails_geojson.get("features") or []
    # Strip previously-appended custom features for idempotent re-runs.
    cleaned = [f for f in features if not f.get("properties", {}).get("isCustom")]
    stripped_count = len(features) - len(cleaned)
    trails_geojson["features"] = cleaned

    metadata = trails_geojson.setdefault("metadata", {})
    routes = metadata.setdefault("routes", {})
    for rid in list(routes.keys()):
        if routes[rid].get("isCustom"):
            del routes[rid]

    changed = stripped_count > 0

    # ----- Bucket flags on OSM routes -----
    # Three non-exclusive booleans per route:
    #   winter    = (seasonal=winter in OSM) OR id in winter_relations
    #   emergency = id in emergency_access_relations
    #   summer    = id in summer_relations OR (not winter AND not emergency)
    # Summer is the default. ``summer_relations`` is the opt-back-in list
    # for year-round routes (ridden in summer AND groomed in winter).
    #
    # Config lists are ints (OSM relation ids); metadata.routes is keyed
    # by string. A super-relation listed in any of these keys was expanded
    # by fetch_trails.py and its parent→children map persisted to
    # metadata; replaying it here lets each child inherit the bucket.
    super_expansions = trails_geojson.get("metadata", {}).get("super_relation_expansions", {}) or {}

    def _expand(config_ids):
        out = set()
        for x in config_ids or []:
            sx = str(x)
            if sx in super_expansions:
                out.update(super_expansions[sx])
            else:
                out.add(sx)
        return out

    summer_ids = _expand(config.get("summer_relations"))
    winter_ids = _expand(config.get("winter_relations"))
    emergency_ids = _expand(config.get("emergency_access_relations"))

    for rid_str, info in routes.items():
        is_winter = (info.get("seasonal") == "winter") or (rid_str in winter_ids)
        is_emergency = rid_str in emergency_ids
        is_summer = (rid_str in summer_ids) or (not is_winter and not is_emergency)

        # Compare before overwriting so we can return a tight "changed?" bit.
        prior = (
            info.get("summer"),
            info.get("winter"),
            info.get("emergency"),
            info.get("isCustom", False),
        )
        info["summer"] = is_summer
        info["winter"] = is_winter
        info["emergency"] = is_emergency
        info["isCustom"] = False
        # Keep `seasonal`: `is_winter` reads it, so deleting it would break
        # idempotence on a rebuild that reuses an existing trails.geojson.

        if prior != (is_summer, is_winter, is_emergency, False):
            changed = True

    # ----- Per-route display-name overrides (relation_names) -----
    # Applied post-cache: trails.geojson is regenerated from the pristine
    # trails.src.geojson base on every build, so adding, changing or
    # REMOVING an override never needs a --refresh-trails refetch. Custom
    # routes are unaffected (string IDs; they name themselves in YAML).
    relation_names = {str(k): v for k, v in (config.get("relation_names") or {}).items()}
    if relation_names:
        for rid_str, new_name in relation_names.items():
            info = routes.get(rid_str)
            if info is not None and info.get("name") != new_name:
                info["name"] = new_name
                changed = True
        for feat in trails_geojson["features"]:
            props = feat.get("properties") or {}
            new_name = relation_names.get(str(props.get("route_id")))
            if new_name is not None and props.get("route_name") != new_name:
                props["route_name"] = new_name
                changed = True

    # ----- Typo guard on per-relation override keys -----
    # An override keyed by a relation that isn't on the map is silently
    # inert (relation_names above no-ops; relation_colors is looked up
    # per fetched route at injection time), so surface it here. Runs
    # before event mode synthesizes relation_colors entries in
    # template_inject, so only the curator's own keys are checked. A
    # super-relation parent is a special case: it was expanded into its
    # children at fetch time, so point the curator at those IDs.
    # direction_schedule.per_route is the one dict where a super-relation
    # key is valid: the injector fans a parent's schedule out to its
    # children, so only an id that is on no list at all is a typo there.
    per_route = (config.get("direction_schedule") or {}).get("per_route") or {}
    keyed_overrides = (
        ("relation_names", config.get("relation_names") or {}, False),
        ("relation_colors", config.get("relation_colors") or {}, False),
        ("dashed_relations", config.get("dashed_relations") or {}, False),
        ("direction_schedule.per_route", per_route, True),
    )
    for key, mapping, supers_ok in keyed_overrides:
        for rid in mapping:
            rid_str = str(rid)
            if rid_str in routes:
                continue
            if rid_str in super_expansions:
                if supers_ok:
                    continue
                children = ", ".join(super_expansions[rid_str])
                console.warn(
                    f"{key}[{rid}]: this is a super-relation; key the "
                    f"override by its child route ID(s) instead: {children}"
                )
            else:
                console.warn(f"{key}[{rid}]: no such route on this map (typo?)")

    # ----- Stringify route_id / shared_routes on every feature -----
    # The runtime treats route ids as opaque strings everywhere (so
    # OSM relation ids and custom string ids coexist in the same
    # filter expressions). fetch_trails.py emits OSM ids as ints;
    # custom routes already emit strings. Normalize both here so the
    # downstream JSON has a single consistent type.
    #
    # A base fetched by an older engine also carries `route_ref` and
    # `segment_index`; the runtime reads neither, so they do not ship.
    for feat in trails_geojson["features"]:
        props = feat.setdefault("properties", {})
        props.pop("route_ref", None)
        props.pop("segment_index", None)
        rid = props.get("route_id")
        if isinstance(rid, int):
            props["route_id"] = str(rid)
            changed = True
        sr = props.get("shared_routes")
        if isinstance(sr, list) and any(isinstance(x, int) for x in sr):
            props["shared_routes"] = [str(x) for x in sr]
            changed = True

    # ----- Append custom-route features and metadata -----
    custom_routes = config.get("custom_routes") or []
    for entry in custom_routes:
        cid = entry["id"]
        cname = entry["name"]
        ccolor = entry["color"]
        # build.load_config has already made the path absolute.
        cgeom_abs = entry["geometry"]

        # Bucket flags: if none of the three are set, default to summer-only
        # to match the OSM-default rule. If any is set explicitly, use the
        # given values (False for the unset ones).
        flags_given = any(k in entry for k in ("summer", "winter", "emergency"))
        if flags_given:
            c_summer = bool(entry.get("summer", False))
            c_winter = bool(entry.get("winter", False))
            c_emergency = bool(entry.get("emergency", False))
        else:
            c_summer, c_winter, c_emergency = True, False, False

        # `dashed` accepts three shapes for flexibility:
        #   False / absent: solid line (default).
        #   True:           default dashed pattern [4, 4].
        #   list of nums:   explicit dash pattern (e.g. [2, 2]).
        # The list form lets the event-mode pre-pass push a specific
        # background pattern into a non-featured custom route without
        # going through the relation-id-keyed dashed_relations override
        # (custom routes have string ids; that path is OSM-only).
        c_dashed_raw = entry.get("dashed", False)
        if isinstance(c_dashed_raw, list):
            c_dashed = True
            c_dashed_pattern = list(c_dashed_raw)
        else:
            c_dashed = bool(c_dashed_raw)
            c_dashed_pattern = [4, 4]  # framework default for `dashed: true`
        c_dash_cap = entry.get("dashCap")
        trail_name_field = entry.get("trail_name_field")

        # Load and validate geometry.
        try:
            with open(cgeom_abs, encoding="utf-8") as f:
                gj = json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(f"ERROR: custom_routes[{cid!r}].geometry: cannot read {cgeom_abs!r}: {e}")

        if gj.get("type") == "FeatureCollection":
            gj_features = gj.get("features") or []
        elif gj.get("type") == "Feature":
            gj_features = [gj]
        else:
            sys.exit(
                f"ERROR: custom_routes[{cid!r}].geometry: top-level type "
                f"must be Feature or FeatureCollection "
                f"(got {gj.get('type')!r})"
            )

        for i, feat in enumerate(gj_features):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            if gtype not in ("LineString", "MultiLineString"):
                sys.exit(
                    f"ERROR: custom_routes[{cid!r}].geometry feature {i}: "
                    f"geometry type must be LineString or MultiLineString "
                    f"(got {gtype!r})"
                )
            coords = geom.get("coordinates") or []
            if not coords:
                sys.exit(f"ERROR: custom_routes[{cid!r}].geometry feature {i}: empty coordinates")

            # Normalize MultiLineString into separate LineString features
            # (matches fetch_trails.py's per-segment shape).
            linestrings = coords if gtype == "MultiLineString" else [coords]

            src_props = feat.get("properties") or {}
            trail_name = ""
            if trail_name_field and trail_name_field in src_props:
                val = src_props[trail_name_field]
                if isinstance(val, str):
                    trail_name = val

            # oneway "-1" is OSM's "one-way against the drawn direction".
            # The runtime knows one direction, along the line, so the
            # line is reversed and the value becomes "yes", as
            # fetch_trails does for an OSM way.
            oneway = entry.get("oneway", "")
            against = oneway == "-1"
            if against:
                oneway = "yes"

            for line in linestrings:
                if not isinstance(line, list) or len(line) < 2:
                    continue
                if against:
                    line = line[::-1]
                new_feat = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": line,
                    },
                    "properties": {
                        "route_id": cid,  # string id, like the
                        # stringified OSM ids above.
                        "route_name": cname,
                        "route_colour": ccolor,
                        "trail_name": trail_name,
                        "shared_routes": [cid],
                        "imba_difficulty": "",
                        # Custom routes don't have OSM `oneway=` tags on
                        # individual segments (the GeoJSON has no per-
                        # segment OSM metadata). Curators opt in via
                        # the entry-level `oneway:` field - typically
                        # set automatically when event_mode.direction_arrows
                        # is true (see _apply_event_mode_to_custom_routes).
                        # Empty string means no arrows.
                        "oneway": oneway,
                        "way_ids": [],
                        "isCustom": True,
                    },
                }
                trails_geojson["features"].append(new_feat)

        # Metadata.routes entry - shape mirrors OSM-sourced routes plus
        # the three bucket flags and isCustom.
        info = {
            "name": cname,
            "colour": ccolor,
            "summer": c_summer,
            "winter": c_winter,
            "emergency": c_emergency,
            "isCustom": True,
        }
        if c_dashed:
            # An explicit list-form pattern on the entry wins; otherwise
            # [4, 4], the default for `dashed: true`.
            info["dashed"] = c_dashed_pattern
            if c_dash_cap:
                info["dashCap"] = c_dash_cap
        routes[cid] = info
        changed = True

    # ----- Per-route distance stats -----
    # After the custom routes are appended, so they are measured too.
    from compute_route_stats import compute_and_attach

    if compute_and_attach(trails_geojson, config):
        changed = True

    return changed
