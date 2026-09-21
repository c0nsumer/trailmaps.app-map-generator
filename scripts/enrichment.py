"""Trail-GeoJSON enrichment.

Flags features with season / difficulty / route-bucket metadata, appends
custom routes, and attaches per-route stats. The features stay canonical:
one per route per run of way, shared paths sharing vertices, each in its
route's own travel direction. The browser lays out the parallel lanes
from exactly that (maplibre-gl-lanes), so nothing here reorders,
realigns or expands geometry.
"""

import json
import os
import sys

import console


def _enrich_trails_geojson(config, trails_geojson, project_root, cache_dir=None):
    """Enrich trails.geojson in-place with bucket flags + custom routes.

    Runs after trails have been fetched (or loaded from cache). It:

    - Strips any previously-appended custom-route features and metadata
      entries so re-runs are idempotent.
    - Computes three non-exclusive bucket booleans (``summer``, ``winter``,
      ``emergency``) for every OSM-sourced route in metadata.routes. Rules:
        winter    = (seasonal=winter in OSM)  OR  id in winter_relations
        emergency = id in emergency_access_relations
        summer    = id in summer_relations
                    OR  (not winter AND not emergency)
      "Summer is the default" - a plain OSM route with no seasonal tag and
      no inclusion in any of the three lists is summer-only.
      ``summer_relations`` is the opt-back-in list for year-round routes
      (the RAMBA SBR pattern: ridden in summer AND groomed in winter).
    - Applies ``relation_names`` display-name overrides to both
      metadata.routes and the per-feature ``route_name`` property, and
      warns about relation_names / relation_colors keys that match no
      fetched route (typo guard).
    - Loads each ``custom_routes`` entry's GeoJSON file, validates geometry
      type (LineString / MultiLineString only), normalizes features into
      the shape fetch_trails.py emits (one LineString per feature), and
      appends them to ``features``. Custom-route metadata entries are added
      to metadata.routes with bucket flags declared inline in the config.

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
    # Config lists are ints (OSM relation ids); metadata.routes is keyed
    # by string. Stringify config values for lookup.
    #
    # Super-relation expansion: if the curator listed a super-relation
    # ID in any of these config keys, fetch_trails.py expanded it into
    # the child route IDs and persisted the parent→children map to
    # trails.geojson metadata. We replay that expansion here so each
    # child route inherits the parent's bucket assignment (winter /
    # summer / emergency) without the curator having to enumerate the
    # children individually in YAML.
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
        # Keep the OSM-source `seasonal` field as-is. It's the upstream
        # input that `is_winter` reads above, so deleting it would make
        # this function non-idempotent: a rebuild that reuses an existing
        # trails.geojson (no --refresh-trails) would see `seasonal`
        # already gone from the previous enrichment pass and miscompute
        # `is_winter` for every OSM-tagged winter relation.

        if prior != (is_summer, is_winter, is_emergency, False):
            changed = True

    # ----- Per-route display-name overrides (relation_names) -----
    # Applied here, post-cache, so a YAML edit takes effect on a plain
    # rebuild: the expanded trails.geojson is regenerated from the
    # pristine trails.src.geojson base (which keeps the OSM names) on
    # every build, so adding, changing, or REMOVING an override never
    # needs a --refresh-trails refetch. Runs before the subway-style pass so
    # stub features inherit the overridden route_name. Custom routes
    # are unaffected (string IDs; they name themselves in YAML).
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
    for key in ("relation_names", "relation_colors"):
        for rid in config.get(key) or {}:
            rid_str = str(rid)
            if rid_str in routes:
                continue
            if rid_str in super_expansions:
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
    for feat in trails_geojson["features"]:
        props = feat.setdefault("properties", {})
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
        cgeom_rel = entry["geometry"]
        cgeom_abs = cgeom_rel if os.path.isabs(cgeom_rel) else os.path.join(project_root, cgeom_rel)

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

        appended = 0
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

            for line in linestrings:
                if not isinstance(line, list) or len(line) < 2:
                    continue
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
                        "route_ref": "",
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
                        "oneway": entry.get("oneway", ""),
                        "segment_index": appended,
                        "way_ids": [],
                        "isCustom": True,
                    },
                }
                trails_geojson["features"].append(new_feat)
                appended += 1

        # Metadata.routes entry - shape mirrors OSM-sourced routes plus
        # the three bucket flags and isCustom.
        info = {
            "name": cname,
            "colour": ccolor,
            "ref": "",
            "seasonal": "winter" if (c_winter and not c_summer) else "",
            "summer": c_summer,
            "winter": c_winter,
            "emergency": c_emergency,
            "isCustom": True,
        }
        if c_dashed:
            # Pattern: explicit list-form on the entry takes precedence;
            # otherwise [4, 4] (framework default for `dashed: true`).
            # Users who need a specific pattern can either use the
            # list form directly OR add a matching dashed_relations
            # entry keyed by the custom id (the runtime filter treats
            # the route id opaquely).
            info["dashed"] = c_dashed_pattern
            if c_dash_cap:
                info["dashCap"] = c_dash_cap
        routes[cid] = info
        changed = True

    # ----- Per-route distance / elevation stats -----
    # After the custom routes are appended, so they are measured too.
    from compute_route_stats import compute_and_attach

    if compute_and_attach(trails_geojson, config, cache_dir):
        changed = True

    return changed
