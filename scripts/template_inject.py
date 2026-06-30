"""Runtime config injection + template/asset assembly.

Builds the CONFIG object the browser app reads (CONFIG_SPEC drives the
straightforward keys; routes / about / logo / etc. get custom handling),
substitutes it into the templates, and copies processed assets (logo,
icons, fonts, sprites) into the build output.
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime

import console
from event_mode import _apply_event_mode_to_relations
from font_trimmer import copy_trimmed_fonts
from generate_icons import generate_icons
from inject_clip_arrow import inject_clip_arrow
from logo import logo_output_filename, process_logo
from validate_config import DEFAULT_VISIBLE_LAYERS

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


# Declarative config spec: (yaml_key, js_key, default).
# Hoisted to module scope so validate_config.py's `--check-spec` drift
# lint can import it. Simple entries flow through `inject_config_into
# _template` automatically; the keys with custom logic (routes,
# directionSchedules, baseLayers, customRoutes, defaultTrailColor,
# about, logoUrl) are handled in a separate block right after the
# loop and intentionally do NOT appear in CONFIG_SPEC. The set
# `validate_config.HANDLED_SPECIALLY` lists those YAML keys so the
# drift lint accepts the omission.
#
# The runtime persists user-facing toggle states (POI visibility,
# season mode, Emergency, labels, Difficulty) in localStorage under
# `mtb.*` keys — there are no `*_default_on` config knobs. House
# defaults live in app.js. The `show_*` fields below gate *data
# fetching* and *build-time asset generation* (e.g. show_markers:
# false skips the Overpass query entirely; show_difficulty: false
# skips IMBA sprite generation), not UI visibility.
CONFIG_SPEC = [
    # Identity
    ("name", "name", None),
    ("slug", "slug", None),
    ("title", "title", None),
    # View geometry.
    # `bbox` still frames the trails for the initial view fit.
    # `pan_bbox` is the looser envelope used for maxBounds (the pan
    # wall) and gets precomputed from `bbox` + `pan_padding` above.
    ("bbox", "bbox", None),
    ("pan_bbox", "panBbox", None),
    ("center", "center", None),
    ("zoom", "zoom", 14),
    ("min_zoom", "minZoom", 10),
    ("max_zoom", "maxZoom", 18),
    # Build-time data gates (skip fetching / sprite-gen when False).
    # `show_markers` merges guideposts + emergency access points into
    # one "trail marker" category — they render identically now.
    ("show_markers", "showMarkers", True),
    ("show_features", "showFeatures", True),
    ("show_parking", "showParking", True),
    ("show_trailheads", "showTrailheads", True),
    ("show_hubs", "showHubs", True),
    ("show_toilets", "showToilets", True),
    ("show_drinking_water", "showDrinkingWater", True),
    ("show_terrain", "showTerrain", True),
    ("show_difficulty", "showDifficulty", True),
    # Distance (meters) from the nearest visible trail within which a
    # trail-marker or feature POI is allowed to render. Tight values
    # (~10 m) hide POIs that aren't directly on the trail; loose
    # values (~75 m+) include nearby attractions but risk surfacing
    # bbox-incidental POIs. Default 50 surfaces typical
    # `tourism=attraction` features (often 10-50 m off-trail) while
    # keeping the filter useful. The Features peek toggle auto-hides
    # if no feature POI passes this check.
    ("poi_proximity_m", "poiProximityMeters", 50),
    # UI gates for the Finder + Labels dropdown. Some systems have no
    # curated routes (trails only) or treat routes and trails as the
    # same set; turning one off hides the matching Finder section and
    # Labels option.
    ("show_routes", "showRoutes", True),
    ("show_trails", "showTrails", True),
    # Build-time data gate for the direction-arrow layer. When False,
    # no arrows are placed on any oneway trail and the Options toggle
    # row is hidden — even if `direction_arrows` is in `forced_visible`
    # (the show gate wins). Use for aesthetic maps that should never
    # display directional indicators regardless of the underlying OSM
    # tagging. Default True mirrors every other show_* gate's
    # "show by default, opt out per-map" pattern.
    ("show_direction_arrows", "showDirectionArrows", True),
    # Display
    # Labels mode default. Was "routes" historically; now defaults to
    # "none" so a fresh-LS visit produces a clean map with the rider
    # opting into labels via the Labels segmented control.
    ("default_labels", "defaultLabels", "none"),
    # Labels mode lock. When set ("routes" / "trails" / "none"), the
    # Labels segmented control is hidden in Options and the rider's
    # persisted choice is ignored. Validated at build time against
    # show_routes / show_trails so the lock can't contradict the
    # surfaced sections.
    # Default "" (empty string) rather than None — None is the
    # "required key" sentinel for inject_config_into_template's loop;
    # the runtime check is `CONFIG.forcedLabels ? lock : free` which
    # treats "" as the unset/free state.
    ("forced_labels", "forcedLabels", ""),
    # Initial colour scheme for first-visit riders. "light" / "dark"
    # / "auto" (auto resolves prefers-color-scheme). Default "light"
    # preserves existing behaviour for maps that don't opt in. The
    # rider can override via the Options Appearance toggle; LS wins
    # over this default on subsequent visits. The build also injects
    # this value into the inline <head> bootstrap script so first
    # paint already has the right scheme set on <html>.
    ("default_color_scheme", "defaultColorScheme", "light"),
    # Whether the brand-img logo should auto-invert in dark mode.
    # Default true matches historical behaviour; curators with
    # colored logos that look bad inverted set false per-map.
    ("invert_logo_dark", "invertLogoDark", True),
    ("color_by", "colorBy", "relation"),
    ("suppress_basemap_path_labels", "suppressBasemapPathLabels", False),
    ("suppress_basemap_pois", "suppressBasemapPois", False),
    ("suppress_basemap_oneway_arrows", "suppressBasemapOnewayArrows", False),
    # When true (the default), highlighting a route or trail dims
    # everything else on the map (basemap tint + non-highlighted
    # arrows/difficulty hidden + POI markers faded) so the highlighted
    # feature reads as a spotlight. Name labels stay visible for
    # wayfinding. Set false per-map to keep every route visible at full
    # brightness behind the highlight ribbon.
    ("map_dim_on_highlight", "mapDimOnHighlight", True),
    # Opacity (0..1) of the dark scrim used for BOTH the in-map spotlight
    # wash while a route/trail is highlighted (only when
    # map_dim_on_highlight is true) AND the Search / Options / About menu
    # backdrops (via the --scrim-opacity CSS var, published in init()).
    # One value so the in-map wash and the menu backdrops share a single
    # density — moving between a highlight and an open menu reads as one
    # continuous wash. Lower keeps the surrounding network legible (you
    # can still trace the connecting trails needed to reach the
    # highlighted one); higher is a stronger dim. Default 0.40.
    ("scrim_opacity", "scrimOpacity", 0.40),
    # When true (the default), a highlighted route/trail gets a soft
    # amber selection glow beneath the ribbon so it reads as "selected"
    # at a glance — including dark/black routes that would otherwise be
    # easy to lose. Set false to drop back to the outline + stroke ribbon
    # with no glow.
    ("highlight_glow", "highlightGlow", True),
    # When true (the default), MapLibre writes `#zoom/lat/lon` to
    # the URL hash as the user pans/zooms, and honours any hash on
    # page load. Makes views shareable and survives reload, at the
    # cost of leaking last-viewed location in the address bar /
    # screenshots / screen-shares. Set false to drop the hash
    # entirely (URL stays clean, no persistence across reload, no
    # shareable deep-links).
    ("url_hash", "urlHash", False),
    # Distance units for every distance/elevation display in the
    # app: off-screen indicator pill, route stats in the Finder,
    # highlight chip, any future distance display. The underlying
    # data is always meters in trails.geojson; this setting only
    # affects render-time formatting. "mi" → ft + decimal mi (and
    # ft for elevation gain); "km" → m + decimal km (and m for
    # elevation gain). Validator (validate_config.py) restricts the
    # value to "mi" or "km".
    ("distance_units", "distanceUnits", "mi"),
    # Share button in the expanded sheet (above Install). When true
    # (default), generates a shareable URL of the current view +
    # highlighted route/trail and surfaces it via the Web Share API
    # (or clipboard fallback). When false, the entire share section
    # is stripped from index.html at build time. Set false for maps
    # where the curator wants no share affordance (e.g. private/
    # family maps); leave true for community/public maps.
    ("share_button", "shareButton", True),
    # Marker colours (kept per user request; some systems have
    # branded marker palettes aligned with their trail colours).
    # parking/trailhead/feature colours flow to CSS custom
    # properties on :root so the peek-bar swatch, the on-map
    # marker, and the popup badge all stay in lockstep.
    ("marker_color", "markerColor", "#795548"),
    ("marker_text_color", "markerTextColor", "white"),
    ("marker_border_color", "markerBorderColor", "white"),
    ("parking_color", "parkingColor", "#2980b9"),
    ("parking_text_color", "parkingTextColor", "white"),
    ("parking_border_color", "parkingBorderColor", "white"),
    ("trailhead_color", "trailheadColor", "#27ae60"),
    ("trailhead_text_color", "trailheadTextColor", "white"),
    ("trailhead_border_color", "trailheadBorderColor", "white"),
    ("hub_color", "hubColor", "#f39c12"),
    ("hub_text_color", "hubTextColor", "white"),
    ("hub_border_color", "hubBorderColor", "white"),
    ("feature_color", "featureColor", "#8e44ad"),
    ("feature_ring_color", "featureRingColor", "#ffffff"),
    # PWA
    ("pwa", "pwa", True),
    # When true, surface PWA install affordances on platforms that
    # support them (Chrome's mini-infobar + our custom Install button
    # via beforeinstallprompt; iOS Safari Add-to-Home-Screen
    # instructions). Default false because not every map wants install
    # promotion (e.g. a personal/family map). When false, the
    # beforeinstallprompt handler is not registered at all — silencing
    # Chrome's "page must call prompt()" warning — and the custom
    # Install button is hidden everywhere.
    ("pwa_install_prompt", "pwaInstallPrompt", True),
    # User-supplied
    ("parking", "parking", []),
]


def inject_config_into_template(template_content, config, trails_geojson):
    """Replace the CONFIG placeholder in templates with actual config data."""
    # Extract route metadata from the trails GeoJSON
    routes = {}
    if trails_geojson and "metadata" in trails_geojson:
        routes = trails_geojson["metadata"].get("routes", {})

    # Event-mode pre-pass on the relations side. Synthesises
    # relation_colors and dashed_relations entries for every
    # non-featured OSM route so the override loop below applies the
    # background style. No-op when event_mode is absent. Curator's
    # explicit per-relation entries WIN.
    _apply_event_mode_to_relations(config, trails_geojson)

    # Apply route overrides (winter, colour, dash) in a single pass.
    # YAML keys keep their original "relation" names since they take OSM
    # relation IDs as input; the values populate route info on the JS side.
    winter_ids = set(config.get("winter_relations") or [])
    relation_colors = config.get("relation_colors") or {}
    dashed_relations = config.get("dashed_relations") or {}

    # Direction schedule controls when ways tagged oneway=yes/-1/reversible
    # have their arrows rotated 180° (day-of-week alternation). Single
    # hierarchical key with two parts:
    #
    #   direction_schedule:
    #     reverse_days: [...]    # system-wide default for every route
    #     per_route:             # per-relation overrides (optional)
    #       <rel_id>:
    #         reverse_days: [...]
    #
    # An explicit per-route entry always wins, even if its reverse_days is
    # empty — that's the way to opt one route out of the system-wide
    # default. Relations NOT mentioned under per_route fall back to the
    # default.
    #
    # Relations are just the grouping handle here — relations themselves don't
    # have direction; their member ways do.
    VALID_WEEKDAYS = {"sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"}
    # Parity tokens extend the vocabulary: a route with `reverse_days:
    # [even_days]` flips 180° on any even calendar date (2, 4, 6 …); `odd_days`
    # is the mirror. These are evaluated at runtime the same way weekday tokens
    # are — any match in the list triggers reversal — so mixing them is fine
    # ("today is Monday OR today is even"), though most users will use one form
    # or the other. The canonical snake_case forms are what gets emitted into
    # CONFIG.directionSchedules; short forms like "even" or "odd" are accepted
    # for author convenience.
    VALID_PARITY = {"even_days", "odd_days"}
    VALID_DAYS = VALID_WEEKDAYS | VALID_PARITY

    def _normalise_days(days_in, error_label):
        days_norm = []
        for d in days_in or []:
            dl = str(d).strip().lower()
            match = next(
                (
                    full
                    for full in VALID_DAYS
                    if full == dl or (len(dl) >= 3 and full.startswith(dl))
                ),
                None,
            )
            if match is None:
                sys.exit(
                    f"ERROR: {error_label} contains unknown day token {d!r}; "
                    f"valid: {sorted(VALID_DAYS)}"
                )
            days_norm.append(match)
        return sorted(set(days_norm))

    # Pull the schedule block. Both halves are optional; an absent
    # block means no rotation anywhere on the map.
    sched_block = config.get("direction_schedule") or {}

    # System-wide default. Stored as None when unset OR when explicitly
    # set with empty reverse_days (degenerate; treated the same as unset).
    def_sched_days = _normalise_days(
        sched_block.get("reverse_days"),
        "direction_schedule.reverse_days",
    )
    def_sched_norm = {"reverse_days": def_sched_days} if def_sched_days else None

    # Per-relation overrides. Two-pass processing so super-relation
    # entries (auto-expanded via metadata.super_relation_expansions)
    # propagate to children, but explicit per-child entries always win:
    #   Pass 1 — process every entry whose key is a LEAF (or absent
    #            from the expansion table). These take precedence.
    #   Pass 2 — process super-relation entries, fanning out to
    #            children only if the child wasn't already set in
    #            Pass 1.
    # This lets a curator write `per_route: { 99999999: { reverse_days:
    # [] } }` for a whole second trail system that doesn't reverse,
    # while still individually overriding one of its child routes if
    # needed.
    super_expansions = trails_geojson.get("metadata", {}).get("super_relation_expansions", {}) or {}
    per_route_raw = sched_block.get("per_route") or {}
    sched_processed = {}

    # Pass 1: explicit leaves and any keys not in the expansion table.
    deferred_supers = []
    for rel_id, spec in per_route_raw.items():
        rel_id_str = str(rel_id)
        if rel_id_str in super_expansions:
            deferred_supers.append((rel_id, spec))
            continue
        days = _normalise_days(
            (spec or {}).get("reverse_days"),
            f"direction_schedule.per_route[{rel_id}].reverse_days",
        )
        # Even an empty list is recorded — that's how a user opts a single
        # route out of the system-wide default.
        sched_processed[int(rel_id)] = {"reverse_days": days}

    # Pass 2: super-relations fan out to children (without clobbering
    # an explicit per-child entry from Pass 1).
    for rel_id, spec in deferred_supers:
        days = _normalise_days(
            (spec or {}).get("reverse_days"),
            f"direction_schedule.per_route[{rel_id}].reverse_days",
        )
        for child_id in super_expansions[str(rel_id)]:
            child_int = int(child_id)
            if child_int not in sched_processed:
                sched_processed[child_int] = {"reverse_days": days}

    # Resolve the effective schedule for each route: per-relation override if
    # present (whether empty or not), else the default if any.
    def effective_schedule(rel_id):
        if rel_id in sched_processed:
            return sched_processed[rel_id]
        return def_sched_norm  # None if no default set

    effective_schedules = {}
    for route_id_str, route_info in routes.items():
        # Custom routes have non-numeric string ids and no direction
        # schedules. Skip them so int() doesn't blow up.
        if route_info.get("isCustom"):
            continue
        rid = int(route_id_str)
        eff = effective_schedule(rid)
        if eff and eff.get("reverse_days"):
            effective_schedules[rid] = eff

    # Validate oneway=reversible features against the resolved schedules.
    # fetch_trails.py performs the same check against raw OSM data, but it
    # only runs when the GeoJSON is being (re)built. A "config-only" rebuild
    # — `build.py <config>` without --trails/--force — reuses the cached
    # GeoJSON and would otherwise skip the check. We re-validate here so the
    # build always fails when a reversible way has no schedule covering it,
    # regardless of whether trails were refetched this run.
    if trails_geojson and trails_geojson.get("features"):
        # scheduled_ids is compared against feature `shared_routes`, which are
        # stringified in the enriched GeoJSON. Stringify the keys to match.
        scheduled_ids = set(str(k) for k in effective_schedules.keys())
        unscheduled = []  # list of (way_id_or_None, parent_route_ids)
        unscheduled_no_wayids = 0  # features missing way_ids (legacy cache)
        seen = set()
        for feat in trails_geojson["features"]:
            props = feat.get("properties", {})
            if props.get("oneway") != "reversible":
                continue
            parents = set(props.get("shared_routes") or [])
            if parents & scheduled_ids:
                continue
            way_ids = props.get("way_ids") or []
            if not way_ids:
                # Cached GeoJSON predates the way_ids field. We can still flag
                # the problem at the route level, just not point at specific
                # OSM ways. Recommend rebuilding trails to get URLs.
                unscheduled_no_wayids += 1
                key = ("__route__", tuple(sorted(parents)))
                if key not in seen:
                    seen.add(key)
                    unscheduled.append((None, sorted(parents)))
                continue
            for wid in way_ids:
                if wid in seen:
                    continue
                seen.add(wid)
                unscheduled.append((wid, sorted(parents)))
        if unscheduled:
            lines = [
                "ERROR: Found oneway=reversible way(s) without a direction",
                "       schedule covering them. Reversible trails change",
                "       direction by schedule and cannot render correctly",
                "       without one.",
                "",
                "       Either set a system-wide schedule that covers every route:",
                "",
                "         direction_schedule:",
                "           reverse_days: [tuesday, thursday, saturday]",
                "",
                "       …or schedule the specific parent relation:",
                "",
                "         direction_schedule:",
                "           per_route:",
                "             <relation_id>:",
                "               reverse_days: [tuesday, thursday, saturday]",
                "",
                "       Offending ways (way_id → parent relation IDs):",
            ]
            for way_id, parents in unscheduled[:20]:
                if way_id is None:
                    lines.append(
                        f"         (way_ids unavailable in cached GeoJSON)"
                        f"  →  parent relations: {parents}"
                    )
                else:
                    lines.append(
                        f"         https://www.openstreetmap.org/way/{way_id}  →  {parents}"
                    )
            if len(unscheduled) > 20:
                lines.append(f"         ... and {len(unscheduled) - 20} more")
            if unscheduled_no_wayids:
                lines.append("")
                lines.append("       Tip: rerun with --trails to refresh the cached")
                lines.append("       GeoJSON; new builds include OSM way IDs so this")
                lines.append("       message will list specific ways.")
            sys.exit("\n".join(lines))

    for route_id_str, route_info in routes.items():
        # Custom routes carry their own style + flags (set at enrichment
        # time) and don't participate in the int-keyed OSM overrides.
        if route_info.get("isCustom"):
            # Still normalise dashed to a concrete value so the runtime
            # can assume the field is present.
            if "dashed" not in route_info:
                route_info["dashed"] = False
            continue

        route_id = int(route_id_str)

        # Winter override — kept for the legacy "seasonal" field on route
        # info (backward compat with the current template). The three
        # bucket flags (summer/winter/emergency) are set in
        # _enrich_trails_geojson before this function runs.
        if route_id in winter_ids:
            route_info["seasonal"] = "winter"

        # Colour override
        color_override = relation_colors.get(route_id)
        if color_override:
            route_info["colour"] = color_override

        # Dash pattern override
        raw = dashed_relations.get(route_id)
        if raw:
            if isinstance(raw, list):
                route_info["dashed"] = raw
            elif isinstance(raw, dict):
                route_info["dashed"] = raw.get("pattern", [2, 2])
                if raw.get("cap"):
                    route_info["dashCap"] = raw["cap"]
                if raw.get("colors"):
                    route_info["dashColors"] = raw["colors"]
            else:
                route_info["dashed"] = False
        else:
            route_info["dashed"] = False

        # Mark routes that have an effective direction schedule (per-relation
        # override or system-wide default) so the runtime can find them quickly
        # without scanning the schedule map.
        if route_id in effective_schedules:
            route_info["hasDirectionSchedule"] = True

    config_obj = {}
    for yaml_key, js_key, default in CONFIG_SPEC:
        if default is None:
            config_obj[js_key] = config[yaml_key]  # required keys
        else:
            config_obj[js_key] = config.get(yaml_key, default)

    # Keys with custom logic
    config_obj["routes"] = routes
    config_obj["directionSchedules"] = {
        # JSON object keys must be strings; the runtime parses them to Number.
        # We emit the *resolved* per-route schedules (default expanded out for
        # every route it applies to, with per-relation overrides honored) so
        # the runtime doesn't need to know about the default/override layering.
        str(rel_id): spec
        for rel_id, spec in effective_schedules.items()
    }
    config_obj["baseLayers"] = config.get("base_layers") or []
    config_obj["customRoutes"] = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "color": entry["color"],
            "description": entry.get("description", ""),
        }
        for entry in (config.get("custom_routes") or [])
    ]

    # Event-mode runtime hints. The runtime uses these to:
    #   - eventModeActive: gate the always-on event-mode UX changes
    #     (force Labels mode to "routes" + restrict labels to featured
    #     routes only + hide the Labels segmented control).
    #   - eventPoiColor: chip background for the always-on event POIs.
    #   - hasEventPois: presence flag so addEventPoiMarkers runs at boot.
    em = config.get("event_mode") or {}
    config_obj["eventModeActive"] = bool(em)
    config_obj["eventPoiColor"] = em.get("poi_color", "")
    config_obj["hasEventPois"] = bool(em.get("pois"))
    # default_trail_color: string or object with color/pattern/cap
    dtc = config.get("default_trail_color", "#808080")
    if isinstance(dtc, dict):
        config_obj["defaultTrailColor"] = dtc.get("color", "#808080")
        config_obj["defaultTrailDash"] = dtc.get("pattern", False)
        config_obj["defaultTrailCap"] = dtc.get("cap", "round")
    else:
        config_obj["defaultTrailColor"] = dtc
        config_obj["defaultTrailDash"] = False
        config_obj["defaultTrailCap"] = "round"

    config_obj["buildDate"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    config_obj["dataDate"] = config.get("_data_date", "")
    config_obj["hasClipEndpoints"] = bool(config.get("_has_clip_endpoints"))
    # Build-time scan for trail-property gates that surface Options
    # toggles. Done at build time (rather than counting placed
    # decorations at runtime) because the first computeDecorations()
    # pass is deferred to map.once('idle', …) for first-paint perf,
    # so a runtime count would race the deferral and read 0 at gate
    # time. Currently scans for:
    #   - any oneway-tagged feature → CONFIG.hasOnewayTrails →
    #     direction-arrow toggle
    #   - any mtb:scale:imba-tagged feature → CONFIG.hasDifficultyTrails
    #     → difficulty toggle
    # See setupFloatingChrome() in app.js where these are read.
    has_oneway = False
    has_difficulty = False
    for f in (trails_geojson.get("features") or []) if trails_geojson else []:
        props = f.get("properties") or {}
        if not has_oneway:
            ow = props.get("oneway")
            if ow in ("yes", True, "-1", "reversible"):
                has_oneway = True
        if not has_difficulty:
            imba = props.get("imba_difficulty")
            if imba and str(imba).strip():
                has_difficulty = True
        if has_oneway and has_difficulty:
            break
    config_obj["hasOnewayTrails"] = has_oneway
    config_obj["hasDifficultyTrails"] = has_difficulty
    # Per-mode route orderings from MLNCM optimization (see
    # route_order.compute_route_orders). Runtime app.js looks up the
    # active mode's order in computeOffsetsAndFilter / computeLabelData
    # to keep within-corridor offsets side-stable across adjacent
    # corridors. Missing → app.js falls back to natural-sort
    # (legacy behavior).
    config_obj["routeOrders"] = (
        (trails_geojson.get("metadata") or {}).get("routeOrders") or {} if trails_geojson else {}
    )
    # Stable-lane corridor baselines per mode: {mode: {corridor_key:
    # baseline}}. computeOffsetsAndFilter / computeLabelData add the
    # baseline to a route's in-corridor position so routes hold a lane
    # instead of re-centering ("breathing") when neighbors join/leave.
    # Missing → app.js falls back to the legacy centered offset.
    config_obj["corridorBaselines"] = (
        (trails_geojson.get("metadata") or {}).get("corridorBaselines") or {}
        if trails_geojson
        else {}
    )
    config_obj["about"] = config.get("about") or None
    # Welcome modal config: pass through unchanged. Three forms
    # accepted: omitted (None → framework default), false (modal
    # suppressed), or a dict with title/body/show_controls_hint.
    # Distinguish "explicitly false" from "omitted" — `or` would
    # collapse both to None, which the runtime would interpret as
    # "use defaults" and still show the modal.
    config_obj["welcome"] = config.get("welcome") if "welcome" in config else None
    # default_visible: list of layer names that default to ON for
    # first-visit riders. Three accepted YAML forms:
    #   - omitted: empty list (everything off)
    #   - "all":   expand to the full layer list
    #   - list:   pass through (validator already checked names)
    # Runtime always sees a list, so isDefaultVisible() can do a
    # plain .includes() check.
    raw_default_visible = config.get("default_visible")
    if raw_default_visible == "all":
        config_obj["defaultVisible"] = sorted(DEFAULT_VISIBLE_LAYERS)
    elif isinstance(raw_default_visible, list):
        config_obj["defaultVisible"] = list(raw_default_visible)
    else:
        config_obj["defaultVisible"] = []
    # forced_visible: list of layer names whose toggle row is hidden
    # AND whose layer is force-rendered ON regardless of LS state /
    # default_visible. Same shape + accepted-name set as
    # default_visible. Runtime checks isForcedVisible(name) before
    # any toggle wiring; matched layers skip the toggle and are
    # rendered visible at boot.
    raw_forced_visible = config.get("forced_visible")
    if raw_forced_visible == "all":
        config_obj["forcedVisible"] = sorted(DEFAULT_VISIBLE_LAYERS)
    elif isinstance(raw_forced_visible, list):
        config_obj["forcedVisible"] = list(raw_forced_visible)
    else:
        config_obj["forcedVisible"] = []
    # Accent palette: resolved at build time (see _accent_palette
    # stash in build.py). The build always produces a 4-value palette —
    # a deep light-mode shade + a lightened dark-mode shade, each with
    # its best on-accent text colour — from the logo pick / explicit
    # hex / framework default. app.js sets the four BASE vars on :root
    # and style.css maps --accent / --on-accent per [data-color-scheme],
    # so the accent reads correctly in both schemes. accentColor (= the
    # light shade) stays emitted for back-compat / external readers.
    accent_palette = config.get("_accent_palette") or {}
    config_obj["accentLight"] = accent_palette.get("light")
    config_obj["accentDark"] = accent_palette.get("dark")
    config_obj["onAccentLight"] = accent_palette.get("onLight")
    config_obj["onAccentDark"] = accent_palette.get("onDark")
    config_obj["accentColor"] = accent_palette.get("light")

    # Per-type POI counts (computed from pois.geojson at build
    # time — see _poi_counts stash). Drives the dynamic Welcome
    # Search line so it only mentions place types actually present
    # on this map.
    config_obj["poiCounts"] = config.get("_poi_counts") or {}

    # Logo: derived from `logo:` if set, else falls back to `icon:`. Processed
    # in copy_assets() into a normalized `logo.webp` (raster) or `logo.svg`
    # (vector). Only emit logoUrl when the chosen source actually exists.
    project_root_for_logo = os.path.dirname(SCRIPTS_DIR)
    logo_p = config.get("logo") or ""
    icon_p = config.get("icon") or ""
    logo_source_rel = ""
    if logo_p and os.path.isfile(os.path.join(project_root_for_logo, logo_p)):
        logo_source_rel = logo_p
    elif not logo_p and icon_p and os.path.isfile(os.path.join(project_root_for_logo, icon_p)):
        logo_source_rel = icon_p
    config_obj["logoUrl"] = logo_output_filename(logo_source_rel) if logo_source_rel else None

    config_json = json.dumps(config_obj, indent=2)
    return template_content.replace("/*__CONFIG__*/", f"const CONFIG = {config_json};")


# Pillow-readable raster extensions used by the icon-source resolver
# below. SVG and PDF aren't readable by Pillow, so a logo with one of
# those extensions can't fall back to icon source — generate_icons
# would crash. Defined at module scope so resolve_icon_source() and
# the icon-generation call site (copy_assets) share one definition.
_PIL_READABLE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def resolve_icon_source(config, project_root):
    """Return the resolved icon source path, or "" if none exists.

    Three input shapes, in priority order:
      1. config["icon"]: explicit. Returned as-is when set (any value,
         even non-raster, is the curator's call — generate_icons will
         flag the failure if Pillow can't read it).
      2. config["logo"]: automatic fallback when icon: is unset, but
         only when the logo file exists AND has a Pillow-readable
         extension. SVG logos can't fall back (Pillow won't read).
      3. Empty string: no usable source. Caller should skip icon
         generation AND strip the icon links from the HTML.

    Used by both copy_templates (to decide whether to keep the icons
    HTML block) AND copy_assets (to decide whether to call
    generate_icons). One function so they can't drift — the previous
    bug was copy_templates checking only `icon:` while copy_assets
    also accepted `logo:`, producing builds with manifest+icons on
    disk but no <link rel="manifest"> in the HTML.
    """
    icon_path = config.get("icon", "")
    if icon_path:
        return icon_path
    logo_path = config.get("logo", "")
    if logo_path:
        ext = os.path.splitext(logo_path)[1].lower()
        if ext in _PIL_READABLE_EXTS:
            candidate = os.path.join(project_root, logo_path)
            if os.path.isfile(candidate):
                return logo_path
    return ""


def copy_templates(config, output_dir, trails_geojson):
    """Copy and process HTML/JS/CSS templates."""
    project_root = os.path.dirname(SCRIPTS_DIR)
    templates_dir = os.path.join(project_root, "templates")

    for filename in ["index.html", "app.js", "style.css"]:
        src = os.path.join(templates_dir, filename)
        if not os.path.exists(src):
            console.warn(f"Template not found: {src}")
            continue

        with open(src, encoding="utf-8") as f:
            content = f.read()

        # Inject config into JS files
        if filename.endswith(".js"):
            content = inject_config_into_template(content, config, trails_geojson)

        # Process HTML template
        if filename == "index.html":
            # Dynamic page title
            content = re.sub(
                r"<title>.*?</title>",
                f"<title>{config['title']}</title>",
                content,
            )

            # Open Graph + Twitter Card metadata. Always-on (no gate) —
            # benefits search engines and the Share-button preview cards
            # equally. Values are HTML-attribute-escaped to survive
            # quotes / ampersands in trail-system names + descriptions.
            og_title = config.get("title") or config.get("name") or "Trail Map"
            about = config.get("about") or {}
            og_description_raw = (about.get("description") or "").strip()
            # First paragraph only (split on the first double-newline);
            # cap at ~200 chars to avoid runaway snippet length in
            # share previews.
            og_description = og_description_raw.split("\n\n", 1)[0].strip()
            if len(og_description) > 200:
                og_description = og_description[:197].rstrip() + "..."
            # If no description configured, fall back to the title so
            # OG previews still have something readable instead of an
            # empty `content=""` attribute.
            if not og_description:
                og_description = og_title
            # html.escape with quote=True turns " into &quot; so the
            # value is safe inside the `content="..."` attribute.
            from html import escape as _html_escape

            content = content.replace("__OG_TITLE__", _html_escape(og_title, quote=True))
            content = content.replace(
                "__OG_DESCRIPTION__", _html_escape(og_description, quote=True)
            )

            # Strip the Share button section when share_button: false.
            # Default true — the section's `hidden` class is only used
            # to keep the section invisible until app.js reveals it.
            if not config.get("share_button", True):
                content = re.sub(
                    r"\s*<!-- Share start -->.*?<!-- Share end -->\n",
                    "",
                    content,
                    flags=re.DOTALL,
                )

            # Brand title — substitute the map's title text into both
            # the alt= on the brand-img (used by screen readers + as a
            # fallback when the image is missing) AND the brand-title
            # span text (shown when no logo is configured at all). Same
            # value as the OG title.
            brand_title = config.get("title") or config.get("name") or "Trail Map"
            content = content.replace("__BRAND_TITLE__", _html_escape(brand_title, quote=True))

            # Brand-img CLS-prevention dimensions. process_logo() stashes
            # the actual written pixel dimensions on config["_brand_img_dims"]
            # (or (None, None) when it couldn't determine them — Pillow
            # missing, SVG without viewBox, etc.). Substitute width/height
            # into the <img> tag when known; otherwise emit the empty
            # string so the tag stays valid HTML and we accept a small
            # CLS hit rather than emit wrong dimensions. The CSS
            # (style.css #brand-img: max-width 200px, max-height 48px,
            # width/height auto) still controls actual render size; the
            # HTML attrs only set the aspect ratio used by the browser
            # to reserve layout box before image bytes arrive.
            #
            # fetchpriority="high" is unconditional in the template — it
            # makes the brand-img the LCP image regardless of whether we
            # could determine dims. Browsers without fetchpriority support
            # ignore the attribute (no regression).
            brand_dims = config.get("_brand_img_dims") or (None, None)
            bw, bh = brand_dims
            if bw and bh:
                content = content.replace("__BRAND_IMG_DIMS__", f' width="{bw}" height="{bh}"')
            else:
                content = content.replace("__BRAND_IMG_DIMS__", "")

            # Inline colour-scheme bootstrap script. Runs synchronously
            # in <head> BEFORE the stylesheet, so first paint already
            # has the right data-color-scheme attribute on <html> and
            # CSS variables resolve to the correct values without FOUC.
            # Slug + default-scheme are baked in at build time; the
            # snippet reads LS / falls back to the default / resolves
            # "auto" against prefers-color-scheme.
            slug = config.get("slug", "")
            default_scheme = config.get("default_color_scheme", "light")
            bootstrap_slug = json.dumps(slug)  # JS string-safe
            bootstrap_default = json.dumps(default_scheme)
            # Accent base vars, baked into the pre-paint bootstrap so the
            # per-map accent is correct on the FIRST frame — before app.js
            # (which carries CONFIG) has downloaded. Without this, a slow
            # first load paints accent-coloured chrome (notably the
            # initial-load progress bar) with style.css's default
            # --accent-light blue until app.js patches it: a visible colour
            # flash. Inline style on <html> beats the stylesheet :root
            # defaults; app.js still sets the same four vars later
            # (idempotent). style.css maps --accent / --on-accent from
            # these per [data-color-scheme], so a missing palette here just
            # falls back to the stylesheet defaults (accent_js empty).
            _accent_palette = config.get("_accent_palette") or {}
            accent_js = ""
            for _av_name, _av_val in (
                ("--accent-light", _accent_palette.get("light")),
                ("--accent-dark", _accent_palette.get("dark")),
                ("--on-accent-light", _accent_palette.get("onLight")),
                ("--on-accent-dark", _accent_palette.get("onDark")),
            ):
                if _av_val:
                    accent_js += "d.style.setProperty(%s,%s);" % (
                        json.dumps(_av_name), json.dumps(_av_val))
            # The runtime stores LS values JSON-stringified (see
            # LS.set in app.js: setItem(..., JSON.stringify(value))),
            # so a stored "dark" preference is on disk as the
            # 6-char string `"dark"` (with literal quote marks).
            # The bootstrap parses it back; on parse failure (older
            # raw values, or future schema drift) it falls through
            # to the curator default rather than blocking on a
            # broken LS entry.
            bootstrap_script = (
                "<script>"
                "(function(){"
                "try{"
                "var d=document.documentElement;"
                f'var raw=localStorage.getItem({bootstrap_slug}+".mtb.colorScheme");'
                "var stored=null;"
                "if(raw){try{stored=JSON.parse(raw);}catch(e){stored=raw;}}"
                f"var s=stored||{bootstrap_default};"
                'if(s==="auto"){'
                's=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";'
                "}"
                'document.documentElement.setAttribute("data-color-scheme",s);'
                + accent_js +
                # Sync the meta theme-color tag in the same pass so
                # the Android Chrome PWA status bar paints the right
                # colour on first frame (the static value in the
                # template is just a fallback; without this update,
                # a dark-mode PWA would show a light status bar
                # until applyColorScheme runs much later in app.js).
                "var m=document.querySelector('meta[name=\"theme-color\"]');"
                "if(!m){m=document.createElement('meta');m.setAttribute('name','theme-color');document.head.appendChild(m);}"
                'm.setAttribute(\'content\',s==="dark"?"#1c1c1e":"#ffffff");'
                "}catch(e){}"
                "})();"
                "</script>"
            )
            content = content.replace("__COLOR_SCHEME_BOOTSTRAP__", bootstrap_script)

            # Inject or remove brand image. Logo source falls back to
            # icon: when logo: is omitted; raster sources are normalized
            # to `logo.webp` in copy_assets() while SVG sources are
            # copied as `logo.svg`. The template ships the brand-img
            # with src="logo.webp"; rename to .svg if the source is
            # vector. Strip the brand-img tag entirely when neither
            # source is set, so the brand element falls back to the
            # title span only.
            logo_path = config.get("logo", "")
            icon_path_for_logo = config.get("icon", "")
            logo_chosen = logo_path or icon_path_for_logo
            if logo_chosen:
                out_name = logo_output_filename(logo_chosen)
                if out_name != "logo.webp":
                    content = content.replace("logo.webp", out_name)
            else:
                content = re.sub(
                    r"\s*<!-- Brand img start -->.*?<!-- Brand img end -->\n",
                    "",
                    content,
                    flags=re.DOTALL,
                )

            # Strip PWA install UI and SW registration when PWA is disabled
            if not config.get("pwa", True):
                content = re.sub(
                    r"\s*<!-- PWA start -->.*?<!-- PWA end -->\n",
                    "",
                    content,
                    flags=re.DOTALL,
                )
                content = re.sub(
                    r"\s*<!-- SW start -->.*?<!-- SW end -->\n",
                    "",
                    content,
                    flags=re.DOTALL,
                )

            # Strip icon links when no icon source is resolvable.
            # Uses the same fallback logic as copy_assets so a config
            # with `logo:` set but no `icon:` keeps the manifest /
            # apple-touch / theme-color links — the icons get
            # generated from the logo and the HTML correctly points
            # at them. (Without this consistency, the HTML strip
            # would remove the manifest link even though copy_assets
            # would happily generate a manifest from the logo,
            # leaving a build with icons on disk but no PWA install.)
            if not resolve_icon_source(config, project_root):
                content = re.sub(
                    r"\s*<!-- Icons start -->.*?<!-- Icons end -->\n",
                    "",
                    content,
                    flags=re.DOTALL,
                )

        dst = os.path.join(output_dir, filename)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(content)
        console.info(f"Copied {filename}")


def copy_assets(config, output_dir):
    """Copy logo, icons, fonts, and sprites."""
    project_root = os.path.dirname(SCRIPTS_DIR)

    # Logo: resampled to a bounding-box render size and written as logo.webp.
    # If `logo:` is omitted but `icon:` is set, the icon source is used as the
    # logo automatically (square icons render as ~48x48 badges).
    logo_path = config.get("logo", "")
    icon_path = config.get("icon", "")
    logo_src = None
    if logo_path:
        candidate = os.path.join(project_root, logo_path)
        if os.path.isfile(candidate):
            logo_src = candidate
        else:
            console.warn(f"Logo not found: {candidate}")
    elif icon_path:
        candidate = os.path.join(project_root, icon_path)
        if os.path.isfile(candidate):
            logo_src = candidate
            console.info("No logo configured — using icon as logo")
    if logo_src:
        out_name = logo_output_filename(logo_src)
        out_path = os.path.join(output_dir, out_name)
        # Capture the written logo's pixel dimensions so copy_templates
        # can substitute them into the brand-img element's HTML
        # width/height attributes (CLS prevention — browsers reserve
        # layout box from these before image bytes arrive). Returns
        # (None, None) for unrecognised SVGs / Pillow-missing / etc.;
        # copy_templates falls back to omitting the attributes in that
        # case, accepting the small CLS risk over emitting wrong dims.
        config["_brand_img_dims"] = process_logo(logo_src, out_path)

    # Icons — generated from the resolved source image. Source resolution
    # (icon: → logo: → none) is shared with the HTML icons-block strip in
    # copy_templates via resolve_icon_source(); the manifest is written
    # inside generate_icons().
    icon_path = resolve_icon_source(config, project_root)
    if icon_path and icon_path != config.get("icon", ""):
        # The fallback fired — the resolved source is the logo, not
        # an explicit icon: setting. Log it so the curator knows.
        console.info("No icon configured — using logo as icon source")
    if icon_path:
        icon_src = os.path.join(project_root, icon_path)
        generate_icons(icon_src, output_dir, config)
    else:
        console.info("No icon configured — skipping icon generation")

    # Fonts (trimmed based on map data)
    fonts_src = os.path.join(project_root, "assets", "fonts")
    copy_trimmed_fonts(output_dir, fonts_src)

    # Sprites — only copy the version referenced by app.js
    sprites_src = os.path.join(project_root, "assets", "sprites")
    sprites_dst = os.path.join(output_dir, "sprites")
    app_js_path = os.path.join(output_dir, "app.js")
    sprite_version = None
    if os.path.exists(app_js_path):
        with open(app_js_path, encoding="utf-8") as f:
            m = re.search(r"sprites/(v\d+)/", f.read())
            if m:
                sprite_version = m.group(1)
    sprites_injected_dirs = []  # collect for clip-arrow injection below
    if sprite_version:
        ver_src = os.path.join(sprites_src, sprite_version)
        ver_dst = os.path.join(sprites_dst, sprite_version)
        if os.path.exists(ver_src):
            if os.path.exists(sprites_dst):
                shutil.rmtree(sprites_dst)
            os.makedirs(sprites_dst, exist_ok=True)
            shutil.copytree(ver_src, ver_dst)
            console.info(f"Copied sprites ({sprite_version})")
            sprites_injected_dirs.append(ver_dst)
        else:
            console.warn(f"Sprites {sprite_version} not found at {ver_src}")
            console.info("Download from: https://github.com/protomaps/basemaps-assets")
    elif os.path.exists(sprites_src) and os.listdir(sprites_src):
        if os.path.exists(sprites_dst):
            shutil.rmtree(sprites_dst)
        shutil.copytree(sprites_src, sprites_dst)
        console.info("Copied sprites (all versions)")
        # Inject into every version directory found.
        for entry in sorted(os.listdir(sprites_dst)):
            sub = os.path.join(sprites_dst, entry)
            if os.path.isdir(sub):
                sprites_injected_dirs.append(sub)
    else:
        console.warn(f"Sprites not found at {sprites_src}")
        console.info("Download from: https://github.com/protomaps/basemaps-assets")

    # Inject the SDF clip-continuation arrowhead into each copied atlas so
    # the renderer can tint it per-route via icon-color. Idempotent — a no-op
    # on rebuilds where the icon is already present.
    sdf_1x = os.path.join(project_root, "assets", "extras", "clip-arrow.sdf.png")
    sdf_2x = os.path.join(project_root, "assets", "extras", "clip-arrow.sdf@2x.png")
    if os.path.exists(sdf_1x) and os.path.exists(sdf_2x):
        for sprite_dir in sprites_injected_dirs:
            inject_clip_arrow(sprite_dir, sdf_1x, sdf_2x)
    else:
        console.warn(
            "clip-arrow SDF assets missing — continuation "
            "arrowheads will not render. Run "
            "`python assets/extras/generate_clip_arrow.py` to regenerate."
        )
