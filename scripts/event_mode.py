"""Event-mode config transforms.

Applies "event mode" (feature a subset of routes, mute the rest) to the
config and trail geometry: background styling, inline/featured route sets,
and custom-route / relation / oneway rewrites. Extracted from build.py.
"""

import console

_EVENT_MODE_DEFAULT_BG = {
    "color": "gray",
    "pattern": [0, 2],
    "cap": "round",
}


def _event_mode_background_style(config):
    """Resolve the background_style for event mode (override + default)."""
    em = config.get("event_mode") or {}
    bg = dict(_EVENT_MODE_DEFAULT_BG)
    bg.update(em.get("background_style") or {})
    # Defensive: ensure pattern is a list.
    if not isinstance(bg.get("pattern"), list) or not bg["pattern"]:
        bg["pattern"] = list(_EVENT_MODE_DEFAULT_BG["pattern"])
    return bg


def _event_mode_inline_route_ids(config):
    """Set of stringified IDs declared inline under event_mode.routes."""
    em = config.get("event_mode") or {}
    routes = em.get("routes") or []
    out = set()
    for entry in routes:
        if isinstance(entry, dict):
            cid = entry.get("id")
            if isinstance(cid, str) and cid:
                out.add(cid)
    return out


def _event_mode_featured_set(config, super_expansions):
    """Resolve the complete set of featured route IDs (stringified).

    Combines:
      - Every event_mode.routes[i].id (inline; featured by definition).
      - Every entry in event_mode.featured (string ids pass through;
        int ids resolve to themselves OR fan out via super_expansions).
    """
    em = config.get("event_mode") or {}
    out = _event_mode_inline_route_ids(config)
    for ref in em.get("featured") or []:
        if isinstance(ref, bool):
            continue
        if isinstance(ref, str):
            out.add(ref)
        elif isinstance(ref, int):
            sref = str(ref)
            if sref in super_expansions:
                out.update(str(c) for c in super_expansions[sref])
            else:
                out.add(sref)
    return out


def _apply_event_mode_to_custom_routes(config):
    """Pre-enrichment event-mode pass.

    Folds event_mode.routes into config["custom_routes"] so they
    participate in the standard geometry bake-in inside
    _enrich_trails_geojson. Overrides non-featured custom routes'
    `color` and `dashed` fields so the muted background style flows
    through that bake-in into trails.geojson metadata.

    Also handles `event_mode.direction_arrows`: when true, every
    inline event route gets `oneway: "yes"` (so the bake-in
    propagates it to features as the existing arrow renderer
    expects), and `direction_arrows` is added to `forced_visible`
    so the rider toggle disappears + arrows always render.

    Mutates `config` in place (and returns it). No-op when event_mode
    is absent.
    """
    em = config.get("event_mode")
    if not em:
        return config

    inline_routes = list(em.get("routes") or [])
    inline_ids = _event_mode_inline_route_ids(config)

    # event_mode.direction_arrows: stamp `oneway: "yes"` on each inline
    # route entry (so the custom-route bake-in carries it onto every
    # emitted feature) and add `direction_arrows` to `forced_visible`
    # so the runtime renders arrows always (no rider toggle to
    # disable).
    if em.get("direction_arrows"):
        for entry in inline_routes:
            if isinstance(entry, dict) and not entry.get("oneway"):
                entry["oneway"] = "yes"
        # forced_visible: "all" already covers direction_arrows — and
        # list() on the STRING would explode it into
        # ['a','l','l','direction_arrows'], corrupting the config (the
        # injector's == "all" check then misses and every genuinely
        # forced layer silently un-forces).
        if config.get("forced_visible") != "all":
            existing_forced = list(config.get("forced_visible") or [])
            if "direction_arrows" not in existing_forced:
                existing_forced.append("direction_arrows")
                config["forced_visible"] = existing_forced

    # Fold inline event_mode.routes into top-level custom_routes.
    # Validator already checked id-uniqueness across both lists, so
    # we don't need to dedupe here.
    if inline_routes:
        existing = list(config.get("custom_routes") or [])
        config["custom_routes"] = existing + inline_routes

    # Featured set, less the OSM-int side (which we resolve later when
    # super_expansions is available). For the custom-route mutation
    # pass we only care about which custom-route string ids are
    # featured, which is: every inline route id PLUS any string entry
    # in event_mode.featured.
    featured_strings = set(inline_ids)
    for ref in em.get("featured") or []:
        if isinstance(ref, str):
            featured_strings.add(ref)

    bg = _event_mode_background_style(config)
    bg_color = bg["color"]
    bg_pattern = bg["pattern"]
    bg_cap = bg.get("cap", "round")

    for entry in config.get("custom_routes") or []:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        if not isinstance(cid, str):
            continue
        if cid in featured_strings:
            # Featured: leave declared color + dashed alone.
            continue
        # Non-featured custom route: overwrite to background style.
        # `dashed` accepts a list-form pattern (see _enrich_trails_geojson),
        # so we push the background pattern + cap directly onto the entry
        # and the bake-in picks them up.
        entry["color"] = bg_color
        entry["dashed"] = list(bg_pattern)
        entry["dashCap"] = bg_cap

    return config


def _apply_event_mode_to_feature_oneway(config, trails_geojson):
    """Restrict direction arrows to featured routes only.

    Without this pass, `event_mode.direction_arrows: true` would make
    every OSM-tagged oneway way render arrows alongside the featured
    route's arrows (because the runtime arrow emitter checks
    `way.oneway === "yes"` on every way regardless of route). That
    clutters an event map with OSM oneway clutter.

    Solution: strip the `oneway` property from any feature whose
    routes are ALL non-featured. A way shared between a featured
    route and a non-featured route keeps its `oneway` (the featured
    route still wants the arrows on its shared geometry).

    Mutates trails_geojson features in place. Returns True if any
    feature was changed (caller writes back to disk). No-op (returns
    False) when event_mode is absent or `direction_arrows: false`.
    """
    em = config.get("event_mode") or {}
    if not em.get("direction_arrows"):
        return False

    super_expansions = trails_geojson.get("metadata", {}).get("super_relation_expansions", {}) or {}
    featured = _event_mode_featured_set(config, super_expansions)

    stripped = 0
    for feat in trails_geojson.get("features") or []:
        props = feat.get("properties") or {}
        if not props.get("oneway"):
            continue
        # Collect every route ID this feature contributes to: its
        # primary route_id plus any shared_routes (a way borrowed
        # by multiple routes shows up in all of them).
        rids = set()
        rid = props.get("route_id")
        if rid is not None:
            rids.add(str(rid))
        for sr in props.get("shared_routes") or []:
            rids.add(str(sr))
        # If any of the feature's routes is featured, keep oneway.
        if rids & featured:
            continue
        # No featured route uses this way: drop arrows on it.
        props["oneway"] = ""
        stripped += 1

    if stripped:
        console.info(
            f"Event mode: stripped `oneway` from {stripped} non-featured "
            f"feature(s) so arrows render on the featured route(s) only."
        )
    return stripped > 0


def _apply_event_mode_to_relations(config, trails_geojson):
    """Pre-injection event-mode pass.

    Synthesizes relation_colors and dashed_relations entries for every
    non-featured route ID in trails_geojson.metadata.routes. Curator's
    explicit entries WIN: a route already covered by config[
    "relation_colors"] or config["dashed_relations"] is left alone.

    Mutates `config` in place (and returns it). No-op when event_mode
    is absent.
    """
    em = config.get("event_mode")
    if not em:
        return config

    routes = trails_geojson.get("metadata", {}).get("routes", {}) or {}
    super_expansions = trails_geojson.get("metadata", {}).get("super_relation_expansions", {}) or {}

    featured = _event_mode_featured_set(config, super_expansions)

    bg = _event_mode_background_style(config)
    bg_color = bg["color"]
    bg_pattern = list(bg["pattern"])
    bg_cap = bg.get("cap", "round")

    explicit_relation_colors = config.get("relation_colors") or {}
    explicit_dashed = config.get("dashed_relations") or {}

    new_relation_colors = dict(explicit_relation_colors)
    new_dashed = dict(explicit_dashed)

    for rid_str, info in routes.items():
        if rid_str in featured:
            # Mark featured so the runtime can sort it on top of
            # background routes and render it slightly wider. The
            # flag flows through to CONFIG.routes via the metadata
            # passthrough in inject_config_into_template.
            info["featured"] = True
            continue
        # Custom routes (string ids) were already handled in the
        # pre-enrichment pass via direct mutation of custom_routes
        # entries; their per-route metadata was set during bake-in.
        if info.get("isCustom"):
            continue
        # OSM route: use int key for relation_colors / dashed_relations
        # (matches the existing config conventions; lookups in
        # inject_config_into_template use int keys).
        try:
            rid_int = int(rid_str)
        except ValueError:
            continue
        if rid_int not in explicit_relation_colors:
            new_relation_colors[rid_int] = bg_color
        if rid_int not in explicit_dashed:
            new_dashed[rid_int] = {
                "pattern": bg_pattern,
                "cap": bg_cap,
                "colors": [bg_color],
            }

    config["relation_colors"] = new_relation_colors
    config["dashed_relations"] = new_dashed
    return config
