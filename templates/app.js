/* MTB Trail Map Framework — Map Viewer */
/* global maplibregl, pmtiles, basemaps */

/*__CONFIG__*/

// Set the peek-title text synchronously so it never flashes a
// placeholder. This script tag lives at the end of <body>, so the
// element already exists; CONFIG is injected above and is available
// here. Running this before any map-init async work means the first
// paint carries the real title.
(function setPeekTitleEarly() {
    const el = document.getElementById("sheet-peek-title");
    if (el) el.textContent = CONFIG.name || CONFIG.title || "Trail Map";
})();

// Push YAML-configured POI colours onto :root as CSS custom
// properties so the peek-bar swatches, on-map markers, and popup
// badges all read the same single source of truth. Done before
// any markers or stylesheets evaluate against the default values
// so the first paint carries the real colours.
(function setPoiColorVars() {
    const root = document.documentElement;
    // Trail markers (merged guideposts + emergency access)
    if (CONFIG.markerColor)          root.style.setProperty("--marker-color",          CONFIG.markerColor);
    if (CONFIG.markerTextColor)      root.style.setProperty("--marker-text-color",     CONFIG.markerTextColor);
    if (CONFIG.markerBorderColor)    root.style.setProperty("--marker-border-color",   CONFIG.markerBorderColor);
    // Parking
    if (CONFIG.parkingColor)         root.style.setProperty("--parking-color",         CONFIG.parkingColor);
    if (CONFIG.parkingTextColor)     root.style.setProperty("--parking-text-color",    CONFIG.parkingTextColor);
    if (CONFIG.parkingBorderColor)   root.style.setProperty("--parking-border-color",  CONFIG.parkingBorderColor);
    // Trailheads
    if (CONFIG.trailheadColor)       root.style.setProperty("--trailhead-color",       CONFIG.trailheadColor);
    if (CONFIG.trailheadTextColor)   root.style.setProperty("--trailhead-text-color",  CONFIG.trailheadTextColor);
    if (CONFIG.trailheadBorderColor) root.style.setProperty("--trailhead-border-color", CONFIG.trailheadBorderColor);
    // Features (inner dot + outer ring)
    if (CONFIG.featureColor)         root.style.setProperty("--feature-color",         CONFIG.featureColor);
    if (CONFIG.featureRingColor)     root.style.setProperty("--feature-ring-color",    CONFIG.featureRingColor);
})();

// ============================================================
// localStorage helpers — UI state persists per-map under
// `<slug>.mtb.*` keys. localStorage is scoped by origin, not by
// path, so without the slug prefix every map served from the same
// domain (e.g. trailmaps.app/bloomer, trailmaps.app/dte) would
// share state — toggling winter on one would flip the other on
// next visit. Prefixing with CONFIG.slug isolates each map's UI
// prefs. All values are purely-functional UI prefs (not personal
// data); falls under the ePrivacy "strictly necessary" exemption —
// no consent banner required. Unprefixed keys: mtb.seasonMode,
// mtb.emergencyOn, mtb.poi.markers (merged guidepost + emergency
// trail-marker layer), mtb.poi.parking, mtb.poi.trailheads,
// mtb.poi.features, mtb.labels, mtb.difficulty.
// ============================================================
const LS_PREFIX = (CONFIG && CONFIG.slug ? CONFIG.slug + "." : "");
const LS = {
    get(key, fallback) {
        try {
            const v = window.localStorage.getItem(LS_PREFIX + key);
            if (v === null) return fallback;
            return JSON.parse(v);
        } catch (_) { return fallback; }
    },
    set(key, value) {
        try { window.localStorage.setItem(LS_PREFIX + key, JSON.stringify(value)); }
        catch (_) { /* private mode / quota */ }
    },
};

// Sort comparator for route ids — handles numeric OSM ids and string
// custom ids consistently. With {numeric:true}, "2" sorts before "10",
// and string ids sort lexicographically among themselves.
const ROUTE_ID_COMPARE = (a, b) =>
    String(a).localeCompare(String(b), undefined, { numeric: true });

// ============================================================
// Constants
// ============================================================
// Zoom-interpolated offset expression — multiplies offset_index (from
// computeOffsetsAndFilter) by a zoom-scaled step that exceeds casing
// width to avoid overlap.
function makeOffsetExpr() {
    // Step slightly exceeds fill width (not casing width) so adjacent
    // fills have a ~1px gap. Semi-transparent casings overlap between
    // adjacent trails, forming a clean dark border.
    // Fill widths: z10=2, z14=4, z18=7  →  steps: 3, 5, 8
    return [
        "interpolate", ["linear"], ["zoom"],
        10, ["*", ["get", "offset_index"], 3],
        14, ["*", ["get", "offset_index"], 5],
        18, ["*", ["get", "offset_index"], 8],
    ];
}

// ============================================================
// IMBA Difficulty Icons
// ============================================================
const IMBA_RATINGS = [
    { id: "imba-0", shape: "circle",         color: "#ffffff", border: "#888888" },
    { id: "imba-1", shape: "circle",         color: "#2ecc40", border: "#27ae60" },
    { id: "imba-2", shape: "square",         color: "#0074D9", border: "#005fa3" },
    { id: "imba-3", shape: "diamond",        color: "#111111", border: "#000000" },
    { id: "imba-4", shape: "double-diamond", color: "#111111", border: "#000000" },
    { id: "imba-5", shape: "double-diamond", color: "#FF8C00", border: "#CC7000" },
];

function drawDifficultyShape(ctx, size, rating) {
    const cx = size / 2;
    const cy = size / 2;
    const r = size / 2 - 3;  // margin for outer halo

    // Helper: trace the shape path (reused for halo, fill, and inner border)
    function circlePath() {
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
    }
    function squarePath() {
        const half = r * 0.75;
        ctx.beginPath();
        ctx.rect(cx - half, cy - half, half * 2, half * 2);
    }
    function diamondPath(centerX, centerY, dr) {
        ctx.beginPath();
        ctx.moveTo(centerX, centerY - dr);
        ctx.lineTo(centerX + dr, centerY);
        ctx.lineTo(centerX, centerY + dr);
        ctx.lineTo(centerX - dr, centerY);
        ctx.closePath();
    }

    const tracePath = (centerX, centerY, dr) => {
        if (rating.shape === "circle") circlePath();
        else if (rating.shape === "square") squarePath();
        else if (rating.shape === "diamond") diamondPath(centerX || cx, centerY || cy, dr || r);
        else if (rating.shape === "double-diamond") diamondPath(centerX, centerY, dr);
    };

    if (rating.shape === "double-diamond") {
        const dr = r * 0.5;
        const gap = r * 0.52;
        // Outer white halo
        for (const xOff of [cx - gap, cx + gap]) {
            tracePath(xOff, cy, dr);
            ctx.strokeStyle = "rgba(255,255,255,0.9)";
            ctx.lineWidth = 4;
            ctx.stroke();
        }
        // Fill + inner border
        for (const xOff of [cx - gap, cx + gap]) {
            tracePath(xOff, cy, dr);
            ctx.fillStyle = rating.color;
            ctx.fill();
            tracePath(xOff, cy, dr);
            ctx.strokeStyle = rating.border;
            ctx.lineWidth = 1.5;
            ctx.stroke();
        }
    } else {
        // Outer white halo
        tracePath();
        ctx.strokeStyle = "rgba(255,255,255,0.9)";
        ctx.lineWidth = 4;
        ctx.stroke();

        // Fill
        tracePath();
        ctx.fillStyle = rating.color;
        ctx.fill();

        // Inner border
        tracePath();
        ctx.strokeStyle = rating.border;
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }
}

function registerDifficultyIcons() {
    const size = 24;
    const ratio = 4;  // high-DPI for crisp rendering at all zoom levels

    for (const rating of IMBA_RATINGS) {
        if (map.hasImage(rating.id)) continue;
        const canvas = document.createElement("canvas");
        canvas.width = size * ratio;
        canvas.height = size * ratio;
        const ctx = canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        drawDifficultyShape(ctx, size, rating);
        map.addImage(rating.id, {
            width: size * ratio,
            height: size * ratio,
            data: ctx.getImageData(0, 0, size * ratio, size * ratio).data,
        }, { pixelRatio: ratio });
    }
}

function difficultyToggleOn() {
    return LS.get("mtb.difficulty", true) === true;
}

// ============================================================
// Direction arrows (one-way trails, optional day-of-week alternation)
// ============================================================
const WEEKDAY_NAMES = ["sunday", "monday", "tuesday", "wednesday",
                      "thursday", "friday", "saturday"];

// Bold chevron icon, drawn in canvas like the IMBA difficulty icons so we
// control colour, weight, and halo. Light theme only — black arrow with a
// light halo always reads against the casings (which are also black/dark).
//
// The arrow points RIGHT (positive-X). `symbol-placement: line` with
// `icon-rotation-alignment: map` aligns the icon's positive-X axis with the
// line's direction of travel, so an icon drawn pointing right ends up
// pointing along the line; 0° = forward along digitisation, 180° =
// reversed (alternating days).
function drawArrow(ctx, size, fillColor, haloColor) {
    const cy    = size / 2;
    const tip   = size * 0.94;   // tip x
    const back  = size * 0.14;   // back-edge x (tail corners)
    const notch = size * 0.32;   // notch x — close to the back so the head
                                 // reads as a chunky triangle rather than a
                                 // thin > chevron.
    const half  = size * 0.42;   // half-height at the back corners

    function tracePath() {
        ctx.beginPath();
        ctx.moveTo(tip, cy);             // tip
        ctx.lineTo(back, cy - half);     // top-back corner
        ctx.lineTo(notch, cy);           // shallow back notch
        ctx.lineTo(back, cy + half);     // bottom-back corner
        ctx.closePath();
    }

    tracePath();
    ctx.strokeStyle = haloColor;
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.stroke();

    tracePath();
    ctx.fillStyle = fillColor;
    ctx.fill();
}

const ARROW_ICON_ID = "arrow-dark";
const ARROW_FILL = "#000000";
const ARROW_HALO = "rgba(255,255,255,0.9)";

function registerArrowIcons() {
    const size = 22;
    const ratio = 4;
    if (map.hasImage(ARROW_ICON_ID)) return;
    const canvas = document.createElement("canvas");
    canvas.width = size * ratio;
    canvas.height = size * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    drawArrow(ctx, size, ARROW_FILL, ARROW_HALO);
    map.addImage(ARROW_ICON_ID, {
        width: size * ratio,
        height: size * ratio,
        data: ctx.getImageData(0, 0, size * ratio, size * ratio).data,
    }, { pixelRatio: ratio });
}

function todaysReverseRoutes() {
    const now = new Date();
    const weekday = WEEKDAY_NAMES[now.getDay()];
    // Parity token tracks calendar day-of-month parity. Simple getDate()%2 —
    // month boundaries (e.g. Mar 31 → Apr 1) can yield two odd days in a
    // row; semantics are "reverse when today's date is even/odd," not
    // "strictly alternate."
    const parityToken = (now.getDate() % 2 === 0) ? "even_days" : "odd_days";
    const out = new Set();
    const sched = CONFIG.directionSchedules || {};
    for (const [relId, spec] of Object.entries(sched)) {
        const list = spec.reverse_days || [];
        if (list.includes(weekday) || list.includes(parityToken)) {
            out.add(String(relId));
        }
    }
    return out;
}

// Recomputed on day-tick (see setInterval in setupBottomSheet); each
// arrow's rotation is baked into its feature properties at decoration
// build time, so a change here forces a full updateDecorationsSource().
let reverseRoutesToday = todaysReverseRoutes();

// ============================================================
// Decoration system — pre-deconflicted Point features that replace
// the old per-layer `symbol-placement: line` setup. MapLibre places
// line symbols PER TILE: the same feature gets re-anchored inside
// each tile bucket independently, so cross-layer alignment (arrows
// over diamonds over labels) breaks at tile seams regardless of
// `symbol-spacing` math. Pre-computing the decoration positions in
// JS, then rendering them as point features with `icon-allow-overlap:
// true`, sidesteps the entire collision pipeline — the layout we
// compute is exactly what the user sees.
//
// One source (`trail-decorations`), four kinds (trail_name,
// route_name, diamond, arrow), each with `min_zoom` for tiered
// density. Layer filter: `kind == X && min_zoom <= zoom`.
// ============================================================

// String enums for decoration kinds (the `kind` property on every
// trail-decorations feature) and POI types (the `poi_type` property
// on every pois.geojson feature). Centralised so a typo anywhere
// produces a missing-property reference at evaluation time instead
// of silently filtering nothing. The values are the literal strings
// MapLibre filters and the build-time POI emitter compare against —
// if you change a value here you must also update fetch_pois.py.
const KIND = Object.freeze({
    TRAIL_NAME: "trail_name",
    ROUTE_NAME: "route_name",
    DIAMOND:    "diamond",
    ARROW:      "arrow",
});
const POI = Object.freeze({
    TRAIL_MARKER: "trail_marker",
    PARKING:      "parking",
    TRAILHEAD:    "trailhead",
    FEATURE:      "feature",
});

const EARTH_RADIUS_M = 6378137;

function haversineMeters(lng1, lat1, lng2, lat2) {
    const φ1 = lat1 * Math.PI / 180;
    const φ2 = lat2 * Math.PI / 180;
    const dφ = (lat2 - lat1) * Math.PI / 180;
    const dλ = (lng2 - lng1) * Math.PI / 180;
    const a = Math.sin(dφ / 2) ** 2
            + Math.cos(φ1) * Math.cos(φ2) * Math.sin(dλ / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

// Initial bearing from (lng1,lat1) toward (lng2,lat2), degrees CW from north.
function bearingDeg(lng1, lat1, lng2, lat2) {
    const φ1 = lat1 * Math.PI / 180;
    const φ2 = lat2 * Math.PI / 180;
    const dλ = (lng2 - lng1) * Math.PI / 180;
    const y = Math.sin(dλ) * Math.cos(φ2);
    const x = Math.cos(φ1) * Math.sin(φ2)
            - Math.sin(φ1) * Math.cos(φ2) * Math.cos(dλ);
    return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

// Map a trail bearing to the rotation the east-pointing arrow icon
// needs so it points along the line (and adds 180° on reverse-day
// schedules).
function arrowRotateForBearing(bearing, reverse) {
    const b = reverse ? bearing + 180 : bearing;
    return ((b - 90) % 360 + 360) % 360;
}

// Decompose a polyline into segments with bearings + cumulative arc
// length. Returns { segments: [{lng1,lat1,lng2,lat2,lengthM,bearing,
// arcStart}], totalLength: number }. Zero-length segments are
// dropped; the resulting `arcStart` reflects only the kept segments.
function computeWaySegments(coords) {
    const segments = [];
    let arcStart = 0;
    for (let i = 0; i < coords.length - 1; i++) {
        const [lng1, lat1] = coords[i];
        const [lng2, lat2] = coords[i + 1];
        const lengthM = haversineMeters(lng1, lat1, lng2, lat2);
        if (lengthM === 0) continue;
        segments.push({
            lng1, lat1, lng2, lat2, lengthM,
            bearing: bearingDeg(lng1, lat1, lng2, lat2),
            arcStart,
        });
        arcStart += lengthM;
    }
    return { segments, totalLength: arcStart };
}

// Linear interpolation along the polyline at arc distance `arc`.
// Returns {lng, lat, bearing} or null if `arc` is out of range.
function pointAtArcLength(segments, totalLength, arc) {
    if (arc < 0 || arc > totalLength || segments.length === 0) return null;
    for (const seg of segments) {
        if (arc <= seg.arcStart + seg.lengthM) {
            const t = seg.lengthM === 0 ? 0
                : (arc - seg.arcStart) / seg.lengthM;
            return {
                lng: seg.lng1 + t * (seg.lng2 - seg.lng1),
                lat: seg.lat1 + t * (seg.lat2 - seg.lat1),
                bearing: seg.bearing,
            };
        }
    }
    const last = segments[segments.length - 1];
    return { lng: last.lng2, lat: last.lat2, bearing: last.bearing };
}

// Footprint radii (meters) used for placement collision suppression.
// Calibrated to zoom 15 (~3.5 m/px at lat 42°) — at higher zooms the
// icons shrink in metric terms; at lower zooms they grow but the
// tiered min_zoom keeps fewer items on screen, so a static radius
// works across the visible range.
const DECOR_RADIUS_M = {
    label:    60,  // text along trail axis (legacy point placement)
    diamond:  35,
    arrow:    30,
    obstacle: 30,  // POI / parking / trailhead / feature markers
    // Min distance from a line-placed label's support polyline to a
    // DOM marker center. Sized to keep the rendered glyph row clear
    // of the marker icon at typical viewing zoom (14-15). DOM markers
    // are invisible to MapLibre's WebGL collision pipeline, so we
    // chop the label support line in JS instead.
    label_line_clearance: 50,
};

// Snapshot the on-map POI markers so the decoration placer skips
// their footprint. Markers are DOM overlays (above the WebGL canvas),
// not WebGL features, so MapLibre's collision can't see them — we
// have to pre-exclude. `_map` check skips markers detached by the
// proximity filter (they're hidden right now).
//
// Cached at module scope: a single user gesture (season toggle,
// emergency mode flip) often triggers several updateDecorationsSource
// calls in a row, all asking for the same obstacle set. invalidate-
// ObstaclesCache() must be called at every site that adds/removes a
// marker (initial creation, updateMarkerProximity, peek-toggle
// handlers) — otherwise stale obstacles will let labels/arrows
// render through markers that have just appeared/disappeared.
let _obstaclesCache = null;
function invalidateObstaclesCache() {
    _obstaclesCache = null;
}

function gatherObstacles() {
    if (_obstaclesCache !== null) return _obstaclesCache;
    const out = [];
    const r = DECOR_RADIUS_M.obstacle;
    for (const arr of [trailMarkerMarkers, parkingMarkers,
                       trailheadMarkers, featureMarkers]) {
        for (const m of arr) {
            if (m._map !== map) continue;
            const ll = m.getLngLat();
            out.push({ lngLat: [ll.lng, ll.lat], radiusM: r });
        }
    }
    _obstaclesCache = out;
    return out;
}

function placedCollides(lng, lat, radiusM, placed) {
    for (const p of placed) {
        const d = haversineMeters(lng, lat, p.lngLat[0], p.lngLat[1]);
        if (d < radiusM + p.radiusM) return true;
    }
    return false;
}

// Split a way's coords into safe sub-LineStrings that stay at least
// `radius` meters from every obstacle's center. Used to keep curve-
// following labels (symbol-placement: line) from rendering through
// DOM marker icons, which are outside MapLibre's WebGL collision
// pipeline. A coord vertex is "blocked" if it sits inside any
// obstacle's exclusion disc; runs of consecutive non-blocked vertices
// (length >= 2) become individual LineStrings. We don't bother
// interpolating new vertices at the disc boundary — vertex spacing on
// trail data is already fine enough that the small over/under-clip is
// invisible at viewing zooms.
function clipCoordsAroundObstacles(coords, obstacles, radius) {
    if (!obstacles.length || coords.length < 2) return [coords];
    const runs = [];
    let runStart = null;
    for (let i = 0; i < coords.length; i++) {
        const [lng, lat] = coords[i];
        let blocked = false;
        for (const obs of obstacles) {
            if (haversineMeters(lng, lat, obs.lngLat[0], obs.lngLat[1])
                < radius) {
                blocked = true;
                break;
            }
        }
        if (!blocked) {
            if (runStart === null) runStart = i;
        } else if (runStart !== null) {
            if (i - runStart >= 2) runs.push(coords.slice(runStart, i));
            runStart = null;
        }
    }
    if (runStart !== null && coords.length - runStart >= 2) {
        runs.push(coords.slice(runStart));
    }
    return runs;
}

// Canonical-owner ways = one entry per physical way that has at
// least one currently-visible route, owned by the lowest-ranked
// visible route_id (so siblings on shared ways collapse to one).
// The geometry is kept as a segment array (with bearings + cumulative
// arc lengths) so the placer can drop decorations at exact arc
// distances along the way.
function collectCanonicalWays() {
    if (!routesData) return [];

    const allRouteIds = Object.keys(CONFIG.routes).slice().sort(ROUTE_ID_COMPARE);
    const globalRank = new Map();
    allRouteIds.forEach((id, i) => globalRank.set(id, i));

    const ways = [];
    for (const f of routesData.features) {
        const props = f.properties;
        const routeId = props.route_id;
        const shared = props.shared_routes || [routeId];
        const visibleShared = shared
            .filter((id) => visibleRoutes.has(id))
            .sort((a, b) => globalRank.get(a) - globalRank.get(b));
        if (visibleShared.length === 0) continue;
        if (visibleShared[0] !== routeId) continue;

        const coords = f.geometry.type === "LineString"
            ? f.geometry.coordinates
            : f.geometry.coordinates.flat();
        const { segments, totalLength } = computeWaySegments(coords);
        if (segments.length === 0) continue;

        const soloRouteName = visibleShared.length === 1
            ? (props.route_name || "") : "";
        const soloRouteId = visibleShared.length === 1 ? routeId : "";

        ways.push({
            segments,
            totalLength,
            coords,
            trailName: props.trail_name || "",
            imba: props.imba_difficulty || "",
            oneway: props.oneway || "",
            routeId,
            sharedRoutes: shared,
            soloRouteName,
            soloRouteId,
        });
    }
    return ways;
}

// Try to place a decoration of `kind` at the first candidate arc
// position that clears all already-placed items. extraPropsFn
// receives the chosen point (with .bearing) and returns the
// kind-specific properties to merge into the feature.
function tryPlaceDecoration(way, candidateArcs, kind, minZoom,
                            decorations, placed, extraPropsFn) {
    const radius = (kind === KIND.DIAMOND) ? DECOR_RADIUS_M.diamond
                 : (kind === KIND.ARROW)   ? DECOR_RADIUS_M.arrow
                 :                           DECOR_RADIUS_M.label;
    for (const arc of candidateArcs) {
        if (arc < 0 || arc > way.totalLength) continue;
        const pt = pointAtArcLength(way.segments, way.totalLength, arc);
        if (!pt) continue;
        if (placedCollides(pt.lng, pt.lat, radius, placed)) continue;
        const extra = extraPropsFn ? extraPropsFn(pt) : {};
        decorations.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: [pt.lng, pt.lat] },
            properties: {
                kind,
                min_zoom: minZoom,
                ...extra,
            },
        });
        placed.push({ lngLat: [pt.lng, pt.lat], radiusM: radius });
        return true;
    }
    return false;
}

// Build the full decoration FeatureCollection for the current visible
// route set. Order matters:
//   Pass 1 — labels (LineString features rendered via symbol-placement:
//            line so text follows the trail curve; not pre-deconflicted)
//   Pass 2 — tier-0 mandatory icons (2 diamonds + 2 arrows per
//            applicable way, ensures even short trails get markings)
//   Pass 3 — tier-1 (zoom>=13) icons every 400m
//   Pass 4 — tier-2 (zoom>=15) icons every 200m (fills tier-1 gaps)
// Each icon-tier's candidates are deconflicted against everything
// already placed, including obstacle markers gathered up front.
function computeDecorations() {
    const decorations = [];
    const placed = gatherObstacles();
    const ways = collectCanonicalWays();

    // Process longer ways first so their labels claim space before
    // shorter ways' labels — short trails are more likely to drop
    // their label, which is the desired tradeoff.
    ways.sort((a, b) => b.totalLength - a.totalLength);

    const reverseSet = reverseRoutesToday;

    // ---- Pass 1: labels (one or more LineString runs per way;
    //      MapLibre's symbol-placement:line auto-curves text along the
    //      trail). The way coords are clipped around DOM markers so
    //      labels never cross a marker icon. Diamonds and arrows
    //      placed in later passes don't need JS clipping — they're
    //      WebGL features and the label layer's text-allow-overlap:
    //      false + the icon layers' icon-ignore-placement: false make
    //      MapLibre's per-tile collision drop overlapping labels
    //      automatically. ----
    for (const way of ways) {
        const runs = clipCoordsAroundObstacles(way.coords, placed,
            DECOR_RADIUS_M.label_line_clearance);
        if (way.trailName) {
            for (const run of runs) {
                decorations.push({
                    type: "Feature",
                    geometry: { type: "LineString", coordinates: run },
                    properties: {
                        kind: KIND.TRAIL_NAME,
                        min_zoom: 0,
                        text: way.trailName,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    },
                });
            }
        }
        if (way.soloRouteName) {
            for (const run of runs) {
                decorations.push({
                    type: "Feature",
                    geometry: { type: "LineString", coordinates: run },
                    properties: {
                        kind: KIND.ROUTE_NAME,
                        min_zoom: 0,
                        text: way.soloRouteName,
                        solo_route_id: way.soloRouteId,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    },
                });
            }
        }
    }

    // ---- Pass 2: tier-0 mandatory decor — 2 diamonds + 2 arrows per
    //      applicable way so even short trails get clear markings. ----
    for (const way of ways) {
        const L = way.totalLength;
        const hasDiamond = ["0", "1", "2", "3", "4", "5"].includes(way.imba);
        const hasArrow = way.oneway === "yes" || way.oneway === "reversible";
        const reverse = reverseSet.has(way.routeId)
            || way.sharedRoutes.some((id) => reverseSet.has(id));

        if (hasDiamond) {
            // Two anchors: ~quarter and ~three-quarter, each with
            // local fallbacks if the primary slot collides.
            const anchors = [
                [L * 0.25, L * 0.15, L * 0.35, L * 0.10],
                [L * 0.75, L * 0.85, L * 0.65, L * 0.90],
            ];
            for (const cand of anchors) {
                tryPlaceDecoration(way, cand, KIND.DIAMOND, 0,
                    decorations, placed, () => ({
                        imba_difficulty: way.imba,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
        if (hasArrow) {
            const anchors = [
                [L * 0.50, L * 0.40, L * 0.60, L * 0.45],
                [L * 0.85, L * 0.80, L * 0.90, L * 0.75],
            ];
            for (const cand of anchors) {
                tryPlaceDecoration(way, cand, KIND.ARROW, 0,
                    decorations, placed, (pt) => ({
                        rotation: arrowRotateForBearing(pt.bearing, reverse),
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
    }

    // ---- Pass 3: tier-1 (zoom>=13) — diamonds every 400m, arrows
    //      phase-shifted by 200m. The tier-0 anchors at L*0.25/0.75
    //      establish the pattern; this sweep fills the gaps. ----
    for (const way of ways) {
        const L = way.totalLength;
        const hasDiamond = ["0", "1", "2", "3", "4", "5"].includes(way.imba);
        const hasArrow = way.oneway === "yes" || way.oneway === "reversible";
        const reverse = reverseSet.has(way.routeId)
            || way.sharedRoutes.some((id) => reverseSet.has(id));

        if (hasDiamond) {
            for (let arc = 400; arc < L; arc += 400) {
                tryPlaceDecoration(way, [arc], KIND.DIAMOND, 13,
                    decorations, placed, () => ({
                        imba_difficulty: way.imba,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
        if (hasArrow) {
            for (let arc = 200; arc < L; arc += 400) {
                tryPlaceDecoration(way, [arc], KIND.ARROW, 13,
                    decorations, placed, (pt) => ({
                        rotation: arrowRotateForBearing(pt.bearing, reverse),
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
    }

    // ---- Pass 4: tier-2 (zoom>=15) — fill in at half the tier-1
    //      spacing so close-in views get a denser pattern. Most arcs
    //      coincide with tier-1 placements and get rejected by the
    //      collision check; the remaining ones land in the gaps. ----
    for (const way of ways) {
        const L = way.totalLength;
        const hasDiamond = ["0", "1", "2", "3", "4", "5"].includes(way.imba);
        const hasArrow = way.oneway === "yes" || way.oneway === "reversible";
        const reverse = reverseSet.has(way.routeId)
            || way.sharedRoutes.some((id) => reverseSet.has(id));

        if (hasDiamond) {
            for (let arc = 200; arc < L; arc += 200) {
                tryPlaceDecoration(way, [arc], KIND.DIAMOND, 15,
                    decorations, placed, () => ({
                        imba_difficulty: way.imba,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
        if (hasArrow) {
            for (let arc = 100; arc < L; arc += 200) {
                tryPlaceDecoration(way, [arc], KIND.ARROW, 15,
                    decorations, placed, (pt) => ({
                        rotation: arrowRotateForBearing(pt.bearing, reverse),
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
    }

    return { type: "FeatureCollection", features: decorations };
}

// Refresh the trail-decorations source. Cheap to recompute (~ms for
// most maps); called on initial load, route-visibility change, marker
// proximity change, and day-tick (when reverseRoutesToday flips).
function updateDecorationsSource() {
    const src = map.getSource("trail-decorations");
    if (src) src.setData(computeDecorations());
}

// Add the four decor layers — kind-filtered with a tier gate
// (`min_zoom <= zoom`) so density grows with zoom.
//
// Icons (diamond, arrow) use `icon-allow-overlap: true` because their
// positions were already deconflicted in computeDecorations and we
// want exactly the placements we chose. They use `icon-ignore-
// placement: false` so they DO register in MapLibre's collision index
// — which lets the label layers see them and shift labels out of the
// way. Net: icons sit where JS put them; labels avoid icons.
//
// Labels (trail_name, route_name) use the default `text-allow-overlap:
// false` so they curve along the trail (symbol-placement: line) and
// MapLibre's per-tile collision drops labels that would overlap each
// other or any registered icon. The label LineStrings are pre-clipped
// in computeDecorations to skip past DOM markers (which live outside
// the WebGL collision pipeline).
function addDecorationLayers() {
    if (map.getLayer("decor-trail-name")) return;

    map.addLayer({
        id: "decor-trail-name",
        type: "symbol",
        source: "trail-decorations",
        filter: ["all",
            ["==", ["get", "kind"], KIND.TRAIL_NAME],
            ["<=", ["get", "min_zoom"], ["zoom"]],
        ],
        layout: {
            "symbol-placement": "line",
            "text-field": ["get", "text"],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["interpolate", ["linear"], ["zoom"],
                10, 10, 14, 13, 18, 16],
            "text-max-angle": 45,
            "text-padding": 3,
            "symbol-spacing": 250,
            "text-optional": true,
            "visibility": labelMode === "trails" ? "visible" : "none",
        },
        paint: {
            "text-color": "#1a1a1a",
            "text-halo-color": "rgba(255,255,255,0.9)",
            "text-halo-width": 2,
        },
    });

    map.addLayer({
        id: "decor-route-name",
        type: "symbol",
        source: "trail-decorations",
        filter: ["all",
            ["==", ["get", "kind"], KIND.ROUTE_NAME],
            ["<=", ["get", "min_zoom"], ["zoom"]],
        ],
        layout: {
            "symbol-placement": "line",
            "text-field": ["get", "text"],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["interpolate", ["linear"], ["zoom"],
                10, 10, 14, 13, 18, 16],
            "text-max-angle": 45,
            "text-padding": 3,
            "symbol-spacing": 250,
            "text-optional": true,
            "visibility": labelMode === "routes" ? "visible" : "none",
        },
        paint: {
            "text-color": "#1a1a1a",
            "text-halo-color": "rgba(255,255,255,0.9)",
            "text-halo-width": 2,
        },
    });

    if (CONFIG.showDifficulty) {
        map.addLayer({
            id: "decor-diamond",
            type: "symbol",
            source: "trail-decorations",
            filter: ["all",
                ["==", ["get", "kind"], KIND.DIAMOND],
                ["<=", ["get", "min_zoom"], ["zoom"]],
            ],
            layout: {
                "symbol-placement": "point",
                "icon-image": [
                    "match", ["get", "imba_difficulty"],
                    "0", "imba-0",
                    "1", "imba-1",
                    "2", "imba-2",
                    "3", "imba-3",
                    "4", "imba-4",
                    "5", "imba-5",
                    "",
                ],
                "icon-size": ["interpolate", ["linear"], ["zoom"],
                    12, 0.5, 14, 0.7, 18, 1.0],
                "icon-rotation-alignment": "viewport",
                // Always draw, even if a label tries to occupy the same
                // pixels (we deconflicted the diamonds in JS so they're
                // exactly where we want them).
                "icon-allow-overlap": true,
                // BUT do register the diamond's footprint in the
                // collision index, so labels (text-allow-overlap:false)
                // shift along their support line to dodge it.
                "icon-ignore-placement": false,
                "visibility": difficultyToggleOn() ? "visible" : "none",
            },
        });
    }

    map.addLayer({
        id: "decor-arrow",
        type: "symbol",
        source: "trail-decorations",
        filter: ["all",
            ["==", ["get", "kind"], KIND.ARROW],
            ["<=", ["get", "min_zoom"], ["zoom"]],
        ],
        layout: {
            "symbol-placement": "point",
            "icon-image": ARROW_ICON_ID,
            "icon-size": ["interpolate", ["linear"], ["zoom"],
                12, 0.5, 14, 0.7, 18, 1.0],
            "icon-rotate": ["get", "rotation"],
            "icon-rotation-alignment": "map",
            "icon-allow-overlap": true,
            // Register in the collision index so labels avoid us; see
            // the matching comment on decor-diamond above.
            "icon-ignore-placement": false,
        },
    });
}

// Build kind-filter + (optionally) highlight-filter for a decor layer.
// The kind/min_zoom gate is non-negotiable; under spotlight dim we AND
// in a shared_routes / trail_name match so non-highlighted decor goes
// dark with the rest of the map.
function buildDecorFilter(kind) {
    const base = ["all",
        ["==", ["get", "kind"], kind],
        ["<=", ["get", "min_zoom"], ["zoom"]],
    ];
    if (!highlightDimActive()) return base;
    if (highlight.kind === "route") {
        return ["all", ...base.slice(1),
            ["in", highlight.key, ["get", "shared_routes"]]];
    }
    return ["all", ...base.slice(1),
        ["==", ["get", "trail_name"], highlight.key]];
}

// Refresh the diamond + arrow filters in response to highlight state
// changes. Label visibility (and route_name's solo-route filter) is
// handled in updateLabels(); see that function for the additional
// dim-aware logic.
function updateDecorationsHighlight() {
    if (map.getLayer("decor-diamond")) {
        map.setFilter("decor-diamond", buildDecorFilter(KIND.DIAMOND));
    }
    if (map.getLayer("decor-arrow")) {
        map.setFilter("decor-arrow", buildDecorFilter(KIND.ARROW));
    }
}

// ============================================================
// State
// ============================================================
let map;
let routesData = null; // original GeoJSON (never mutated)
let poisData = null;
// Continuation-arrow source data for clipped relations. Held at module
// scope so recomputeClipEndpointVisibility() can mutate per-feature
// `visible_count` and push the update back through setData().
let clipEndpointsData = null;
let visibleRoutes = new Set(); // route IDs currently shown (strings)
let basemapMode = "default"; // "default" or "custom:<id>"
let labelMode = LS.get("mtb.labels", CONFIG.defaultLabels || "routes");

// Bucket-model state
let seasonMode = LS.get("mtb.seasonMode", "summer"); // "summer" | "winter"
let emergencyOn = LS.get("mtb.emergencyOn", false);

// Single-highlight invariant: at most one route OR trail highlighted.
let highlight = null; // { kind: "route"|"trail", key: string } | null

// Indexes derived once at startup
let routeIndex = []; // [{ id, name, color, summer, winter, emergency, isCustom, distanceM, elevationGainM, elevationLossM }]
let trailIndex = []; // [{ name, routeIds: [string] }]

// Marker arrays — trailMarkerMarkers covers the merged guidepost +
// emergency-access-point category (single "Markers" peek button).
let trailMarkerMarkers = [];
let parkingMarkers = [];
let trailheadMarkers = [];
let featureMarkers = [];
let userLocation = null; // [lng, lat] from geolocate control
// MapLibre GeolocateControl handle; assigned in init(). Hoisted to module
// scope so the off-screen indicator's click handler (defined at module
// scope in updateLocationIndicator) can resume tracking via .trigger()
// when the user taps it.
let geolocateControl = null;
// GeolocateControl + off-screen indicator state: see the
// state-machine docblock above the GeolocateControl creation in
// init() for the full story (state diagram, transition table, what
// each flag is for, and where each gets set vs consumed).
let _showToastOnNextFix = false;
let _suppressOffScreenToast = false;
let _firstGeolocateMoveAfterTrigger = false;
let _followUserOnGeolocate = false;

// ============================================================
// Initialization
// ============================================================
// Required CONFIG fields, checked at boot. If the build produces a
// CONFIG missing any of these (e.g. the template substitution broke,
// or a required YAML key was removed without updating CONFIG_SPEC),
// the app refuses to start with a clear error in the console + a
// visible message on the map div, instead of failing later with an
// opaque `Cannot read property of undefined` somewhere downstream.
const REQUIRED_CONFIG_KEYS = [
    "name", "slug", "title",
    "bbox", "panBbox", "center",
    "minZoom", "maxZoom",
    "routes",
];

function validateConfigShape() {
    if (typeof CONFIG !== "object" || CONFIG === null) {
        throw new Error("CONFIG is missing or not an object — check that the build's template substitution succeeded.");
    }
    const missing = REQUIRED_CONFIG_KEYS.filter((k) =>
        CONFIG[k] === undefined || CONFIG[k] === null);
    if (missing.length > 0) {
        throw new Error(
            `CONFIG missing required key(s): ${missing.join(", ")}. ` +
            `Re-run the build (scripts/build.py) to regenerate.`);
    }
    // Bbox arrays must have 4 numbers; an off-by-one here breaks
    // maxBounds and fitBoundsOptions in opaque ways downstream.
    for (const k of ["bbox", "panBbox"]) {
        if (!Array.isArray(CONFIG[k]) || CONFIG[k].length !== 4
            || !CONFIG[k].every((n) => typeof n === "number")) {
            throw new Error(
                `CONFIG.${k} must be a 4-number array [w, s, e, n]; ` +
                `got ${JSON.stringify(CONFIG[k])}.`);
        }
    }
}

// Module-scope holding pen for a parsed share-link state. Set by
// consumeShareHash() before map construction; consumed by the post-
// trails-load handler to apply the highlight (and discarded after).
let _pendingShareHighlight = null;

// Apply a highlight that was parsed from an incoming share link.
// Called once, after trails + indexes are loaded. The view portion
// (zoom / center) of the share link is already in effect via map
// construction options. Best-effort: silently no-ops if the
// referenced route or trail no longer exists in the data (so a stale
// link doesn't render an error).
function applyPendingShareHighlight() {
    const h = _pendingShareHighlight;
    _pendingShareHighlight = null;
    if (!h || !h.kind || !h.key) return;
    if (h.kind === "route") {
        // h.key is the OSM relation ID (or custom-route ID), matching
        // the keys of CONFIG.routes. Verify before calling.
        if (CONFIG.routes && CONFIG.routes[h.key]) {
            highlightRoute(h.key);
        }
    } else if (h.kind === "trail") {
        // h.key is the trail name as-stored on each feature's
        // trail_name property. highlightTrail does its own matching.
        // Sanity-check that at least one feature carries the name so
        // we don't surface an empty highlight.
        if (trailIndex.some((t) => t.name === h.key)) {
            highlightTrail(h.key);
        }
    }
}

// Build the "#share=..." URL for the current map view + active
// highlight. Used by the Share button. Format mirrors what
// consumeShareHash() parses on the receiving side. Coordinates
// rounded to 5 decimals (~1.1 m precision; well under what the user
// can perceive on the map and short enough to keep the URL compact).
function buildShareUrl() {
    const c = map.getCenter();
    const zoom = map.getZoom().toFixed(2).replace(/\.?0+$/, "");
    const lat = c.lat.toFixed(5);
    const lon = c.lng.toFixed(5);
    let path = `share=${zoom}/${lat}/${lon}`;
    if (highlight && highlight.kind === "route" && highlight.key) {
        path += `/r/${encodeURIComponent(highlight.key)}`;
    } else if (highlight && highlight.kind === "trail" && highlight.key) {
        path += `/t/${encodeURIComponent(highlight.key)}`;
    }
    const url = new URL(window.location.href);
    url.hash = path;
    return url.toString();
}

// Build the human-readable share-sheet title — appears as email
// subject in Mail-style apps and as the share-sheet header in iOS
// /Android UIs. Most messaging apps actually rely on OG tags fetched
// from the URL itself for their preview cards, so this is mostly a
// helpful default for email.
function buildShareTitle() {
    const baseTitle = CONFIG.title || CONFIG.name || "Trail Map";
    if (highlight && highlight.kind === "route" && highlight.key
            && CONFIG.routes && CONFIG.routes[highlight.key]) {
        return `${baseTitle} — ${CONFIG.routes[highlight.key].name}`;
    }
    if (highlight && highlight.kind === "trail" && highlight.key) {
        return `${baseTitle} — ${highlight.key}`;
    }
    return baseTitle;
}

// Share-button click handler. Tries Web Share API first (native
// share sheet on mobile, gives the user real choice — Messages /
// Mail / AirDrop / clipboard / etc.); falls back to clipboard with
// a toast confirmation when Web Share isn't available (most
// desktop browsers) or the user dismissed it.
async function shareCurrentView() {
    const url = buildShareUrl();
    const title = buildShareTitle();
    if (navigator.share) {
        try {
            await navigator.share({ title, url });
            return;
        } catch (e) {
            // AbortError = user dismissed the share sheet without
            // picking a target. That's a deliberate action, not a
            // failure — don't fall through to clipboard (would feel
            // like the app is overruling them).
            if (e && e.name === "AbortError") return;
            // Other errors (e.g. permission denied, NotAllowedError
            // when called outside a user gesture in some browsers):
            // fall through to clipboard so the user still gets a
            // working path.
        }
    }
    fallbackCopyShareUrl(url);
}

function fallbackCopyShareUrl(url) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url)
            .then(() => showToast("View URL copied — paste anywhere to share."))
            .catch(() => showToast("Couldn't copy URL — try again or use the URL bar."));
    } else {
        // Very old browser path. Use the legacy execCommand fallback
        // via a hidden textarea (works in IE / pre-clipboard-API
        // contexts). Rarely hit in practice.
        try {
            const ta = document.createElement("textarea");
            ta.value = url;
            ta.setAttribute("readonly", "");
            ta.style.position = "absolute";
            ta.style.left = "-9999px";
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
            showToast("View URL copied — paste anywhere to share.");
        } catch (e) {
            showToast("Couldn't copy URL — try again or use the URL bar.");
        }
    }
}

// Reveal + wire up the Share button. Called from setupBottomSheet
// during boot. Does nothing when share_button: false at build time
// (the section is stripped from index.html, button doesn't exist).
function setupShareButton() {
    const section = document.getElementById("sheet-share-section");
    const btn = document.getElementById("share-btn");
    if (!section || !btn) return;
    section.classList.remove("hidden");
    btn.addEventListener("click", shareCurrentView);
}

// Parse a "#share=zoom/lat/lon[/r/<routeId>|/t/<trailName>]" hash and
// return {center: [lon, lat], zoom, highlight: {kind, key} | null} or
// null if no share hash is present / parseable. Side effect: strips
// the hash from the URL via history.replaceState so that
//   (a) the share-link doesn't persist in the address bar,
//   (b) MapLibre's own hash machinery (when CONFIG.urlHash=true)
//       starts fresh from the current state instead of competing
//       with our share format,
//   (c) a refresh doesn't re-trigger the share path with stale data.
//
// Format chosen for: human-readable, URL-encoded for safety,
// distinguishable from MapLibre's "#zoom/lat/lon" so the two
// conventions can coexist.
function consumeShareHash() {
    const raw = (window.location.hash || "").replace(/^#/, "");
    if (!raw.startsWith("share=")) return null;
    const body = raw.slice("share=".length);
    const parts = body.split("/");
    if (parts.length < 3) return null;
    const zoom = parseFloat(parts[0]);
    const lat = parseFloat(parts[1]);
    const lon = parseFloat(parts[2]);
    if (!Number.isFinite(zoom) || !Number.isFinite(lat) || !Number.isFinite(lon)) {
        return null;
    }
    let highlight = null;
    if (parts.length >= 5) {
        const kindCode = parts[3];
        const keyEnc = parts.slice(4).join("/"); // tolerate slashes in names
        const key = decodeURIComponent(keyEnc);
        if (kindCode === "r") highlight = { kind: "route", key };
        else if (kindCode === "t") highlight = { kind: "trail", key };
    }
    // Strip the hash regardless of url_hash setting; MapLibre will
    // start writing fresh hash if urlHash is true.
    try {
        const url = new URL(window.location.href);
        url.hash = "";
        window.history.replaceState(null, "", url.toString());
    } catch (e) {
        // Best-effort; some embedded contexts disallow history mutation.
    }
    return {
        center: [lon, lat],
        zoom,
        highlight,
    };
}

// Fire-and-forget check that the basemap PMTiles server honors HTTP
// Range requests. Logs a console.error on failure with enough detail
// to diagnose the proxy configuration. See call site for rationale.
async function checkPMTilesRangeSupport() {
    const url = "basemap.pmtiles";
    try {
        const resp = await fetch(url, {
            method: "GET",
            headers: { "Range": "bytes=0-1000" },
            // Bypass the browser's HTTP cache — we want the origin's
            // actual response. (The service worker may still intercept
            // and return 206 from its own cache once it's active; that
            // produces a "false negative" on subsequent visits, which
            // is the correct behavior — once the file is cached
            // locally, broken-Range at the origin no longer matters.)
            cache: "no-store",
        });
        const contentLength = parseInt(
            resp.headers.get("Content-Length") || "0", 10);
        const contentRange = resp.headers.get("Content-Range");
        const looksOk = (
            resp.status === 206 &&
            contentRange &&
            contentLength > 0 &&
            contentLength <= 2000  // we asked for 1001 bytes; small slop for off-by-one
        );
        if (!looksOk) {
            console.error(
                "[mtb-map] HTTP Range requests not honored for " + url + ".\n" +
                "First-visit map loads will trigger multiple full-file downloads " +
                "(potentially 10-20× normal traffic; multi-minute load on cellular). " +
                "After the service worker caches the file, this stops mattering — " +
                "but every brand-new visitor pays the cost.\n" +
                "Server returned: status=" + resp.status +
                ", Content-Range=" + contentRange +
                ", Content-Length=" + contentLength +
                " (expected status=206, Content-Range present, Content-Length≤1001).\n" +
                "Fix: configure your reverse proxy to forward the Range header and " +
                "return 206 Partial Content. See README → Troubleshooting → " +
                "\"PMTiles won't load offline\" entry."
            );
        }
    } catch (e) {
        // Network errors have other manifestations; don't double-alert.
    }
}

async function init() {
    try {
        validateConfigShape();
    } catch (e) {
        // Surface the error visibly on the map div so it's not buried
        // in DevTools — operators deploying a broken build will see
        // this immediately.
        console.error(e);
        const container = document.getElementById("map");
        if (container) {
            container.innerHTML =
                `<div style="padding:24px;font-family:system-ui;color:#b00">` +
                `<strong>Map failed to start.</strong><br>` +
                `<code style="white-space:pre-wrap">${e.message}</code></div>`;
        }
        return;
    }

    // Register PMTiles protocol
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    // Background diagnostic: probe the server for HTTP Range support on
    // the basemap PMTiles. PMTiles relies on Range requests to fetch
    // tiny byte slices (header, directory, individual tiles); a broken
    // proxy that strips the Range header and returns 200 with the full
    // body forces the runtime to download the whole archive (50-200MB)
    // for every range request, blowing up first-visit load time.
    //
    // The service worker hides this on subsequent visits (it caches the
    // full file once and synthesizes 206 responses from local data), so
    // by design this check only catches the bad case on FIRST cold
    // visit — which is exactly when it matters. Silent on success;
    // logs a console.error with diagnostic detail on failure. No toast,
    // no UI noise — this is curator-with-DevTools-open territory.
    checkPMTilesRangeSupport();

    // Build map style (light theme only)
    const style = buildStyle();

    // Build transformRequest for base layers that require auth headers
    const headersByDomain = buildHeaderMap();

    // Detect a "#share=..." URL (from the Share button on another
    // session) BEFORE map construction. If present, we use its center
    // /zoom as the initial view and stash any highlight for application
    // after trails load. The share hash is also stripped from the URL
    // here — it's a one-shot view restoration, not ambient state.
    const shareState = consumeShareHash();
    if (shareState && shareState.highlight) {
        _pendingShareHighlight = shareState.highlight;
    }

    // Create map
    //
    // Default: `bounds` uses the tight CONFIG.bbox so the initial
    // view frames the trails snugly. `maxBounds` uses CONFIG.panBbox
    // (built from bbox + pan_padding) so the user has room to pan
    // for context — maxBounds clamps the map CENTER, not the
    // viewport edge, and the basemap/terrain PMTiles are extracted
    // to match panBbox so real tiles fill the full pannable
    // envelope.
    //
    // Share-link override: when a #share=zoom/lat/lon hash was
    // consumed above, we use explicit center+zoom instead of bounds
    // so the recipient lands on the exact view the sharer captured.
    // maxBounds still applies; if the shared view is somehow
    // outside panBbox (shouldn't happen on the same map version)
    // MapLibre will clamp it to the wall.
    const mapOptions = {
        container: "map",
        style: style,
        minZoom: CONFIG.minZoom,
        maxZoom: CONFIG.maxZoom,
        maxBounds: [
            [CONFIG.panBbox[0], CONFIG.panBbox[1]],
            [CONFIG.panBbox[2], CONFIG.panBbox[3]],
        ],
        // Lock orientation: north is always up, no tilt.
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        attributionControl: false,
        // URL hash (#zoom/lat/lon) — makes views shareable and reload-
        // preserved, but leaks last-viewed location via URL / screen-
        // share. Controlled per-map via CONFIG.urlHash; default false
        // (URL stays clean). Opt in per-map by setting `url_hash: true`
        // in the YAML. The Share button (B.3) generates one-shot share
        // links via a separate "#share=..." format that's read on load
        // regardless of urlHash setting and then stripped.
        hash: CONFIG.urlHash,
    };
    if (shareState) {
        mapOptions.center = shareState.center;
        mapOptions.zoom = shareState.zoom;
    } else {
        mapOptions.bounds = [
            [CONFIG.bbox[0], CONFIG.bbox[1]],
            [CONFIG.bbox[2], CONFIG.bbox[3]],
        ];
        mapOptions.fitBoundsOptions = { padding: 50 };
    }

    if (Object.keys(headersByDomain).length > 0) {
        mapOptions.transformRequest = (url) => {
            for (const [domain, headers] of Object.entries(headersByDomain)) {
                if (url.includes(domain)) {
                    return { url, headers };
                }
            }
            return { url };
        };
    }

    map = new maplibregl.Map(mapOptions);

    // Disable two-finger twist rotation on touch devices.
    map.touchZoomRotate.disableRotation();
    map.setBearing(0);
    map.setPitch(0);

    // Controls
    //
    // NavigationControl (zoom +/-) is intentionally not added — pinch,
    // scroll-wheel, and double-tap cover zoom on every platform the
    // app targets, and dropping the button reclaims the top-left
    // corner for a cleaner map. GeolocateControl IS added (hidden via
    // CSS) because we still need its event/state machine; the peek
    // menu's Locate button drives it via geolocate.trigger().
    //
    // ============================================================
    // GeolocateControl + off-screen indicator state machine
    // ============================================================
    // MapLibre's GeolocateControl owns the actual GPS tracking state.
    // We don't replace it — we hide its DOM control via CSS, drive
    // it through .trigger(), and modify its camera behaviour.
    //
    // MapLibre states (read from the native button's class list):
    //
    //          ┌──────────┐
    //          │   IDLE   │ ◄────┐
    //          └────┬─────┘      │
    //         click │            │
    //               ▼            │
    //          ┌──────────┐      │ click → IDLE
    //          │ WAITING  │      │
    //          └────┬─────┘      │
    //         first │            │
    //         fix   ▼            │
    //          ┌──────────┐      │
    //          │  ACTIVE  │ ─────┤
    //          └────┬─────┘      │
    //         user  │  ▲         │
    //         pan   │  │ click   │
    //               ▼  │         │
    //          ┌──────────┐      │ click → IDLE
    //          │BACKGROUND│ ─────┘
    //          └──────────┘
    //
    // Stock MapLibre camera behaviour at each transition:
    //   IDLE → ACTIVE         easeTo(user, fitBoundsOptions) — pan + zoom
    //   ACTIVE per-fix        easeTo(user) — just pan
    //   ACTIVE → BACKGROUND   no camera move (user is in control)
    //   BACKGROUND fix update no camera move (dot moves, not the map)
    //   BACKGROUND → ACTIVE   easeTo(user) — re-center, no zoom
    //
    // Our modifications (vs stock):
    //   - Suppress the IDLE → ACTIVE auto-zoom (preserve user's view)
    //   - Suppress per-fix easeTo by default; allow only when
    //     follow-me opt-in is on
    //   - Allow the BACKGROUND → ACTIVE re-engagement easeTo
    //   - Manual pan exits follow-me (active → background-effective)
    //
    // Where each modification lives:
    //   - Locate-button click handler: reads pre-click state, sets
    //     flags BEFORE calling trigger() — single decision point
    //   - Off-screen indicator click handler: own flyTo path; sets
    //     flags before triggering re-engage
    //   - `movestart` filter: consumes flags to allow / cancel the
    //     geolocate-source camera moves
    //   - mirrorLocateState: clears userLocation when state goes
    //     idle / disabled (handles the "tracking actually ended"
    //     transition without depending on which event fires)
    //
    // We deliberately do NOT use `trackuserlocationstart` to arm
    // flags — MapLibre 5.x fires it for IDLE → WAITING but NOT for
    // BACKGROUND → ACTIVE re-engagement (that one fires
    // userlocationfocus instead). Driving flag setup off click
    // handlers (synchronous, sees the pre-click state) is reliable
    // across MapLibre versions and across all the ways the user
    // can engage tracking.
    //
    // Flags driving the modifications:
    //
    //   _firstGeolocateMoveAfterTrigger
    //     Cancel the next geolocate-source movestart (the IDLE →
    //     ACTIVE fitBounds-zoom). Set true on idle→active click,
    //     false on background→active click. Consumed once.
    //
    //   _followUserOnGeolocate
    //     Allow per-fix easeTo updates through the movestart filter
    //     so the map auto-follows the user. Set true on any →
    //     active click. Cleared by user manual pan.
    //
    //   _showToastOnNextFix
    //     "On the next geolocate / userlocationfocus event, if the
    //     user is off-screen, show a hint toast suggesting they tap
    //     the blue ▲ indicator." Set true on idle→active (user
    //     just enabled tracking; might not realise their position
    //     is off the trail bbox). Set false on background→active
    //     (user knows what they want — to be re-centered).
    //     Consumed once.
    //
    //   _suppressOffScreenToast
    //     Hard-suppress the off-screen toast for the indicator
    //     click path; that path triggers events that would
    //     otherwise fire the toast circularly. Set in indicator
    //     click; cleared in moveend (or 2 s safety timeout).
    //
    //   userLocation
    //     Cached [lng, lat] from the latest geolocate event.
    //     Cleared by mirrorLocateState when state goes idle /
    //     disabled, hiding the indicator without depending on
    //     which event MapLibre fires for that transition.
    // ============================================================
    const geolocate = new maplibregl.GeolocateControl({
        positionOptions: {
            enableHighAccuracy: true,
            // Without an explicit timeout, watchPosition hangs forever
            // when the OS / browser can't acquire a fix — most often
            // seen on desktop macOS browsers when Location Services
            // is off, the browser-level permission is revoked, or
            // CoreLocation can't reach Apple's Wi-Fi positioning
            // service. The spinner just spins; the user has no
            // signal anything is wrong. 15 s is generous for normal
            // Wi-Fi positioning and short enough to feel responsive
            // when something's broken.
            timeout: 15000,
        },
        trackUserLocation: true,
    });
    map.addControl(geolocate, "top-left");
    geolocateControl = geolocate;  // expose to updateLocationIndicator
    attachOffScreenIndicatorHandler();

    const maybeShowOffScreenToast = () => {
        // Suppress entirely when the user's recent action was clicking
        // the indicator itself — the toast would be circular advice.
        // Also consume the flag so a LATER geolocate event (after
        // moveend has cleared the suppress flag) doesn't belatedly
        // fire the toast: the indicator click triggered a GPS fix, the
        // fix arrives after moveend, suppress is now false, flag is
        // still true → toast. Consuming here closes that race.
        if (_suppressOffScreenToast) {
            _showToastOnNextFix = false;
            return;
        }
        if (!_showToastOnNextFix || !userLocation) return;
        _showToastOnNextFix = false;
        const pt = map.project(userLocation);
        const cv = map.getCanvas();
        if (pt.x < 0 || pt.x > cv.clientWidth || pt.y < 0 || pt.y > cv.clientHeight) {
            showToast("Your location is off-screen — tap the blue ▲ at the map edge to center on it.");
        }
    };

    // movestart filter for geolocate-sourced camera moves. Three branches:
    //
    //  1. Move is user-initiated (no geolocateSource): they want to look
    //     around, exit follow-me mode. Don't cancel — the user IS the
    //     camera now.
    //  2. Move is the FIRST geolocate-source move after entering active
    //     tracking (the fitBounds-zoom yank): always cancel. Defer to a
    //     microtask, which drains after easeTo finishes setup but before
    //     any rAF fires — cancelling the animation before a frame draws.
    //  3. Move is a SUBSEQUENT geolocate-source ease (per-fix follow-me
    //     update): allow if the user has opted into follow mode (set
    //     true in the Locate-button click handler, cleared by branch 1).
    //     Otherwise cancel as before.
    map.on("movestart", (e) => {
        if (!e.geolocateSource) {
            _followUserOnGeolocate = false;
            return;
        }
        if (_firstGeolocateMoveAfterTrigger) {
            _firstGeolocateMoveAfterTrigger = false;
            queueMicrotask(() => map.stop());
            return;
        }
        if (_followUserOnGeolocate) {
            return;  // allow follow-me ease through
        }
        queueMicrotask(() => map.stop());
    });

    geolocate.on("geolocate", (e) => {
        userLocation = [e.coords.longitude, e.coords.latitude];
        updateLocationIndicator();
        maybeShowOffScreenToast();
    });

    // NOTE: no trackuserlocationstart handler. MapLibre 5.x doesn't
    // fire it for BACKGROUND → ACTIVE re-engagement (that's
    // userlocationfocus instead), so it's an unreliable hook. All
    // flag setup happens in the Locate-button click handler below
    // (see the state-machine docblock above the GeolocateControl).

    geolocate.on("trackuserlocationend", () => {
        _firstGeolocateMoveAfterTrigger = false;
        _followUserOnGeolocate = false;
        // NOTE: don't clear userLocation here. Despite the event name
        // sounding terminal, MapLibre 5.x fires trackuserlocationend
        // on ACTIVE_LOCK → BACKGROUND too (i.e. when the user just
        // panned away during tracking — they're STILL being tracked,
        // just not auto-followed). Clearing userLocation here made
        // the indicator vanish forever after the first manual pan.
        // userLocation is cleared in mirrorLocateState() instead,
        // which reads the actual button state class and only acts on
        // the true off / disabled transitions.
    });

    geolocate.on("userlocationfocus", () => {
        maybeShowOffScreenToast();
    });

    geolocate.on("outofmaxbounds", () => {
        userLocation = null;
        updateLocationIndicator();
        geolocate.trigger();
        showToast(`You are outside the ${CONFIG.name} area`);
    });

    // Surface geolocation failures so they don't silently leave the
    // Locate button in a broken-looking state. MapLibre's button
    // already changes to its error class (which we mirror), but a
    // toast tells the user what to do about it.
    geolocate.on("error", (e) => {
        const code = e && e.code;
        if (code === 1) {
            // PERMISSION_DENIED
            showToast("Location permission denied. Allow location access in your browser's site settings, then tap Locate again.");
        } else if (code === 3) {
            // TIMEOUT
            showToast("Couldn't get your location. On desktop, check that macOS Location Services is enabled for this browser.");
        } else {
            // POSITION_UNAVAILABLE or unknown
            showToast("Couldn't determine your location. Try again, or check your device's location settings.");
        }
    });

    // Wire the peek menu's Locate button to the GeolocateControl and
    // mirror its FULL state machine (idle / waiting / active /
    // background / active-error / background-error / disabled) so the
    // peek swatch renders identically to MapLibre's native control —
    // same hex colors, same iconography, same spinner during
    // acquisition. The native control is hidden via CSS but remains in
    // the DOM because its classList is the canonical state source;
    // MapLibre's public events don't cover all transitions (notably
    // background vs active, and disabled). A MutationObserver on the
    // hidden button copies state changes onto #toggle-locate via
    // locate-state-* classes (see style.css).
    const locateBtn = document.getElementById("toggle-locate");
    const nativeLocateBtn = document.querySelector(".maplibregl-ctrl-geolocate");
    const LOCATE_STATE_NAMES = [
        "idle", "waiting", "active", "background",
        "active-error", "background-error", "disabled",
    ];
    function mirrorLocateState() {
        if (!locateBtn || !nativeLocateBtn) return;
        const cls = nativeLocateBtn.classList;
        let state = "idle";
        if (nativeLocateBtn.disabled) {
            state = "disabled";
        } else if (cls.contains("maplibregl-ctrl-geolocate-background-error")) {
            state = "background-error";
        } else if (cls.contains("maplibregl-ctrl-geolocate-active-error")) {
            state = "active-error";
        } else if (cls.contains("maplibregl-ctrl-geolocate-background")) {
            state = "background";
        } else if (cls.contains("maplibregl-ctrl-geolocate-active")) {
            state = "active";
        }
        // Waiting can stack with other states during transitions; let
        // the spinner win visually because it's the most user-relevant
        // cue at that moment ("hang on, acquiring").
        if (!nativeLocateBtn.disabled && cls.contains("maplibregl-ctrl-geolocate-waiting")) {
            state = "waiting";
        }
        LOCATE_STATE_NAMES.forEach((s) => {
            locateBtn.classList.remove("locate-state-" + s);
        });
        locateBtn.classList.add("locate-state-" + state);
        // aria-pressed mirrors "is tracking" (any state other than
        // idle/waiting/disabled). The peek-icon off-state styling is
        // overridden in CSS for #toggle-locate so idle doesn't render
        // the slash — our state classes control appearance.
        const tracking = state === "active" ||
            state === "background" ||
            state === "active-error" ||
            state === "background-error";
        locateBtn.setAttribute("aria-pressed", tracking ? "true" : "false");
        // When tracking is truly off (idle = user toggled Locate
        // off; disabled = no permission / no GPS), clear the cached
        // fix so the off-screen indicator hides itself. Doing this
        // here (rather than in the geolocate event handlers) catches
        // every off-transition without conflating it with the
        // active → background transition that just means "user
        // panned but is still being tracked".
        if (state === "idle" || state === "disabled") {
            userLocation = null;
            updateLocationIndicator();
        }
    }
    if (locateBtn) {
        locateBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            // Read the current state BEFORE trigger() runs and set
            // the flags it'll need. trigger() may fire camera moves
            // and dispatch events synchronously, so the flags must
            // already be in place. See the state-machine docblock
            // above for the full rationale.
            const cls = nativeLocateBtn ? nativeLocateBtn.classList : null;
            const inBackground = cls && cls.contains(
                "maplibregl-ctrl-geolocate-background");
            const inTrackingState = cls && (
                cls.contains("maplibregl-ctrl-geolocate-active") ||
                cls.contains("maplibregl-ctrl-geolocate-waiting") ||
                cls.contains("maplibregl-ctrl-geolocate-active-error") ||
                cls.contains("maplibregl-ctrl-geolocate-background-error"));
            if (inBackground) {
                // BACKGROUND → ACTIVE re-engagement. Stock-like
                // re-centering: allow MapLibre's easeTo through, no
                // hint toast (the user is being centered right now).
                _firstGeolocateMoveAfterTrigger = false;
                _showToastOnNextFix = false;
                _followUserOnGeolocate = true;
            } else if (!inTrackingState) {
                // IDLE / DISABLED → ACTIVE (initial enable). Original
                // framework intent: don't yank the camera with the
                // fitBounds-zoom; arm the hint toast so the user
                // knows to tap the indicator if their dot is off-
                // screen.
                _firstGeolocateMoveAfterTrigger = true;
                _showToastOnNextFix = true;
                _followUserOnGeolocate = true;
            }
            // else: ACTIVE / WAITING / *_ERROR → trigger() goes to
            //   IDLE. mirrorLocateState handles userLocation cleanup
            //   and the indicator hides itself; no flag setup needed.
            //
            // trigger() returns false only during initial permission
            // query; in that case clicking again once permission
            // resolves will work. No explicit retry needed.
            geolocate.trigger();
        });
        if (nativeLocateBtn) {
            const obs = new MutationObserver(mirrorLocateState);
            obs.observe(nativeLocateBtn, {
                attributes: true,
                attributeFilter: ["class", "disabled"],
            });
            mirrorLocateState();
        } else {
            // Fallback if native control didn't mount (should not
            // happen in practice — map.addControl above adds it).
            locateBtn.classList.add("locate-state-idle");
        }
    }

    // NOTE: no auto-locate on load. Even with permission already
    // granted, automatically calling geolocate.trigger() would put
    // the GPS into high-accuracy continuous mode (we set
    // enableHighAccuracy: true above), which is a meaningful battery
    // drain on a multi-hour ride. Tracking is strictly user-initiated
    // — they tap the Locate button when they want it.

    // Refresh the off-screen indicator on every visible-area change.
    // `move` covers pan + zoom + pitch + rotate in MapLibre, but we
    // also wire `zoom` + `resize` defensively — pinch-zoom centered
    // on the screen has been observed to not always fire `move`
    // (depending on MapLibre version + touch-event ordering), and
    // resize doesn't fire `move` at all. Without these, the user can
    // zoom in until their dot leaves the viewport but the indicator
    // doesn't appear until the next pan. All three feed the same rAF
    // debounce so multiple events per frame collapse to one update.
    let rafPending = false;
    const scheduleIndicatorUpdate = () => {
        if (rafPending) return;
        rafPending = true;
        requestAnimationFrame(() => {
            rafPending = false;
            updateLocationIndicator();
        });
    };
    map.on("move", scheduleIndicatorUpdate);
    map.on("zoom", scheduleIndicatorUpdate);
    map.on("resize", scheduleIndicatorUpdate);

    const attrControl = new maplibregl.AttributionControl({ compact: true });
    map.addControl(attrControl, "bottom-right");
    attrControl._container.classList.add("maplibregl-compact-show");

    // Load data and add layers once map is ready. The bottom sheet is
    // hidden via inline visibility:hidden in the HTML and is revealed
    // at the end of setupBottomSheet(). If setup throws, reveal it
    // anyway in finally so the user isn't stranded with no controls.
    map.once("style.load", async () => {
        try {
            await loadTrails();
            await loadPOIs();
            buildRouteIndex();
            buildTrailIndex();
            setupBottomSheet();
            setupInteractions();
            promoteBasemapLabels();
            suppressPathLabels();
            suppressBasemapPois();
            // Apply share-link highlight, if any. Done here (after
            // both trails and route/trail indexes are built) so we
            // can resolve route IDs / trail names against real data.
            // Best-effort — silently skip if the referenced route or
            // trail no longer exists (e.g. the map has been rebuilt
            // since the link was shared and the relation changed).
            if (_pendingShareHighlight) {
                applyPendingShareHighlight();
            }
        } finally {
            const sheet = document.getElementById("bottom-sheet");
            if (sheet) sheet.style.visibility = "";
        }
    });

    // Update page title
    document.title = CONFIG.title;

    // About This Map modal
    initAbout();

    // First-visit welcome modal (A.2). Self-suppresses after the first
    // dismiss via localStorage flag. Per-map flag (key includes slug) so
    // a user who's seen one map's welcome still sees another map's.
    initWelcomeModal();
}

// ============================================================
// First-visit welcome modal (A.2)
// ============================================================
//
// Bridges the gap between landing on the map and understanding the
// peek bar / routes-vs-trails distinction / where the About button
// lives. Shown ONCE per map per browser; dismissal stored in
// localStorage under `mtb.welcomed:<slug>`. Subsequent visits skip
// it entirely (no flicker).
//
// The body copy is built from CONFIG so it reflects the actual
// features available on the current map (routes vs trails sections,
// share button, install affordance) rather than describing things
// that aren't there.
function initWelcomeModal() {
    const modal = document.getElementById("welcome-modal");
    if (!modal) return;
    const slug = CONFIG.slug || "default";
    const flagKey = `mtb.welcomed:${slug}`;
    if (LS.get(flagKey) === "true") return;

    const titleEl = document.getElementById("welcome-modal-title");
    const bodyEl = document.getElementById("welcome-modal-body");
    const closeBtn = document.getElementById("welcome-modal-close");
    const cta = document.getElementById("welcome-modal-cta");

    if (titleEl) titleEl.textContent = `Welcome to ${CONFIG.title || CONFIG.name || "this trail map"}`;

    // Build body copy reflecting the map's actual features.
    const showRoutes = CONFIG.showRoutes !== false;
    const showTrails = CONFIG.showTrails !== false;
    const finderLine = (showRoutes && showTrails)
        ? "Search <strong>routes</strong> (named loops) or <strong>trails</strong> (the segments that make them up) from the bottom sheet."
        : showRoutes
            ? "Search <strong>routes</strong> (named loops) from the bottom sheet."
            : showTrails
                ? "Search <strong>trails</strong> from the bottom sheet."
                : null;

    const bullets = [];
    bullets.push("Tap the <strong>locate icon</strong> in the bottom bar to centre the map on you.");
    if (finderLine) {
        bullets.push("Tap the <strong>search icon</strong> to find routes and trails by name.");
    }
    bullets.push("Tap or drag the bottom bar up to open <strong>Settings</strong> — toggle layer visibility, change the season, pick a basemap, and more. Settings are saved between visits.");
    bullets.push("Tap <strong>About this map</strong> for sources, attribution, and contact info.");
    if (CONFIG.shareButton) {
        bullets.push("Found a great view? Tap <strong>Share this view</strong> to send a link with the current zoom and any highlighted route.");
    }

    if (bodyEl) {
        bodyEl.innerHTML = "<ul>" +
            bullets.map((b) => `<li>${b}</li>`).join("") + "</ul>";
    }

    function dismissWelcome() {
        LS.set(flagKey, "true");
        modal.classList.add("hidden");
    }

    if (closeBtn) closeBtn.addEventListener("click", dismissWelcome);
    if (cta) cta.addEventListener("click", dismissWelcome);

    // Escape key dismisses too — matches About modal behavior so the
    // keyboard pattern is consistent.
    function onKeydown(e) {
        if (e.key === "Escape" && !modal.classList.contains("hidden")) {
            dismissWelcome();
        }
    }
    document.addEventListener("keydown", onKeydown);

    // Show the modal. Done after a tick so the bottom-sheet visibility
    // toggle has settled (otherwise on slow paint the modal can flash
    // with nothing behind it).
    requestAnimationFrame(() => modal.classList.remove("hidden"));
}

// ============================================================
// About This Map modal
// ============================================================
function initAbout() {
    const btn = document.getElementById("about-btn");
    const modal = document.getElementById("about-modal");
    const closeBtn = modal.querySelector(".about-modal-close");

    btn.addEventListener("click", openAboutModal);
    // Close via the X or Escape — backdrop clicks intentionally
    // do nothing. Rationale: About is launched from inside the
    // expanded bottom sheet, and the user expects to return to
    // that still-open sheet when they're done. A stray backdrop
    // tap was previously dismissing both About AND the underlying
    // sheet because the click bubbled to the sheet's document-
    // level outside-click handler. Escape is handled inside the
    // sheet's single document-level Escape handler (see
    // setupBottomSheet) so the priority order is explicit: About
    // first, sheet next, highlight last — one press, one action.
    closeBtn.addEventListener("click", (e) => {
        // stopPropagation keeps this X click from bubbling to the
        // document-level "click outside sheet → closeSheet" handler
        // in setupBottomSheet.
        e.stopPropagation();
        closeAboutModal();
    });

    buildAboutModalContent();
}

function openAboutModal() {
    document.getElementById("about-modal").classList.remove("hidden");
}

function closeAboutModal() {
    document.getElementById("about-modal").classList.add("hidden");
}

function aboutExtLink(url, label) {
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = label;
    return a;
}

function buildAboutModalContent() {
    const titleEl = document.getElementById("about-modal-title");
    const body = document.getElementById("about-modal-body");
    const tail = document.getElementById("about-modal-tail");
    titleEl.textContent = CONFIG.title || "About This Map";
    body.innerHTML = "";
    tail.innerHTML = "";

    // Wrap title in a flex row so the optional brand logo can sit on the
    // right. Idempotent.
    let header = titleEl.parentNode.classList.contains("about-modal-header")
        ? titleEl.parentNode
        : null;
    if (!header) {
        header = document.createElement("div");
        header.className = "about-modal-header";
        titleEl.parentNode.insertBefore(header, titleEl);
        header.appendChild(titleEl);
    } else {
        header.querySelectorAll(".about-modal-logo").forEach((n) => n.remove());
    }
    if (CONFIG.logoUrl) {
        const img = document.createElement("img");
        img.src = CONFIG.logoUrl;
        img.alt = (CONFIG.title || "Map") + " logo";
        img.className = "about-modal-logo";
        header.appendChild(img);
    }

    const about = CONFIG.about || {};

    // Description
    if (about.description) {
        const p = document.createElement("p");
        p.textContent = about.description;
        body.appendChild(p);
    }

    // More Information
    if (Array.isArray(about.more_information) && about.more_information.length) {
        const h = document.createElement("h3");
        h.textContent = "More Information";
        body.appendChild(h);
        const ul = document.createElement("ul");
        about.more_information.forEach((link) => {
            if (!link || !link.url) return;
            const li = document.createElement("li");
            li.appendChild(aboutExtLink(link.url, link.label || link.url));
            ul.appendChild(li);
        });
        body.appendChild(ul);
    }

    // Author
    if (about.author && (about.author.name || about.author.url)) {
        const h = document.createElement("h3");
        h.textContent = "Author";
        body.appendChild(h);
        const p = document.createElement("p");
        if (about.author.url && about.author.name) {
            p.appendChild(aboutExtLink(about.author.url, about.author.name));
        } else if (about.author.url) {
            p.appendChild(aboutExtLink(about.author.url, about.author.url));
        } else {
            p.textContent = about.author.name;
        }
        body.appendChild(p);
    }

    // Extra Links
    if (Array.isArray(about.extra_links) && about.extra_links.length) {
        const h = document.createElement("h3");
        h.textContent = "Additional Links";
        body.appendChild(h);
        const ul = document.createElement("ul");
        about.extra_links.forEach((link) => {
            if (!link || !link.url) return;
            const li = document.createElement("li");
            li.appendChild(aboutExtLink(link.url, link.label || link.url));
            ul.appendChild(li);
        });
        body.appendChild(ul);
    }

    // Versions (always)
    const versionsHeader = document.createElement("h3");
    versionsHeader.textContent = "Versions";
    tail.appendChild(versionsHeader);
    if (CONFIG.dataDate) {
        const p = document.createElement("p");
        p.className = "about-modal-version";
        p.textContent = `Trail data: ${CONFIG.dataDate}`;
        tail.appendChild(p);
    }
    if (CONFIG.buildDate) {
        const p = document.createElement("p");
        p.className = "about-modal-version";
        p.textContent = `App built: ${CONFIG.buildDate}`;
        tail.appendChild(p);
    }

    // Credits (always)
    const creditsHeader = document.createElement("h3");
    creditsHeader.textContent = "Credits";
    tail.appendChild(creditsHeader);

    const osmP = document.createElement("p");
    osmP.appendChild(document.createTextNode("Trail data \u00a9 "));
    osmP.appendChild(aboutExtLink("https://www.openstreetmap.org/copyright", "OpenStreetMap contributors"));
    tail.appendChild(osmP);

    const pmP = document.createElement("p");
    pmP.appendChild(document.createTextNode("Basemap tiles by "));
    pmP.appendChild(aboutExtLink("https://protomaps.com", "Protomaps"));
    tail.appendChild(pmP);

    const fwP = document.createElement("p");
    fwP.appendChild(document.createTextNode("Generated with "));
    fwP.appendChild(aboutExtLink("https://github.com/c0nsumer/mtb-map-framework", "mtb-map-framework"));
    tail.appendChild(fwP);
}

// ============================================================
// Style building (light theme only)
// ============================================================
function getBaseUrl() {
    const loc = window.location;
    const path = loc.pathname.endsWith("/") ? loc.pathname : loc.pathname.replace(/\/[^/]*$/, "/");
    return `${loc.protocol}//${loc.host}${path}`;
}

function isCustomLayer() {
    return basemapMode.startsWith("custom:");
}

function getCustomLayer() {
    const id = basemapMode.replace("custom:", "");
    return (CONFIG.baseLayers || []).find((l) => l.id === id);
}

function buildHeaderMap() {
    const headersByDomain = {};
    for (const layer of CONFIG.baseLayers || []) {
        if (layer.headers) {
            try {
                const domain = new URL(layer.url.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0")).hostname;
                headersByDomain[domain] = layer.headers;
            } catch (e) { /* skip malformed URLs */ }
        }
    }
    return headersByDomain;
}

function buildStyle() {
    const base = getBaseUrl();

    if (isCustomLayer()) {
        return buildCustomStyle(getCustomLayer(), base);
    }

    // Default: Protomaps light flavor. Dark mode was removed in the UI
    // redesign — light theme only across all maps.
    const flavor = "light";
    const basemapLayers = basemaps.layers("basemap", basemaps.namedFlavor(flavor), { lang: "en" });

    return {
        version: 8,
        glyphs: `${base}fonts/{fontstack}/{range}.pbf`,
        sprite: `${base}sprites/v4/${flavor}`,
        sources: {
            basemap: {
                type: "vector",
                url: "pmtiles://basemap.pmtiles",
                attribution: '<a href="https://protomaps.com">Protomaps</a> &copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
            },
        },
        layers: basemapLayers,
    };
}

function buildCustomStyle(layer, base) {
    const sourceConfig = {
        type: "raster",
        tiles: [layer.url],
        tileSize: layer.tile_size || 256,
        attribution: layer.attribution || "",
    };
    if (layer.max_zoom) sourceConfig.maxzoom = layer.max_zoom;

    return {
        version: 8,
        glyphs: `${base}fonts/{fontstack}/{range}.pbf`,
        sprite: `${base}sprites/v4/light`,
        sources: { basemap: sourceConfig },
        layers: [
            {
                id: "custom-raster",
                type: "raster",
                source: "basemap",
                paint: {},
            },
        ],
    };
}

// ============================================================
// Terrain / Hillshade (light tones)
// ============================================================
async function addTerrainLayers() {
    try {
        const resp = await fetch("terrain.pmtiles", { method: "HEAD" });
        if (!resp.ok) return;
    } catch {
        return;
    }

    map.addSource("terrain", {
        type: "raster-dem",
        url: "pmtiles://terrain.pmtiles",
        tileSize: 256,
        encoding: "terrarium",
    });

    let beforeLayer = null;
    const layers = map.getStyle().layers;
    for (const layer of layers) {
        if (layer.type === "symbol") {
            beforeLayer = layer.id;
            break;
        }
    }

    map.addLayer({
        id: "hillshade",
        type: "hillshade",
        source: "terrain",
        paint: {
            "hillshade-illumination-direction": 315,
            "hillshade-illumination-anchor": "map",
            "hillshade-exaggeration": 0.4,
            "hillshade-shadow-color": "#3d3d3d",
            "hillshade-highlight-color": "#ffffff",
        },
    }, beforeLayer);
}

// ============================================================
// Helper: perceived luminance of a CSS hex color (0–1)
// ============================================================
function colorLuminance(hex) {
    hex = hex.replace("#", "");
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    const r = parseInt(hex.substring(0, 2), 16) / 255;
    const g = parseInt(hex.substring(2, 4), 16) / 255;
    const b = parseInt(hex.substring(4, 6), 16) / 255;
    return 0.299 * r + 0.587 * g + 0.114 * b;
}

// Resolve the colour a route appears as on the map, in priority order:
//   1. dashed_relations[id].colors[0] — explicit dash colours beat anything
//   2. routeInfo.colour — from OSM `colour` tag or relation_colors override
//   3. CONFIG.defaultTrailColor
function effectiveRouteColor(routeInfo) {
    if (!routeInfo) return CONFIG.defaultTrailColor;
    if (Array.isArray(routeInfo.dashColors) && routeInfo.dashColors.length > 0) {
        return routeInfo.dashColors[0];
    }
    if (routeInfo.colour) return routeInfo.colour;
    return CONFIG.defaultTrailColor;
}

// Shared threshold for "is this route's color light or dark?" — used
// by every layer that picks a contrasting outline (casing on the
// trail line, halo on the clip-arrow symbol, default-color fallback
// in difficulty-mode casings). 0.5 is the natural midpoint of
// perceived luminance via the standard 0.299·R + 0.587·G + 0.114·B
// formula. Keeping all bidirectional decisions on the same threshold
// means a route's casing and its clip-arrow's halo always go the
// same direction (both dark or both light), so a dark-colored trail
// gets a visible casing AND a visible arrow halo on any basemap.
const CONTRAST_LUM_THRESHOLD = 0.5;

// Bidirectional casing color for trail lines. Light routes get a
// translucent-dark casing (definition against light basemap); dark
// routes get a translucent-light casing (visibility on dark basemap
// or against the route's own dark fill). Both at 0.6 alpha; the
// trail-casing layer further multiplies by line-opacity 0.5, so the
// effective rendered alpha is ~0.3 either direction.
//
// Previous logic returned `#000000` solid black for any route below
// the 0.7 luminance threshold — invisible against dark backgrounds
// for any route with a dark color. This unification with
// contrastingHaloColor (same threshold, same bidirectional pattern)
// fixes that latent bug.
function casingColor(routeInfo) {
    const lum = colorLuminance(effectiveRouteColor(routeInfo));
    return lum > CONTRAST_LUM_THRESHOLD
        ? "rgba(0,0,0,0.6)"            // dark casing for light routes
        : "rgba(255,255,255,0.6)";     // light casing for dark routes
}

// Bidirectional halo color for route-colored symbols (clip arrows
// today; future symbols can reuse). Light routes get a dark halo so
// the symbol stays visible against light basemap; dark routes get a
// light halo so the symbol doesn't blend into similar dark
// surroundings (or into the route's own dark fill). Same threshold
// as casingColor so both decisions stay aligned.
function contrastingHaloColor(routeInfo) {
    const lum = colorLuminance(effectiveRouteColor(routeInfo));
    return lum > CONTRAST_LUM_THRESHOLD
        ? "rgba(0,0,0,0.85)"
        : "rgba(255,255,255,0.85)";
}

// Per-layer icon-color for clip-arrow layers at SHARED endpoints
// (visible_count >= 2). N identical layers paint the same pixels and
// composite via 1 - (1-a)^N; to land on a chosen combined opacity target
// irrespective of N, each layer needs alpha = 1 - (1-target)^(1/N).
function sharedArrowColor() {
    const target = 0.60;
    const rgb = "rgba(0,0,0";
    const alpha = (n) => (1 - Math.pow(1 - target, 1 / n)).toFixed(4);
    return [
        "case",
        [">=", ["get", "visible_count"], 5], `${rgb},${alpha(5)})`,
        [">=", ["get", "visible_count"], 4], `${rgb},${alpha(4)})`,
        [">=", ["get", "visible_count"], 3], `${rgb},${alpha(3)})`,
        `${rgb},${alpha(2)})`,
    ];
}

// MapLibre match expression: imba_difficulty → IMBA colors
function difficultyColorExpr() {
    return [
        "match", ["get", "imba_difficulty"],
        "0", IMBA_RATINGS[0].color,
        "1", IMBA_RATINGS[1].color,
        "2", IMBA_RATINGS[2].color,
        "3", IMBA_RATINGS[3].color,
        "4", IMBA_RATINGS[4].color,
        "5", IMBA_RATINGS[5].color,
        CONFIG.defaultTrailColor,
    ];
}

// Casing-color expression for difficulty mode (light theme).
// Per-rating overrides are explicit, hand-tuned to the IMBA fill
// colors — kept as-is. The fallback (unrated trails painted in
// CONFIG.defaultTrailColor) goes through the same bidirectional
// logic as casingColor() so the casing for an unrated dark default
// trail color doesn't disappear.
function difficultyCasingExpr() {
    const defaultLum = colorLuminance(CONFIG.defaultTrailColor);
    const defaultCasing = defaultLum > CONTRAST_LUM_THRESHOLD
        ? "rgba(0,0,0,0.6)"
        : "rgba(255,255,255,0.6)";
    return [
        "match", ["get", "imba_difficulty"],
        "0", "rgba(0,0,0,0.6)",     // white fill → dark casing
        "1", "#000000",              // green fill → black casing
        "2", "#000000",              // blue fill → black casing
        "3", "rgba(255,255,255,0.6)", // black fill → light casing
        "4", "rgba(255,255,255,0.6)", // black fill → light casing
        "5", "#000000",              // orange fill → black casing
        defaultCasing,               // unrated → derive from defaultTrailColor
    ];
}

// ============================================================
// Proximity filtering helpers
// ============================================================
// Distance (meters) from the nearest visible trail within which a
// trail_marker or feature POI is allowed to render. Features tagged
// `tourism=attraction` in OSM often sit 10-50 m off the trail (scenic
// viewpoints, old structures, named rocks) — 50 m is a permissive
// default that surfaces them while still filtering out incidental
// bbox POIs that aren't actually trail-adjacent. Configurable per-map
// via the `poi_proximity_m` YAML key.
const POI_PROXIMITY_METERS = CONFIG.poiProximityMeters ?? 50;

// Threshold (meters) for "on the highlighted route" during the
// spotlight dim. Intentionally tight — when a single route/trail is
// highlighted, only POIs that sit essentially on its geometry stay at
// full brightness; everything else (parking lots, trailheads, POIs on
// adjacent trails) dims to 25 % opacity so the highlighted feature
// reads as a clean spotlight. Not user-configurable; this is a UI
// polish knob, not a data-visibility knob like POI_PROXIMITY_METERS.
const HIGHLIGHT_POI_PROXIMITY_METERS = 10;

function pointToSegmentDistance(px, py, ax, ay, bx, by) {
    const cosLat = Math.cos(((py + ay + by) / 3) * Math.PI / 180);
    const dx = (bx - ax) * cosLat;
    const dy = by - ay;
    const lenSq = dx * dx + dy * dy;
    let t = 0;
    if (lenSq > 0) {
        t = Math.max(0, Math.min(1, ((px - ax) * cosLat * dx + (py - ay) * dy) / lenSq));
    }
    const projX = (ax + t * (bx - ax) - px) * cosLat;
    const projY = ay + t * (by - ay) - py;
    return Math.sqrt(projX * projX + projY * projY) * 111320;
}

function distanceToVisibleTrails(lng, lat) {
    if (!routesData) return Infinity;
    let minDist = Infinity;
    for (const f of routesData.features) {
        if (!visibleRoutes.has(f.properties.route_id)) continue;
        const coords = f.geometry.type === "LineString"
            ? f.geometry.coordinates
            : f.geometry.coordinates.flat();
        for (let i = 0; i < coords.length - 1; i++) {
            const d = pointToSegmentDistance(lng, lat, coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]);
            if (d < minDist) {
                minDist = d;
                if (d === 0) return 0;
            }
        }
    }
    return minDist;
}

// Min distance (meters) from (lng, lat) to the currently-highlighted
// route or trail's line geometry. For a route highlight, any feature
// whose shared_routes includes the highlighted id counts (captures
// shared segments that belong to the route). For a trail highlight,
// only features with matching trail_name count. Returns Infinity when
// no highlight is set or routesData hasn't loaded — callers treat that
// as "too far" and dim the marker.
function distanceToHighlighted(lng, lat) {
    if (!routesData || !highlight) return Infinity;
    let minDist = Infinity;
    for (const f of routesData.features) {
        const props = f.properties;
        let match = false;
        if (highlight.kind === "route") {
            const shared = props.shared_routes || [props.route_id];
            match = shared.includes(highlight.key);
        } else {
            match = props.trail_name === highlight.key;
        }
        if (!match) continue;
        const coords = f.geometry.type === "LineString"
            ? f.geometry.coordinates
            : f.geometry.coordinates.flat();
        for (let i = 0; i < coords.length - 1; i++) {
            const d = pointToSegmentDistance(lng, lat,
                coords[i][0], coords[i][1],
                coords[i + 1][0], coords[i + 1][1]);
            if (d < minDist) {
                minDist = d;
                if (d === 0) return 0;
            }
        }
    }
    return minDist;
}

// Fade markers that aren't adjacent to the highlighted route/trail.
// Uses MapLibre's Marker.setOpacity() API rather than touching the DOM
// element directly. Reason: MapLibre's render loop writes
// `_element.style.opacity` on every tick (for terrain-occlusion
// handling — `_opacity` vs `_opacityWhenCovered`). An inline opacity
// or CSS class rule on `.maplibregl-marker` gets clobbered by that
// inline write within one frame, which is why every earlier fix
// attempt looked like it worked in isolation but did nothing live.
// setOpacity() feeds the value into MapLibre's own state so the per-
// frame writer uses it. User-location markers are never enumerated
// here, so they never dim. Safe to call repeatedly.
function updateMarkerDimState() {
    const active = highlightDimActive();
    const apply = (markers) => {
        for (const marker of markers) {
            let dimmed = false;
            if (active) {
                const { lng, lat } = marker.getLngLat();
                const dist = distanceToHighlighted(lng, lat);
                dimmed = dist > HIGHLIGHT_POI_PROXIMITY_METERS;
            }
            marker.setOpacity(dimmed ? "0.25" : "1");
        }
    };
    apply(trailMarkerMarkers);
    apply(parkingMarkers);
    apply(trailheadMarkers);
    apply(featureMarkers);
}

function updateMarkerProximity() {
    // Peek toggles use aria-pressed semantics (not checkbox .checked).
    // Trail markers (guideposts + emergency access points) share the
    // single "Markers" button.
    const mkBtn = document.getElementById("toggle-markers");
    const ftBtn = document.getElementById("toggle-features");
    const mkOn = !!mkBtn && mkBtn.getAttribute("aria-pressed") === "true";
    const ftOn = !!ftBtn && ftBtn.getAttribute("aria-pressed") === "true";

    const filterMarkers = (markers, on) => {
        if (!on) return;
        for (const marker of markers) {
            const { lng, lat } = marker.getLngLat();
            const dist = distanceToVisibleTrails(lng, lat);
            if (dist <= POI_PROXIMITY_METERS) {
                marker.addTo(map);
            } else {
                marker.remove();
            }
        }
    };

    filterMarkers(trailMarkerMarkers, mkOn);
    filterMarkers(featureMarkers, ftOn);

    // Markers are obstacles for the decoration placer (gatherObstacles
    // walks the four marker arrays). When any marker is added/removed
    // here, recompute the decorations so arrows/diamonds/labels skip
    // the new POI footprints (or reclaim space when a marker drops).
    invalidateObstaclesCache();
    if (map && map.getSource("trail-decorations")) {
        updateDecorationsSource();
    }

    // Re-evaluate the Features peek button after every proximity pass.
    // If the current visible-routes set leaves zero features within
    // POI_PROXIMITY_METERS of any trail, the button is a dead control —
    // hide it. It comes back the moment a route change brings a
    // near-trail feature into scope.
    updateFeatureButtonVisibility();
}

// True iff at least one POI.FEATURE POI is within
// POI_PROXIMITY_METERS of a currently-visible trail. Independent of
// the Features toggle state (we ask "would anything show if it were
// on?", not "is anything showing now?").
function hasVisibleFeatures() {
    if (!poisData || !routesData) return false;
    for (const f of poisData.features) {
        if (f.properties.poi_type !== POI.FEATURE) continue;
        const [lng, lat] = f.geometry.coordinates;
        if (distanceToVisibleTrails(lng, lat) <= POI_PROXIMITY_METERS) {
            return true;
        }
    }
    return false;
}

// Show/hide the Features peek button based on (a) the YAML gate,
// (b) whether the build emitted any feature POIs at all, and (c)
// whether the proximity filter would currently let any of them
// render. Called on initial POI load and after every route-
// visibility change. The button is `.hidden` when dead so the
// peek row collapses cleanly; persisted aria-pressed state is
// untouched, so toggling features back on once a near-trail
// feature reappears just works.
function updateFeatureButtonVisibility() {
    const btn = document.getElementById("toggle-features");
    if (!btn) return;
    const show = CONFIG.showFeatures && hasVisibleFeatures();
    btn.classList.toggle("hidden", !show);
}

// ============================================================
// Helper: dash properties
// ============================================================
function isDashed(routeInfo) {
    return !!routeInfo.dashed;
}

function getDashPattern(routeInfo) {
    return routeInfo.dashed || [1, 0];
}

function getDashCap(routeInfo) {
    return routeInfo.dashCap || "round";
}

function getDashColors(routeInfo) {
    return routeInfo.dashColors || null;
}

// ============================================================
// Trail data loading
// ============================================================
async function loadTrails() {
    // trails.geojson is required — if it 404s or fails to parse, the
    // app has nothing to render. Fail loudly with a visible message
    // instead of letting downstream addSource calls die opaquely.
    try {
        const resp = await fetch("trails.geojson");
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
        }
        routesData = await resp.json();
    } catch (e) {
        console.error("loadTrails: trails.geojson failed to load:", e);
        showToast("Map data failed to load. Try reloading the page.");
        throw e;  // halt init — there's nothing useful to render
    }

    // Optional sibling file: continuation arrowhead points at bbox-edge
    // endpoints of clipped relations. Build-time flag tells us whether
    // the file actually exists in this build (most maps don't have
    // clipped_relations); skip the fetch entirely when it doesn't, so
    // the network log isn't littered with 404s from an unconditional
    // probe-fetch.
    if (CONFIG.hasClipEndpoints) {
        try {
            const clipResp = await fetch("clip_endpoints.geojson");
            if (clipResp.ok) clipEndpointsData = await clipResp.json();
        } catch (_) {
            clipEndpointsData = null;
        }
    }

    // Initial visibility from bucket model + persisted state
    rebuildVisibleRoutesSet();

    // Add trail source (for line rendering with paint-based line-offset)
    map.addSource("trails", {
        type: "geojson",
        data: computeOffsetsAndFilter(),
    });

    // Add label source (with geometrically offset coordinates)
    map.addSource("trails-labels", {
        type: "geojson",
        data: computeLabelData(),
    });

    // Decoration source — pre-deconflicted Point features (trail
    // names, route names, IMBA diamonds, direction arrows). All
    // placement decisions happen in JS at compute time, so the four
    // decor layers can render with `*-allow-overlap: true` and skip
    // MapLibre's per-tile collision pipeline. See computeDecorations()
    // for the placement algorithm.
    map.addSource("trail-decorations", {
        type: "geojson",
        data: computeDecorations(),
    });

    // Continuation arrowheads for clipped relations
    if (clipEndpointsData) {
        for (const f of clipEndpointsData.features) {
            const ids = f.properties.route_ids || [];
            let n = 0;
            for (const rid of ids) {
                if (visibleRoutes.has(rid)) n++;
            }
            f.properties.visible_count = n;
        }
        map.addSource("clip-endpoints", {
            type: "geojson",
            data: clipEndpointsData,
        });
    }

    // Add terrain/hillshade (before trails, after basemap)
    if (CONFIG.showTerrain) {
        await addTerrainLayers();
    }

    // Create layers for each route. Casings first (bottom), then fills.
    const routes = CONFIG.routes;
    const sortedRoutes = Object.entries(routes)
        .sort(([, a], [, b]) => a.name.localeCompare(b.name));

    const byDifficulty = CONFIG.colorBy === "difficulty";

    // Pass 1: casings
    for (const [routeId, routeInfo] of sortedRoutes) {
        const dashed = isDashed(routeInfo);
        const cap = getDashCap(routeInfo);

        const casingCol = byDifficulty
            ? difficultyCasingExpr()
            : casingColor(routeInfo);

        map.addLayer({
            id: `trail-casing-${routeId}`,
            type: "line",
            source: "trails",
            filter: ["==", ["get", "route_id"], routeId],
            paint: {
                "line-color": casingCol,
                "line-width": dashed
                    ? ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 4, 18, 7]
                    : ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 6, 18, 10],
                "line-offset": makeOffsetExpr(),
                "line-opacity": dashed ? 0 : 0.5,
                "line-dasharray": getDashPattern(routeInfo),
            },
            layout: {
                "line-cap": dashed ? cap : "round",
                "line-join": dashed ? (cap === "square" ? "miter" : "round") : "round",
            },
        });
    }

    // Pass 2: fills
    for (const [routeId, routeInfo] of sortedRoutes) {
        const color = routeInfo.colour || CONFIG.defaultTrailColor;
        const dashed = isDashed(routeInfo);
        const cap = getDashCap(routeInfo);
        const dashColors = getDashColors(routeInfo);

        const fillColor = byDifficulty
            ? difficultyColorExpr()
            : (dashColors ? dashColors[0] : color);

        // When colouring by difficulty with a dashed default, split into
        // rated (solid) and unrated (dashed) fill layers.
        const hasDefaultDash = byDifficulty && CONFIG.defaultTrailDash;
        const ratedFilter = hasDefaultDash
            ? ["all", ["==", ["get", "route_id"], routeId],
                      ["in", ["get", "imba_difficulty"], ["literal", ["0","1","2","3","4","5"]]]]
            : ["==", ["get", "route_id"], routeId];

        // Two-color dashes: solid color B underlay, then dashed color A on top.
        if (dashColors && dashColors.length >= 2) {
            map.addLayer({
                id: `trail-fill2-${routeId}`,
                type: "line",
                source: "trails",
                filter: ["==", ["get", "route_id"], routeId],
                paint: {
                    "line-color": dashColors[1],
                    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 4, 18, 7],
                    "line-offset": makeOffsetExpr(),
                },
                layout: {
                    "line-cap": cap,
                    "line-join": cap === "square" ? "miter" : "round",
                },
            });
        }

        map.addLayer({
            id: `trail-fill-${routeId}`,
            type: "line",
            source: "trails",
            filter: ratedFilter,
            paint: {
                "line-color": fillColor,
                "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 4, 18, 7],
                "line-offset": makeOffsetExpr(),
                "line-dasharray": getDashPattern(routeInfo),
            },
            layout: {
                "line-cap": dashed ? cap : "round",
                "line-join": dashed ? (cap === "square" ? "miter" : "round") : "round",
            },
        });

        if (hasDefaultDash) {
            const defaultCap = CONFIG.defaultTrailCap || "round";
            map.addLayer({
                id: `trail-fill-unrated-${routeId}`,
                type: "line",
                source: "trails",
                filter: ["all", ["==", ["get", "route_id"], routeId],
                                ["!", ["in", ["get", "imba_difficulty"],
                                       ["literal", ["0","1","2","3","4","5"]]]]],
                paint: {
                    "line-color": CONFIG.defaultTrailColor,
                    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 4, 18, 7],
                    "line-offset": makeOffsetExpr(),
                    "line-dasharray": CONFIG.defaultTrailDash,
                },
                layout: {
                    "line-cap": defaultCap,
                    "line-join": defaultCap === "square" ? "miter" : "round",
                },
            });
        }
    }

    // Dim-tint — full-viewport black wash, hidden by default. Rendered
    // above the basemap + trail casings/fills but below the clip-arrows,
    // highlights, labels, difficulty, and arrows. When
    // `CONFIG.mapDimOnHighlight` is true AND a route/trail is highlighted,
    // applyDimState() flips visibility to "visible" and the basemap +
    // non-highlighted trail lines recede behind the wash so the
    // highlighted ribbon reads as a spotlight.
    map.addLayer({
        id: "dim-tint",
        type: "background",
        layout: { "visibility": "none" },
        paint: {
            "background-color": "#000",
            "background-opacity": 0.45,
        },
    });

    // Pass 3: continuation arrowheads at clipped-relation bbox endpoints.
    if (map.getSource("clip-endpoints")) {
        for (const [routeId, routeInfo] of sortedRoutes) {
            // Per-endpoint color logic, paired:
            //   visible_count >= 2 (multi-route convergence) →
            //     fill black (composited alpha via sharedArrowColor)
            //     with a light halo so the dark arrow stays readable
            //     against dark basemap backgrounds.
            //   single route →
            //     fill with the route's actual color, halo bidirectional
            //     (contrastingHaloColor) so light-colored routes get a
            //     dark outline and dark-colored routes get a light one.
            //     Mirrors the trail line casings' contrast logic.
            const iconCol = [
                "case",
                [">=", ["get", "visible_count"], 2],
                sharedArrowColor(),
                effectiveRouteColor(routeInfo),
            ];
            const haloCol = [
                "case",
                [">=", ["get", "visible_count"], 2],
                "rgba(255,255,255,0.85)",   // light halo on dark shared fill
                contrastingHaloColor(routeInfo),
            ];
            map.addLayer({
                id: `clip-arrow-${routeId}`,
                type: "symbol",
                source: "clip-endpoints",
                // Pipe-delimited substring match — leading/trailing `|`
                // prevents 38467 from matching 384670.
                filter: ["in", `|${routeId}|`, ["get", "route_ids_str"]],
                layout: {
                    "icon-image": "clip-arrow",
                    "icon-rotate": ["get", "bearing"],
                    "icon-rotation-alignment": "map",
                    "icon-anchor": "bottom",
                    "icon-allow-overlap": true,
                    "icon-ignore-placement": true,
                    // Clip-continuation arrows now use the same
                    // arrowhead-with-notch shape as on-trail direction
                    // arrows (drawArrow in this file) so the visual
                    // vocabulary stays consistent. The SDF asset's
                    // gradient (radius=2/3) leaves room for a thin
                    // halo without eroding too much of the body.
                    // Sized so the visible filled arrowhead reads
                    // slightly larger than an on-trail direction
                    // arrow — clip-continuation indicators benefit
                    // from extra visual weight since they signal
                    // "trail leaves the map" rather than ongoing
                    // direction.
                    "icon-size": ["interpolate", ["linear"], ["zoom"],
                        12, 1.2, 14, 1.65, 18, 2.4],
                    // Push the arrowhead away from the trail's
                    // clipped end so it doesn't crowd the line where
                    // it meets the bbox edge. Offset is in pre-
                    // rotation icon space (Y is "up" in the asset's
                    // tip-up frame), then rotates with the bearing —
                    // so the arrow ends up shifted "outward" along
                    // the trail's continuation direction. Scales with
                    // icon-size.
                    "icon-offset": [0, -2],
                },
                paint: {
                    "icon-color": iconCol,
                    "icon-opacity": 1.0,
                    // Thin contrasting outline for visual definition
                    // against busy basemap/terrain backgrounds, and
                    // (more critically) so dark-colored routes have a
                    // light edge instead of disappearing into similar
                    // dark surroundings. Halo width is in logical
                    // pixels and renders within the SDF's gradient
                    // zone outside the filled body.
                    "icon-halo-color": haloCol,
                    "icon-halo-width": 1.2,
                    "icon-halo-blur": 0,
                },
            });
        }
    }

    // ----- Highlight layers (above trail fills, below labels + arrows) -----
    // Two layers per highlight kind (route / trail): outline + stroke.
    // Rendered above the fills so they read as a "highlighted ribbon",
    // but below labels + difficulty + arrows so those stay readable /
    // visible when a route or trail is highlighted. All start with a
    // no-match filter; highlightRoute()/highlightTrail() swap in the
    // real filter and set the dynamic colour.
    //
    // History: this used to be a four-layer sandwich (outline, blurred
    // glow, stroke, white core). The glow was removed first — its
    // additive alpha created bright spikes at sharp switchback bends.
    // The white core was dropped when the route stroke switched to the
    // route's NATIVE colour (was amber) — with native colour, the white
    // core blurred into light-coloured routes (yellow, cream) and its
    // zoom-dependent width meant the inner-stripe effect was visible
    // only at high zoom. The remaining black-outline + colour-stroke
    // pair gets the structural emphasis from sheer thickness (~2× the
    // unhighlighted fill width) plus the always-on black silhouette
    // against any basemap; the spotlight dim (mapDimOnHighlight) does
    // the rest of the visibility work by receding everything else.
    const NONE_FILTER_ROUTE = ["==", ["get", "route_id"], "___NONE___"];
    const NONE_FILTER_TRAIL = ["==", ["get", "trail_name"], "___NONE___"];

    // Two-layer route highlight (bottom → top):
    //   outline:  thick silhouette around the highlight ribbon. Colour
    //             is bidirectional (black for light routes, white for
    //             dark) — set by highlightRoute() to mirror the
    //             unhighlighted casing's edge direction so the route's
    //             "edge" reads consistently in both states. Initial
    //             #000 here is a fail-safe; real highlights overwrite
    //             on every selection.
    //   stroke:   opaque, painted with the route's native colour by
    //             highlightRoute(). Width ~2× the unhighlighted fill so
    //             the highlight reads as "this route, scaled up."
    // line-color-transition: { duration: 0 } on every highlight layer.
    // MapLibre's default line-color transition is 300ms; without
    // overriding it, every setPaintProperty('line-color', ...) on a
    // highlight layer animates from its previous value to the new
    // one over 300ms. When the filter then activates the layer, the
    // user sees the color mid-animation — that's the "flash" from
    // amber (or the previous route's color) to the target. Setting
    // duration: 0 makes the colour change instantaneous, so by the
    // time the filter exposes the layer it's already at the target.
    map.addLayer({
        id: "route-highlight-outline",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_ROUTE,
        paint: {
            "line-color": "#000",
            "line-color-transition": { duration: 0 },
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 14, 11, 18, 18],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    map.addLayer({
        id: "route-highlight-stroke",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_ROUTE,
        paint: {
            // Initial fill is amber as a fail-safe in case the dynamic
            // setPaintProperty in highlightRoute() never fires (e.g.,
            // a CONFIG.routes lookup miss). Real highlights overwrite
            // this with effectiveRouteColor(info) on every selection.
            "line-color": "#ffb700",
            "line-color-transition": { duration: 0 },
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 18, 13],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    // Two-layer trail highlight (bottom → top):
    //   outline:  thick black (silhouette)
    //   stroke:   highlighter yellow (#FFEC00) — see highlightTrail().
    //             Trails span multiple routes so they have no native
    //             colour; the highlighter yellow is the framework's
    //             "no colour of its own" emphasis state.
    map.addLayer({
        id: "trail-highlight-outline",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_TRAIL,
        paint: {
            "line-color": "#000",
            "line-color-transition": { duration: 0 },
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 14, 11, 18, 18],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    map.addLayer({
        id: "trail-highlight-stroke",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_TRAIL,
        paint: {
            "line-color": "#FFEC00",
            "line-color-transition": { duration: 0 },
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 18, 13],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });

    // Route-name labels — one layer per route so each follows its
    // own offset line. Only rendered when labelMode === "routes";
    // trail-name labels render from the trail-decorations source via
    // decor-trail-name so each trail name appears exactly once per
    // physical way regardless of how many routes share it.
    for (let li = 0; li < sortedRoutes.length; li++) {
        const [routeId] = sortedRoutes[li];

        // Stagger labels along the line so shared-segment route names
        // don't stack up. With symbol-placement "line", text-offset x
        // shifts along the line direction (in ems).
        const stagger = (li % 4) * 4;   // 0, 4, 8, 12 ems

        map.addLayer({
            id: `trail-label-${routeId}`,
            type: "symbol",
            source: "trails-labels",
            filter: ["==", ["get", "route_id"], routeId],
            layout: {
                "symbol-placement": "line",
                "text-field": ["get", "route_name"],
                "text-font": ["Noto Sans Regular"],
                "text-size": [
                    "interpolate", ["linear"], ["zoom"],
                    10, 10,
                    14, 13,
                    18, 16,
                ],
                "text-max-angle": 45,
                "text-allow-overlap": false,
                "text-ignore-placement": false,
                // Small buffer on the label itself — it doesn't take
                // much to repel a later symbol because the later
                // symbol's own padding does most of the work. Going
                // higher than ~3 noticeably thins out labels on dense
                // maps without a proportionate overlap-prevention win.
                "text-padding": 3,
                "text-optional": true,
                "symbol-spacing": 150,
                "text-offset": [stagger, 0],
                "visibility": labelMode === "routes" ? "visible" : "none",
            },
            paint: {
                "text-color": "#1a1a1a",
                "text-halo-color": "rgba(255,255,255,0.9)",
                "text-halo-width": 2,
            },
        });
    }

    // Decoration layers — IMBA diamonds and direction arrows are
    // pre-deconflicted Point features (rendered with icon-allow-overlap
    // so placements computed in JS are exactly what's drawn). Trail and
    // route name labels are LineString features with symbol-placement:
    // line so text follows the trail curve; their per-tile collision
    // (text-allow-overlap: false) handles label-vs-label overlap.
    if (CONFIG.showDifficulty) {
        registerDifficultyIcons();
    }
    registerArrowIcons();
    addDecorationLayers();
}

// Build a filter expression for a label layer on the trail-decorations
// source. Always includes the KIND/zoom gate (so the layer only renders
// features of the right kind, at zooms ≥ the feature's min_zoom);
// callers append any highlight-narrowing filters as `extraFilters`.
//
// Centralising the gate prevents the three label-layer blocks below
// from drifting in their kind/zoom logic — every label feature is
// gated through this one place.
function buildLabelFilter(kind, ...extraFilters) {
    return ["all",
        ["==", ["get", "kind"], kind],
        ["<=", ["get", "min_zoom"], ["zoom"]],
        ...extraFilters,
    ];
}

function updateLabels() {
    const dim = highlightDimActive();

    // Per-route route-name label layers — each scoped to one route via
    // its baseline filter (set at layer creation, not here). Source
    // data (computeLabelData) only carries shared-way features now;
    // solo-way labels live on the decor-route-name layer.
    // Visible only in "routes" mode; trail highlights hide them all
    // (route names don't correspond to a particular trail), and route
    // highlights keep only the matching layer.
    for (const routeId of Object.keys(CONFIG.routes)) {
        const layerId = `trail-label-${routeId}`;
        if (!map.getLayer(layerId)) continue;

        let visible = labelMode === "routes" && visibleRoutes.has(routeId);
        if (visible && dim) {
            visible = highlight.kind === "route" && routeId === highlight.key;
        }
        map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    }

    // Solo-way route-name labels (one LineString per way with exactly
    // one visible route, labelled with that route's name). Visible
    // only in "routes" mode. Under dim: route highlight narrows to
    // that route via solo_route_id; trail highlight hides all route
    // names (same semantics as per-route layers above).
    if (map.getLayer("decor-route-name")) {
        let visible = labelMode === "routes";
        let filter = buildLabelFilter(KIND.ROUTE_NAME);
        if (visible && dim) {
            if (highlight.kind === "route") {
                filter = buildLabelFilter(KIND.ROUTE_NAME,
                    ["==", ["get", "solo_route_id"], highlight.key]);
            } else {
                visible = false;
            }
        }
        map.setLayoutProperty("decor-route-name",
            "visibility", visible ? "visible" : "none");
        if (visible) map.setFilter("decor-route-name", filter);
    }

    // Trail-name labels (one per physical way). Visible only in
    // "trails" mode. Under dim: route highlight narrows to that
    // route's shared-ways via the shared_routes array; trail
    // highlight narrows to the matching trail_name.
    if (map.getLayer("decor-trail-name")) {
        const visible = labelMode === "trails";
        map.setLayoutProperty("decor-trail-name", "visibility",
            visible ? "visible" : "none");
        if (visible) {
            let filter;
            if (dim && highlight.kind === "route") {
                filter = buildLabelFilter(KIND.TRAIL_NAME,
                    ["in", highlight.key, ["get", "shared_routes"]]);
            } else if (dim && highlight.kind === "trail") {
                filter = buildLabelFilter(KIND.TRAIL_NAME,
                    ["==", ["get", "trail_name"], highlight.key]);
            } else {
                filter = buildLabelFilter(KIND.TRAIL_NAME);
            }
            map.setFilter("decor-trail-name", filter);
        }
    }
}

// ============================================================
// Geometry smoothing (Chaikin's corner-cutting algorithm)
// ============================================================
function smoothLine(coords, iterations = 2) {
    if (coords.length < 3) return coords;
    let pts = coords;
    for (let iter = 0; iter < iterations; iter++) {
        const smoothed = [pts[0]];
        for (let i = 0; i < pts.length - 1; i++) {
            const p0 = pts[i];
            const p1 = pts[i + 1];
            smoothed.push([
                0.75 * p0[0] + 0.25 * p1[0],
                0.75 * p0[1] + 0.25 * p1[1],
            ]);
            smoothed.push([
                0.25 * p0[0] + 0.75 * p1[0],
                0.25 * p0[1] + 0.75 * p1[1],
            ]);
        }
        smoothed.push(pts[pts.length - 1]);
        pts = smoothed;
    }
    return pts;
}

// ============================================================
// Geometric offset for label placement
// ============================================================
function offsetLineGeometry(coords, offsetPx) {
    if (coords.length < 2 || offsetPx === 0) return coords;

    const midLat = coords[Math.floor(coords.length / 2)][1];
    const latRad = midLat * Math.PI / 180;
    const metersPerDegLon = 111320 * Math.cos(latRad);
    const metersPerDegLat = 110540;

    // Convert pixel offset to meters (rough: 1px ≈ 0.5m at z14)
    const metersOffset = offsetPx * 0.5;

    const result = [];
    for (let i = 0; i < coords.length; i++) {
        let dx, dy;
        if (i === 0) {
            dx = coords[1][0] - coords[0][0];
            dy = coords[1][1] - coords[0][1];
        } else if (i === coords.length - 1) {
            dx = coords[i][0] - coords[i - 1][0];
            dy = coords[i][1] - coords[i - 1][1];
        } else {
            dx = coords[i + 1][0] - coords[i - 1][0];
            dy = coords[i + 1][1] - coords[i - 1][1];
        }

        const len = Math.sqrt(dx * dx + dy * dy);
        if (len === 0) {
            result.push(coords[i]);
            continue;
        }
        const nx = dy / len;
        const ny = -dx / len;

        result.push([
            coords[i][0] + nx * metersOffset / metersPerDegLon,
            coords[i][1] + ny * metersOffset / metersPerDegLat,
        ]);
    }
    return result;
}

// ============================================================
// Dynamic offset computation
// ============================================================
function computeOffsetsAndFilter() {
    if (!routesData) return { type: "FeatureCollection", features: [] };

    // Global rank for each route id (consistent across recomputes).
    // Using ROUTE_ID_COMPARE: numeric-aware, so OSM ints sort 1,2,10
    // and string custom ids fall in lexicographically.
    const allRouteIds = Object.keys(CONFIG.routes).slice().sort(ROUTE_ID_COMPARE);
    const globalRank = new Map();
    allRouteIds.forEach((id, i) => globalRank.set(id, i));

    const features = routesData.features.map((f) => {
        const props = f.properties;
        const routeId = props.route_id;
        const shared = props.shared_routes || [routeId];

        const visibleShared = shared
            .filter((id) => visibleRoutes.has(id))
            .sort((a, b) => globalRank.get(a) - globalRank.get(b));
        const visibleCount = visibleShared.length;
        const position = visibleShared.indexOf(routeId);

        let offsetIndex = 0;
        if (visibleCount > 1 && position >= 0) {
            offsetIndex = position - (visibleCount - 1) / 2;
        }

        let geometry = f.geometry;
        if (offsetIndex !== 0 && geometry.type === "LineString") {
            geometry = {
                ...geometry,
                coordinates: smoothLine(geometry.coordinates),
            };
        }

        return {
            ...f,
            geometry: geometry,
            properties: {
                ...props,
                offset_index: offsetIndex,
            },
        };
    });

    return { type: "FeatureCollection", features: features };
}

function computeLabelData() {
    if (!routesData) return { type: "FeatureCollection", features: [] };

    const allRouteIds = Object.keys(CONFIG.routes).slice().sort(ROUTE_ID_COMPARE);
    const globalRank = new Map();
    allRouteIds.forEach((id, i) => globalRank.set(id, i));

    const features = routesData.features
        .filter((f) => visibleRoutes.has(f.properties.route_id))
        .map((f) => {
            const props = f.properties;
            const routeId = props.route_id;
            const shared = props.shared_routes || [routeId];

            const visibleShared = shared
                .filter((id) => visibleRoutes.has(id))
                .sort((a, b) => globalRank.get(a) - globalRank.get(b));
            const visibleCount = visibleShared.length;
            const position = visibleShared.indexOf(routeId);

            let offsetIndex = 0;
            if (visibleCount > 1 && position >= 0) {
                offsetIndex = position - (visibleCount - 1) / 2;
            }

            let geometry = f.geometry;
            if (geometry.type === "LineString" && offsetIndex !== 0) {
                const offsetPx = offsetIndex * 5;
                geometry = {
                    ...geometry,
                    coordinates: offsetLineGeometry(geometry.coordinates, offsetPx),
                };
            }

            return {
                ...f,
                geometry: geometry,
                properties: { ...props, offset_index: offsetIndex, shared_count: visibleCount },
            };
        })
        // Drop solo ways — their route-name labels are emitted by the
        // decor-route-name layer on the trail-decorations source so
        // arrows / difficulty / trail names / route names all share
        // one deconflicted placement pass. Shared ways (>=2 visible
        // routes) still go here because each route variant needs its
        // own offset-displaced label feature; trail-decorations only
        // carries one route label per way.
        .filter((f) => f.properties.shared_count > 1);

    return { type: "FeatureCollection", features: features };
}

// Recompute `visible_count` on every clip-endpoint feature.
function recomputeClipEndpointVisibility() {
    if (!clipEndpointsData) return;
    const src = map.getSource("clip-endpoints");
    if (!src) return;
    for (const f of clipEndpointsData.features) {
        const ids = f.properties.route_ids || [];
        let n = 0;
        for (const rid of ids) {
            if (visibleRoutes.has(rid)) n++;
        }
        f.properties.visible_count = n;
    }
    src.setData(clipEndpointsData);
}

// ============================================================
// Bucket model — non-exclusive flags + season mode + emergency toggle
// ============================================================
function rebuildVisibleRoutesSet() {
    visibleRoutes.clear();
    for (const [id, info] of Object.entries(CONFIG.routes)) {
        if (seasonMode === "summer" && info.summer) {
            visibleRoutes.add(id);
            continue;
        }
        if (seasonMode === "winter" && info.winter) {
            visibleRoutes.add(id);
            continue;
        }
        if (emergencyOn && info.emergency) {
            visibleRoutes.add(id);
            continue;
        }
    }
}

function applyVisibilityChange() {
    rebuildVisibleRoutesSet();
    updateTrailDisplay();
    updateMarkerProximity();
    rebuildFinderList();
    // If the highlighted entity is no longer visible, clear it.
    if (highlight) {
        if (highlight.kind === "route" && !visibleRoutes.has(highlight.key)) {
            clearHighlight();
        }
        // For trails, defer to the trail index (a trail is "visible" if any
        // of its parent routes is in visibleRoutes).
        if (highlight.kind === "trail") {
            const t = trailIndex.find((x) => x.name === highlight.key);
            if (!t || !t.routeIds.some((rid) => visibleRoutes.has(rid))) {
                clearHighlight();
            }
        }
    }
}

function updateTrailDisplay() {
    const updated = computeOffsetsAndFilter();
    const source = map.getSource("trails");
    if (source) source.setData(updated);

    const labelSource = map.getSource("trails-labels");
    if (labelSource) labelSource.setData(computeLabelData());

    // Decorations (arrows, diamonds, trail/route name labels) are
    // pre-deconflicted Point features. Recompute on every visibility
    // change so ways whose only visible route just toggled off drop
    // out — and so the obstacle / way-length set the placer sees
    // matches the new state.
    updateDecorationsSource();

    for (const routeId of Object.keys(CONFIG.routes)) {
        const visible = visibleRoutes.has(routeId);
        const vis = visible ? "visible" : "none";
        for (const prefix of ["trail-casing-", "trail-fill-", "trail-fill2-",
                              "trail-fill-unrated-", "trail-label-",
                              "clip-arrow-"]) {
            const layerId = prefix + routeId;
            if (map.getLayer(layerId)) {
                map.setLayoutProperty(layerId, "visibility", vis);
            }
        }
    }

    recomputeClipEndpointVisibility();

    // applyDimState() rebuilds labels / decoration filters /
    // clip-arrow visibility in a single pass, respecting whether the
    // spotlight dim is currently active.
    applyDimState();
}

// ============================================================
// Highlight system
// ============================================================
// Layer groups — the dark outline + amber glow + amber stroke render
// as a single unit. All three layers take the same filter; only the
// stroke and glow take the dynamic colour (the outline stays dark).
// Two layers each (bottom → top): outline + stroke.
//   outline: always black, never recoloured — silhouettes against any
//            basemap/trail.
//   stroke:  recoloured per highlight via setPaintProperty. For routes
//            it takes the route's native colour (the chip + ribbon
//            agree on identity); for trails it takes the framework's
//            "highlighter yellow" #FFEC00 (trails span multiple routes
//            so they have no single native colour to inherit).
//
// History: an earlier four-layer sandwich (outline + blurred glow +
// stroke + white core) lived here. Both the glow (bright spikes at
// switchbacks) and the white core (zoom-dependent visibility, blurred
// against light-coloured routes after the route-native-colour switch)
// have been removed. The current pair gets the "highlighted" read
// from sheer thickness vs. the unhighlighted fill plus the always-on
// black silhouette; the spotlight dim does the rest.
const ROUTE_HIGHLIGHT_LAYERS = [
    "route-highlight-outline",
    "route-highlight-stroke",
];
const TRAIL_HIGHLIGHT_LAYERS = [
    "trail-highlight-outline",
    "trail-highlight-stroke",
];
const ROUTE_TINTED_HIGHLIGHT_LAYERS = [
    "route-highlight-stroke",
];
const TRAIL_TINTED_HIGHLIGHT_LAYERS = [
    "trail-highlight-stroke",
];
const ROUTE_NONE_FILTER = ["==", ["get", "route_id"], "___NONE___"];
const TRAIL_NONE_FILTER = ["==", ["get", "trail_name"], "___NONE___"];

// ============================================================
// Spotlight dim
// ============================================================
// Gated behind `CONFIG.mapDimOnHighlight` (per-map YAML, default ON —
// opt out with `map_dim_on_highlight: false`). When active,
// highlighting a route or trail dims the rest of the map:
//   - The `dim-tint` background layer comes on, blackening the basemap
//     + non-highlighted trail casings/fills.
//   - Labels, difficulty icons, one-way arrows, and clip-arrows are
//     narrowed to the highlighted route/trail only (so they don't punch
//     through the tint on other lines).
//   - POI markers (DOM overlay, above the WebGL canvas) fade via the
//     `body.map-dim-active` class in style.css.
// Clearing the highlight — or toggling the config off — restores normal
// visibility in one pass via applyDimState().

function highlightDimActive() {
    // Default ON — opt out with `map_dim_on_highlight: false` in YAML.
    // The explicit `!== false` check keeps a missing CONFIG key (e.g., an
    // older bundle) dimming too, rather than silently disabling the
    // effect.
    return CONFIG.mapDimOnHighlight !== false && highlight != null;
}

// Per-route clip-arrow visibility. Clip-arrow layers are route-scoped
// (one layer per route), so this is straight visibility toggling.
// Trail highlights hide every clip-arrow since they're a route-level
// concept.
function updateClipArrowsDim() {
    const dim = highlightDimActive();
    for (const routeId of Object.keys(CONFIG.routes)) {
        const layerId = `clip-arrow-${routeId}`;
        if (!map.getLayer(layerId)) continue;
        // Baseline: clip-arrows follow the route's bucket visibility.
        const routeVisible = visibleRoutes.has(routeId);
        let vis = routeVisible;
        if (vis && dim) {
            vis = highlight.kind === "route" && routeId === highlight.key;
        }
        map.setLayoutProperty(layerId, "visibility", vis ? "visible" : "none");
    }
}

// One-shot sync of everything that responds to dim + highlight state.
// Called from highlightRoute / highlightTrail / clearHighlight, and
// from updateTrailDisplay() so season/emergency toggles don't
// accidentally re-enable non-highlighted labels under an active dim.
function applyDimState() {
    const active = highlightDimActive();
    if (map.getLayer("dim-tint")) {
        map.setLayoutProperty("dim-tint", "visibility",
            active ? "visible" : "none");
    }
    updateLabels();
    updateDecorationsHighlight();
    updateClipArrowsDim();
    updateMarkerDimState();
}

function highlightRoute(routeId) {
    const info = CONFIG.routes[routeId];
    if (!info) return;
    highlight = { kind: "route", key: routeId };

    // Highlight the route in its OWN colour, not a non-native accent
    // colour. Routes have an identity (a chip swatch, a peek-icon
    // colour, OSM's `colour` tag); the highlight inherits that
    // identity by colouring the stroke with effectiveRouteColor().
    // Structural emphasis comes from the layered architecture: a
    // thick black outline beneath, the route-coloured stroke at ~2x
    // the unhighlighted fill width, and the spotlight dim receding
    // every other layer. Together they unmistakably signal "this
    // route is selected" without recolouring the route itself.
    const color = effectiveRouteColor(info);
    // Outline picks dark vs light by the route's luminance, mirroring
    // the bidirectional logic in casingColor / contrastingHaloColor —
    // so the highlighted ribbon's edge tracks the unhighlighted
    // casing's edge for the same route. Without this, a dark-coloured
    // route like Iron Ore Heritage Trail (`#65442D`) had a translucent
    // LIGHT casing in normal display but a SOLID BLACK outline once
    // highlighted, making the edge look darker on selection — the
    // opposite of what "highlighted" should communicate. Solid
    // (non-translucent) so the outline still silhouettes against any
    // basemap; just the colour direction follows the same threshold
    // as the rest of the route's edge styling.
    const outlineColor = colorLuminance(color) > CONTRAST_LUM_THRESHOLD
        ? "#000000"
        : "#ffffff";
    const routeFilter = ["==", ["get", "route_id"], routeId];
    // Set paint BEFORE flipping the filter. setFilter activates the
    // layer (or switches it to a new route's geometry); whatever
    // line-color is currently set paints for one frame before any
    // subsequent setPaintProperty takes effect. That produced a
    // visible "flash" of either the hardcoded fail-safe colour (first
    // highlight) or the previous route's colour (when switching
    // between routes) before the new colour landed. Setting paint
    // first means the filter activation already finds the right
    // colour in place. Same applies to the outline below.
    for (const layerId of ROUTE_TINTED_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setPaintProperty(layerId, "line-color", color);
        }
    }
    if (map.getLayer("route-highlight-outline")) {
        map.setPaintProperty("route-highlight-outline",
            "line-color", outlineColor);
    }
    for (const layerId of ROUTE_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, routeFilter);
        }
    }
    // Clear trail highlights (single-highlight invariant)
    for (const layerId of TRAIL_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, TRAIL_NONE_FILTER);
        }
    }

    // Fit bounds to the route
    fitToRouteOrTrail({ routeId });

    // Show chip with per-route stats. routeIndex carries the same
    // distance/elevation values as the Finder rows; routeStatsText
    // returns "" when neither stat is enabled or available, in which
    // case the chip just shows label + color (current behavior).
    // Chip and ribbon now share the same colour by construction.
    const indexEntry = routeIndex.find((r) => r.id === routeId);
    showHighlightChip({
        label: info.name,
        color,
        stats: indexEntry ? routeStatsText(indexEntry) : "",
    });

    // Spotlight dim (no-op unless CONFIG.mapDimOnHighlight is on)
    applyDimState();
}

function highlightTrail(trailName) {
    highlight = { kind: "trail", key: trailName };

    // Trails span multiple routes — no single native colour to
    // inherit. Use highlighter yellow (#FFEC00, Stabilo Boss territory)
    // as the framework's "no colour of its own" emphasis state. Reads
    // unmistakably as "selected" without claiming the trail belongs to
    // any one route.
    const highlighter = "#FFEC00";
    const trailFilter = ["==", ["get", "trail_name"], trailName];
    // Paint before filter — same flash-prevention pattern as
    // highlightRoute(). Less critical here since trail-highlight-stroke
    // is always #FFEC00 either way, but kept symmetric with the route
    // path for consistency and defensive against future changes.
    for (const layerId of TRAIL_TINTED_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setPaintProperty(layerId, "line-color", highlighter);
        }
    }
    for (const layerId of TRAIL_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, trailFilter);
        }
    }
    // Clear route highlights
    for (const layerId of ROUTE_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, ROUTE_NONE_FILTER);
        }
    }

    fitToRouteOrTrail({ trailName });
    showHighlightChip({ label: trailName, color: highlighter });

    // Spotlight dim (no-op unless CONFIG.mapDimOnHighlight is on)
    applyDimState();
}

function clearHighlight() {
    highlight = null;
    for (const layerId of ROUTE_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, ROUTE_NONE_FILTER);
        }
    }
    for (const layerId of TRAIL_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, TRAIL_NONE_FILTER);
        }
    }
    hideHighlightChip();

    // Dim follows highlight lifecycle — tear it down here.
    applyDimState();
}

function fitToRouteOrTrail({ routeId, trailName }) {
    if (!routesData) return;
    let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
    let hasCoords = false;

    for (const f of routesData.features) {
        const props = f.properties;
        let match = false;
        if (routeId !== undefined) {
            const shared = props.shared_routes || [props.route_id];
            match = shared.includes(routeId);
        } else if (trailName !== undefined) {
            match = props.trail_name === trailName;
        }
        if (!match) continue;

        const coords = f.geometry.type === "LineString"
            ? f.geometry.coordinates
            : f.geometry.coordinates.flat();
        for (const [lng, lat] of coords) {
            if (lng < minLng) minLng = lng;
            if (lng > maxLng) maxLng = lng;
            if (lat < minLat) minLat = lat;
            if (lat > maxLat) maxLat = lat;
            hasCoords = true;
        }
    }

    if (!hasCoords) return;
    map.fitBounds(
        [[minLng, minLat], [maxLng, maxLat]],
        { padding: 60, duration: 500, maxZoom: 16 }
    );
}

function showHighlightChip({ label, color, stats }) {
    const chip = document.getElementById("highlight-chip");
    if (!chip) return;
    const swatch = chip.querySelector(".highlight-chip-swatch");
    const labelEl = chip.querySelector(".highlight-chip-label");
    const statsEl = chip.querySelector(".highlight-chip-stats");
    if (swatch) swatch.style.background = color;
    if (labelEl) labelEl.textContent = label;
    // stats is the pre-formatted "8.2 mi · 410 ft ↑" text from
    // routeStatsText() — empty string or missing means hide the span
    // entirely. Trail highlights pass nothing (per-route stats don't
    // apply to trail segments since trail names span multiple routes).
    if (statsEl) {
        if (stats) {
            statsEl.textContent = stats;
            statsEl.classList.remove("hidden");
        } else {
            statsEl.textContent = "";
            statsEl.classList.add("hidden");
        }
    }
    chip.classList.remove("hidden");
}

function hideHighlightChip() {
    const chip = document.getElementById("highlight-chip");
    if (chip) chip.classList.add("hidden");
}

// ============================================================
// Route + trail indexes for the finder
// ============================================================
function buildRouteIndex() {
    routeIndex = [];
    for (const [id, info] of Object.entries(CONFIG.routes)) {
        routeIndex.push({
            id,
            name: info.name,
            color: effectiveRouteColor(info),
            summer: !!info.summer,
            winter: !!info.winter,
            emergency: !!info.emergency,
            isCustom: !!info.isCustom,
            // Per-route stats from compute_route_stats.py. Any may
            // be absent: distance is gated by show_route_distance,
            // elevation by show_route_elevation + a successful
            // USGS 3DEP fetch at build time. Gain and loss are
            // computed in the same pass, so they're either both
            // present or both absent. Stored as integer meters in
            // CONFIG.routes; render-time formatting uses
            // formatDistance / formatElevationPair which respect
            // CONFIG.distanceUnits.
            distanceM: typeof info.distance_m === "number" ? info.distance_m : null,
            elevationGainM: typeof info.elevation_gain_m === "number" ? info.elevation_gain_m : null,
            elevationLossM: typeof info.elevation_loss_m === "number" ? info.elevation_loss_m : null,
        });
    }
    // Sort alphabetically by name (case-insensitive) for the list display.
    routeIndex.sort((a, b) => a.name.localeCompare(b.name));
}

function buildTrailIndex() {
    if (!routesData) { trailIndex = []; return; }
    const byName = new Map();  // name → Set<routeId>
    for (const f of routesData.features) {
        const name = f.properties.trail_name;
        if (!name) continue;
        let routeIds = byName.get(name);
        if (!routeIds) {
            routeIds = new Set();
            byName.set(name, routeIds);
        }
        const shared = f.properties.shared_routes || [f.properties.route_id];
        for (const rid of shared) routeIds.add(rid);
    }
    trailIndex = [...byName.entries()]
        .map(([name, routeIds]) => ({ name, routeIds: [...routeIds] }))
        .sort((a, b) => a.name.localeCompare(b.name));
}

// ============================================================
// POI loading
// ============================================================
async function loadPOIs() {
    // pois.geojson is optional in spirit — if it fails, the map still
    // works without POI markers. Fall back to an empty collection and
    // toast a warning; downstream count-based gating (`hasTrailMarkers`,
    // `hasParking`, etc.) auto-hides the relevant peek buttons.
    try {
        const resp = await fetch("pois.geojson");
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
        }
        poisData = await resp.json();
    } catch (e) {
        console.warn("loadPOIs: pois.geojson failed to load:", e);
        showToast("POI data failed to load — markers won't show.");
        poisData = { type: "FeatureCollection", features: [] };
    }

    // Count features by type in a single pass. Trail markers merge
    // guideposts + emergency access points into one category.
    const poiCounts = {
        [POI.TRAIL_MARKER]: 0,
        [POI.PARKING]: 0,
        [POI.TRAILHEAD]: 0,
        [POI.FEATURE]: 0,
    };
    for (const f of poisData.features) {
        const t = f.properties.poi_type;
        if (t in poiCounts) poiCounts[t]++;
    }
    const tmCount = poiCounts[POI.TRAIL_MARKER];
    const pkCount = poiCounts[POI.PARKING];
    const thCount = poiCounts[POI.TRAILHEAD];
    const ftCount = poiCounts[POI.FEATURE];

    // Read persisted toggle state (default on for everything that has data).
    const mkDefault = LS.get("mtb.poi.markers", true);
    const pkDefault = LS.get("mtb.poi.parking", true);
    const thDefault = LS.get("mtb.poi.trailheads", true);
    const ftDefault = LS.get("mtb.poi.features", true);

    // Hide a peek-row button when its layer has no data.
    const hidePeekBtn = (id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add("hidden");
    };

    // Trail markers — merged guideposts + emergency-access layer. The
    // button is shown whenever the layer has any data.
    if (CONFIG.showMarkers && tmCount > 0) {
        addTrailMarkers(mkDefault);
    } else {
        hidePeekBtn("toggle-markers");
    }

    if (CONFIG.showParking && pkCount > 0) {
        addParkingMarkers(pkDefault);
    } else {
        hidePeekBtn("toggle-parking");
    }

    if (CONFIG.showTrailheads && thCount > 0) {
        addTrailheadMarkers(thDefault);
    } else {
        hidePeekBtn("toggle-trailheads");
    }

    // Features are gated by both data-presence AND proximity: a build
    // can emit `tourism=attraction` POIs that all sit > POI_PROXIMITY_METERS
    // off the trail (Shelden's "Shelden Estate Wall" / "Old Tennis Court"
    // are ~12 m and ~33 m off, respectively), in which case the runtime
    // proximity filter hides every marker — the toggle would just be a
    // dead control. We always create the markers so they can pop in if
    // a route change later brings them into scope; updateFeatureButtonVisibility()
    // is the source of truth for whether the button shows.
    if (CONFIG.showFeatures && ftCount > 0) {
        addFeatureMarkers(ftDefault);
    }
    updateFeatureButtonVisibility();
}

// ============================================================
// POI marker helpers
// ============================================================
const isSafari = /Safari/.test(navigator.userAgent) &&
    !/Chrome|CriOS|Chromium|Edg|Firefox|FxiOS|OPR/.test(navigator.userAgent);

function directionsLink(coords, directionsUrl) {
    if (directionsUrl) {
        return `<a class="popup-directions" href="${directionsUrl}" target="_blank" rel="noopener">Get Directions &rarr;</a>`;
    }
    const [lon, lat] = coords;
    const url = isSafari
        ? `https://maps.apple.com/?daddr=${lat},${lon}`
        : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
    return `<a class="popup-directions" href="${url}" target="_blank" rel="noopener">Get Directions &rarr;</a>`;
}

function createPoiMarkers({ poiType, className, markerStyle, labelFn, contentFn,
                            popupHtmlFn, popupMaxWidth, addToMap, targetArray }) {
    const features = poisData.features.filter((f) => f.properties.poi_type === poiType);
    for (const feature of features) {
        const coords = feature.geometry.coordinates;
        const props = feature.properties;

        const el = document.createElement("div");
        el.className = className;
        if (markerStyle) el.style.cssText = markerStyle;
        if (contentFn) {
            contentFn(el, props);
        } else {
            el.textContent = labelFn(props);
        }

        const marker = new maplibregl.Marker({ element: el }).setLngLat(coords);

        if (popupHtmlFn) {
            // focusAfterOpen: false — MapLibre's default is to move
            // keyboard focus into the first focusable element of the
            // popup ("Get Directions" link), which renders the
            // browser's native focus ring around it. We're a tap-
            // driven trail map, not a keyboard-navigated form, so the
            // ring is just visual noise. Skipping the auto-focus
            // leaves keyboard focus where the user left it (typically
            // on the marker element itself, or nowhere on touch).
            const popup = new maplibregl.Popup({
                offset: 14,
                maxWidth: popupMaxWidth,
                closeButton: false,
                focusAfterOpen: false,
            }).setHTML(popupHtmlFn(props, coords));
            marker.setPopup(popup);
        }

        if (addToMap) marker.addTo(map);
        targetArray.push(marker);
    }
    // The obstacles cache reads .getLngLat() / ._map from each marker;
    // adding/removing markers invalidates the snapshot.
    invalidateObstaclesCache();
}

// Single helper covering the merged trail-marker POI category — OSM
// guideposts and emergency-access points now render with the same
// style. Shown/hidden together via the "Markers" peek toggle.
function addTrailMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.TRAIL_MARKER,
        className: "trail-marker",
        markerStyle: `min-width:20px;height:20px;padding:0 4px;background:${CONFIG.markerColor};color:${CONFIG.markerTextColor};border-radius:4px;border:2px solid ${CONFIG.markerBorderColor};display:flex;align-items:center;justify-content:center;font-weight:700;font-size:11px;line-height:1;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,0.3);box-sizing:border-box;`,
        // Fall back to "#" when OSM carries neither ref nor name —
        // matches the peek-bar swatch, preserves the marker's physical
        // footprint (empty string would collapse it via min-width),
        // and signals "guidepost / trail marker" to the rider.
        labelFn: (p) => p.ref || p.name || "#",
        addToMap,
        targetArray: trailMarkerMarkers,
    });
}

function addParkingMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.PARKING,
        className: "parking-marker",
        markerStyle: `width:24px;height:24px;background:${CONFIG.parkingColor};color:${CONFIG.parkingTextColor};border-radius:4px;border:2px solid ${CONFIG.parkingBorderColor};display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,0.3);`,
        labelFn: () => "P",
        popupHtmlFn: (p, coords) => {
            let h = `<div class="popup-title">${p.name || "Parking"}</div>`;
            h += directionsLink(coords, p.directions_url);
            return h;
        },
        popupMaxWidth: "220px",
        addToMap,
        targetArray: parkingMarkers,
    });
}

function addTrailheadMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.TRAILHEAD,
        className: "trailhead-marker",
        markerStyle: `width:28px;height:24px;background:${CONFIG.trailheadColor};color:${CONFIG.trailheadTextColor};border-radius:4px;border:2px solid ${CONFIG.trailheadBorderColor};display:flex;align-items:center;justify-content:center;font-weight:700;font-size:11px;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,0.3);`,
        labelFn: () => "TH",
        popupHtmlFn: (p, coords) => {
            let h = `<div class="popup-title">${p.name || "Trailhead"}</div>`;
            h += directionsLink(coords, p.directions_url);
            return h;
        },
        popupMaxWidth: "220px",
        addToMap,
        targetArray: trailheadMarkers,
    });
}

// Feature marker fill — YAML-overridable via `feature_color`.
// Used by the on-map marker's inner dot (.feature-marker-icon).
// The peek-bar chip and any other references read the same hex
// via the --feature-color CSS custom property set at boot.
const FEATURE_COLOR = CONFIG.featureColor || "#8e44ad";

function addFeatureMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.FEATURE,
        className: "feature-marker",
        markerStyle: null,
        contentFn: (el, props) => {
            const icon = document.createElement("div");
            icon.className = "feature-marker-icon";
            icon.style.background = FEATURE_COLOR;
            el.appendChild(icon);
            if (props.name) {
                const label = document.createElement("div");
                label.className = "feature-marker-label";
                label.textContent = props.name;
                el.appendChild(label);
            }
        },
        addToMap,
        targetArray: featureMarkers,
    });
}

// ============================================================
// Bottom sheet — peek + expanded states; replaces the old top-right
// sandwich panel.
// ============================================================
function setupBottomSheet() {
    const sheet = document.getElementById("bottom-sheet");
    const handle = document.getElementById("sheet-handle");
    const peek = document.getElementById("sheet-peek");
    const expanded = document.getElementById("sheet-expanded");
    // The peek-title text is already set eagerly at script-init time
    // (see setPeekTitleEarly near the top of this file) so no flash
    // before map load. The whole bottom-sheet starts visibility:hidden
    // in the HTML and is revealed at the end of this function.

    // ----- Open/close behaviour -----
    // The backdrop is a separate sibling element (sheet-backdrop) that
    // dims the map behind the open drawer. Its .is-active class
    // toggles in lockstep with the sheet's .is-open so the visual
    // state stays in sync. tap-to-close on the backdrop works via
    // the existing document-level "click outside the sheet" handler
    // further down in this function.
    const backdrop = document.getElementById("sheet-backdrop");
    function openSheet() {
        sheet.classList.add("is-open");
        handle.setAttribute("aria-expanded", "true");
        expanded.setAttribute("aria-hidden", "false");
        if (backdrop) backdrop.classList.add("is-active");
    }
    function closeSheet() {
        sheet.classList.remove("is-open");
        handle.setAttribute("aria-expanded", "false");
        expanded.setAttribute("aria-hidden", "true");
        if (backdrop) backdrop.classList.remove("is-active");
    }
    function toggleSheet() {
        if (sheet.classList.contains("is-open")) closeSheet();
        else openSheet();
    }

    // Expose openSheet/closeSheet so other call sites that live outside
    // this function's scope (the finder row clicks, the peek search
    // button) can trigger the drawer through the canonical paths
    // instead of toggling .is-open directly. Toggling the class
    // directly bypasses backdrop sync — backdrop stays .is-active,
    // intercepts all map gestures, and the user can't pan/zoom.
    window.__openBottomSheet = openSheet;
    window.__closeBottomSheet = closeSheet;

    // ----- Drag-to-open gesture -------------------------------------
    //
    // Pointer drags on the handle (or bare peek area) translate the
    // sheet in real time. At pointerup we decide: tap → toggle; else
    // snap based on final position + velocity. The snap transition is
    // handled by the existing CSS animation on max-height (except when
    // the user prefers reduced motion — in that case the final state
    // is applied instantly).
    const TAP_MAX_DIST = 6;        // px
    const TAP_MAX_TIME = 250;      // ms
    const VELOCITY_SNAP = 300;     // px/s that forces open/close
    const OPEN_SNAP_RATIO = 0.40;  // fraction of travel that snaps open

    const reduceMotion = () =>
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let drag = null;

    function getOpenHeight() {
        // Mirror the CSS max-height the sheet animates to for .is-open:
        // 72vh on mobile, 80vh on desktop (see style.css
        // .bottom-sheet.is-open + the @media (min-width: 768px)
        // override). These values must match — if they drift, the
        // snap-to-open position overshoots or undershoots the final
        // CSS state.
        const desktop = window.matchMedia("(min-width: 768px)").matches;
        const cap = window.innerHeight * (desktop ? 0.80 : 0.72);
        return cap;
    }

    function peekHeight() {
        // Use the peek's real rendered height (includes the handle).
        const peekRect = peek.getBoundingClientRect();
        const handleRect = handle.getBoundingClientRect();
        return peekRect.height + handleRect.height;
    }

    // `captureEl` is the element that owns the pointer-capture + event
    // listeners for this drag. Using `e.target` is unreliable because
    // the target can be a descendant (text node / child span) that
    // doesn't stay in the document, breaking setPointerCapture on
    // Chrome Android + Firefox. Always capture on the listener element.
    function makeDragHandlers(captureEl) {
        function startDrag(e) {
            // Only track primary-button pointer events (left click / first
            // touch). Ignore drags started on interactive children (peek
            // icon buttons, form controls) — those should behave as native
            // clicks.
            if (e.button !== undefined && e.button !== 0) return;
            if (e.target.closest && e.target.closest(".peek-icon, select, input, a")) return;
            const wasOpen = sheet.classList.contains("is-open");
            drag = {
                startY: e.clientY,
                startT: performance.now(),
                lastY: e.clientY,
                lastT: performance.now(),
                wasOpen,
                peekH: peekHeight(),
                openH: getOpenHeight(),
                moved: false,
                draggingApplied: false,
                pointerId: e.pointerId,
                captureEl,
            };
            // Capture so moves keep firing even outside the element. Use
            // the *listener* element, not e.target — some browsers throw
            // NotFoundError when capturing on a child text run.
            //
            // Note: we do NOT enter "drag mode" (pin max-height, add
            // .is-dragging, expose .sheet-expanded) on pointerdown.
            // A pure click that never moves shouldn't visually flash
            // the drawer content, and on desktop the brief mid-drag
            // exposure can read as the peek "flashing wide" before
            // the open animation. Drag mode is deferred to the first
            // moveDrag past TAP_MAX_DIST. See moveDrag below.
            try { captureEl.setPointerCapture(e.pointerId); } catch (_) {}
        }

        function enterDragMode() {
            if (!drag || drag.draggingApplied) return;
            drag.draggingApplied = true;
            // Pin max-height to the current visual height BEFORE we
            // expose .sheet-expanded. Without this, adding
            // .is-dragging un-hides the expanded section and the
            // sheet grows to the default CSS cap (100vh - 60px) for
            // one frame before the first pointermove clips it. The
            // user sees a flash of "fully open then drag in"
            // (visible in Firefox in particular). Setting the inline
            // max-height in the same synchronous handler means the
            // browser never paints the un-clipped intermediate
            // state. Disabling transition keeps the snap-back at
            // pointerup from animating from this pinned value.
            sheet.style.transition = "none";
            sheet.style.maxHeight = (drag.wasOpen ? drag.openH : drag.peekH) + "px";
            // Expose .sheet-expanded during drag so max-height has
            // content to reveal. Without this, dragging from closed
            // state was a no-op: the CSS `.sheet-expanded { display:
            // none }` rule left the sheet at peek height regardless
            // of any inline max-height we set. Most visible in
            // Firefox, where the tap-fallback didn't always salvage
            // short drags. Clear in end/cancel.
            if (!drag.wasOpen) sheet.classList.add("is-dragging");
        }

        function moveDrag(e) {
            if (!drag || e.pointerId !== drag.pointerId) return;
            const dy = e.clientY - drag.startY;
            drag.lastY = e.clientY;
            drag.lastT = performance.now();
            if (Math.abs(dy) > TAP_MAX_DIST) {
                drag.moved = true;
                // First time we cross the tap threshold, actually enter
                // drag mode (pin max-height + expose expanded section).
                enterDragMode();
            }

            // No-op until the user has actually moved past the tap
            // threshold — leave the sheet at its CSS-resolved size so
            // a static click never flashes the drawer content.
            if (!drag.draggingApplied) return;

            // Derive a live height by applying the drag delta to whichever
            // state we started in. Negative dy (finger up) opens; positive
            // closes.
            const baseH = drag.wasOpen ? drag.openH : drag.peekH;
            let liveH = baseH - dy;
            if (liveH < drag.peekH) liveH = drag.peekH;
            if (liveH > drag.openH) liveH = drag.openH;

            // Pin max-height during drag; disable the CSS transition so
            // the sheet tracks the finger exactly.
            sheet.style.transition = "none";
            sheet.style.maxHeight = liveH + "px";
            // Prevent text-selection / scroll hijack on touch drags.
            if (drag.moved && e.cancelable) e.preventDefault();
        }

        function endDrag(e) {
            if (!drag || e.pointerId !== drag.pointerId) return;
            const dy = e.clientY - drag.startY;
            const dt = performance.now() - drag.startT;
            const vy = (e.clientY - drag.lastY) /
                Math.max(1, (performance.now() - drag.lastT)) * 1000; // px/s
            const travel = drag.openH - drag.peekH;

            // Clear the inline overrides before applying the final state so
            // the CSS transition can take over for the snap.
            sheet.style.transition = reduceMotion() ? "none" : "";
            sheet.style.maxHeight = "";
            sheet.classList.remove("is-dragging");

            try { drag.captureEl.releasePointerCapture(drag.pointerId); } catch (_) {}

            const wasOpen = drag.wasOpen;
            const moved = drag.moved;
            drag = null;

            // Tap → toggle. A pointerup that never crossed
            // TAP_MAX_DIST is a tap regardless of duration — desktop
            // mouse users sometimes click and hold for a beat before
            // releasing, and the previous TAP_MAX_TIME bound was
            // sending those held-clicks down the snap branch where
            // dy=0 always evaluated to closeSheet(). dt is no longer
            // an input to tap-vs-drag classification; only motion is.
            if (!moved && Math.abs(dy) < TAP_MAX_DIST) {
                toggleSheet();
                return;
            }

            // Velocity-driven decision first.
            if (vy < -VELOCITY_SNAP) { openSheet(); return; }
            if (vy >  VELOCITY_SNAP) { closeSheet(); return; }

            // Otherwise snap based on fraction of travel crossed.
            // When open and dragged down > (1-ratio) of travel → close.
            // When closed and dragged up >  ratio of travel → open.
            if (wasOpen) {
                if (dy > travel * (1 - OPEN_SNAP_RATIO)) closeSheet();
                else openSheet();
            } else {
                if (-dy > travel * OPEN_SNAP_RATIO) openSheet();
                else closeSheet();
            }
        }

        function cancelDrag(e) {
            if (!drag || (e && e.pointerId !== drag.pointerId)) return;
            sheet.style.transition = "";
            sheet.style.maxHeight = "";
            sheet.classList.remove("is-dragging");
            try { drag.captureEl.releasePointerCapture(drag.pointerId); } catch (_) {}
            drag = null;
        }

        return { startDrag, moveDrag, endDrag, cancelDrag };
    }

    // Attach drag listeners to both the handle and the peek row. Each
    // set of handlers captures on its own listener element so pointer
    // capture works reliably across Safari / Chrome / Firefox.
    for (const el of [handle, peek]) {
        const { startDrag, moveDrag, endDrag, cancelDrag } = makeDragHandlers(el);
        el.addEventListener("pointerdown", startDrag);
        el.addEventListener("pointermove", moveDrag);
        el.addEventListener("pointerup", endDrag);
        el.addEventListener("pointercancel", cancelDrag);
    }

    // Keyboard + non-drag fallbacks.
    handle.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleSheet();
        }
    });

    // When the About modal is up, the sheet's outside-click and
    // Escape handlers must stand down — About sits on top of the
    // sheet and owns the foreground. Without this guard, a backdrop
    // tap (or a keystroke that happens to be Escape) would reach
    // these document-level handlers and silently collapse the sheet
    // behind the modal.
    function isAboutOpen() {
        const aboutModal = document.getElementById("about-modal");
        return aboutModal && !aboutModal.classList.contains("hidden");
    }

    // Tap outside the sheet closes it (when expanded).
    document.addEventListener("click", (e) => {
        if (!sheet.classList.contains("is-open")) return;
        if (isAboutOpen()) return;
        if (sheet.contains(e.target)) return;
        // Don't close on chip or map control clicks.
        if (e.target.closest(".highlight-chip")) return;
        closeSheet();
    });

    // Escape = dismiss topmost state, one press at a time. Priority
    // order from topmost downward:
    //   1. About modal open → close About (sheet stays open behind).
    //   2. Sheet open → close sheet.
    //   3. Highlight active → clear highlight (same as tapping the
    //      highlight chip).
    // Consolidated in a single handler so we don't have two
    // document-level Escape listeners racing to close different
    // layers on the same keystroke.
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        if (isAboutOpen()) {
            closeAboutModal();
            return;
        }
        if (sheet.classList.contains("is-open")) {
            closeSheet();
        } else if (highlight) {
            clearHighlight();
        }
    });

    // ----- Season cycle button (peek) --------------------------------
    //
    // Single button that cycles summer ⇌ winter on tap. Hidden entirely
    // when the current map has no winter routes — a control that never
    // does anything is worse than no control.
    //
    // Icons are inline SVG rather than emoji (☀ / ❄). Platform emoji
    // rendering varies wildly — iOS draws a full-colour glyph, Android
    // a different one, Linux a monochrome system font, etc. — which
    // made the summer/winter swatch look subtly wrong on most devices.
    // A hand-drawn SVG renders identically everywhere and picks up
    // `color: white` from the swatch via `currentColor`.
    const SUN_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="8" cy="8" r="2.8" fill="currentColor"/><line x1="8" y1="0.75" x2="8" y2="2.5"/><line x1="8" y1="13.5" x2="8" y2="15.25"/><line x1="0.75" y1="8" x2="2.5" y2="8"/><line x1="13.5" y1="8" x2="15.25" y2="8"/><line x1="2.85" y1="2.85" x2="4.1" y2="4.1"/><line x1="11.9" y1="11.9" x2="13.15" y2="13.15"/><line x1="2.85" y1="13.15" x2="4.1" y2="11.9"/><line x1="11.9" y1="4.1" x2="13.15" y2="2.85"/></g></svg>';
    // Six-ray snowflake: three lines through the center. Tiny "V"
    // barbs at each tip give it the characteristic flake silhouette
    // rather than reading as a plain asterisk.
    const SNOW_SVG = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="1" x2="8" y2="15"/><line x1="1.94" y1="4.5" x2="14.06" y2="11.5"/><line x1="1.94" y1="11.5" x2="14.06" y2="4.5"/><path d="M 6.5 2.5 L 8 4 L 9.5 2.5"/><path d="M 6.5 13.5 L 8 12 L 9.5 13.5"/><path d="M 3.2 3.4 L 4.5 5.5 L 2.4 5.7"/><path d="M 12.8 12.6 L 11.5 10.5 L 13.6 10.3"/><path d="M 3.2 12.6 L 4.5 10.5 L 2.4 10.3"/><path d="M 12.8 3.4 L 11.5 5.5 L 13.6 5.7"/></g></svg>';
    const seasonField = document.getElementById("season-field");
    const seasonSwatch = seasonField && seasonField.querySelector(".season-swatch");
    const seasonButtons = seasonField
        ? Array.from(seasonField.querySelectorAll(".sheet-segmented-btn"))
        : [];
    // CONFIG.routes and CONFIG.customRoutes are objects keyed by route id.
    // A route participates in winter mode iff its info carries `winter: true`.
    const anyRouteHas = (flag) => {
        const check = (coll) => coll && Object.values(coll).some(
            (r) => r && typeof r === "object" && r[flag] === true);
        return check(CONFIG.routes) || check(CONFIG.customRoutes);
    };
    const hasWinter = anyRouteHas("winter");
    if (seasonField && hasWinter && seasonButtons.length) {
        const reflectSeason = () => {
            const isSummer = seasonMode === "summer";
            if (seasonSwatch) {
                seasonSwatch.innerHTML = isSummer ? SUN_SVG : SNOW_SVG;
                // Summer colour (warm forest green) lives in CSS; winter
                // is a cold slate-blue applied inline so the two palettes
                // read as distinct seasonal moods beyond the glyph.
                seasonSwatch.style.background = isSummer ? "" : "#3d6b9c";
            }
            for (const b of seasonButtons) {
                b.setAttribute("aria-checked",
                    b.dataset.value === seasonMode ? "true" : "false");
            }
        };
        for (const b of seasonButtons) {
            b.addEventListener("click", (e) => {
                e.stopPropagation();
                const next = b.dataset.value;
                if (next === seasonMode) return;
                seasonMode = next;
                LS.set("mtb.seasonMode", seasonMode);
                reflectSeason();
                applyVisibilityChange();
            });
        }
        reflectSeason();
    } else if (seasonField) {
        seasonField.classList.add("hidden");
        // Force summer regardless of persisted state so renders are silent.
        seasonMode = "summer";
    }

    // ----- Emergency Access toggle (single authority) ---------------
    //
    // Same .sheet-toggle-row format as the POI toggles — a button
    // that flips aria-pressed on click. Shown only when the current
    // map has at least one route with `emergency: true`.
    const emBtn = document.getElementById("toggle-emergency-routes");
    const hasEmergencyRoutes = anyRouteHas("emergency");
    if (emBtn && hasEmergencyRoutes) {
        emBtn.setAttribute("aria-pressed", emergencyOn ? "true" : "false");
        emBtn.classList.remove("hidden");
        emBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const next = emBtn.getAttribute("aria-pressed") !== "true";
            emBtn.setAttribute("aria-pressed", next ? "true" : "false");
            emergencyOn = next;
            LS.set("mtb.emergencyOn", emergencyOn);
            applyVisibilityChange();
        });
    } else {
        if (emBtn) emBtn.classList.add("hidden");
        // Force off — no route data to toggle.
        emergencyOn = false;
    }

    // ----- POI swatches ---------------------------------------------
    // All peek chip colours flow from YAML via CSS custom properties
    // on :root (see setPoiColorVars near the top of this file). The
    // matching CSS rules in style.css consume --marker-color /
    // --marker-text-color / --marker-border-color (and the parking /
    // trailhead equivalents). No JS-driven inline-style overrides
    // here — that was a legacy path that beat CSS-var specificity
    // and caused the Features chip "purple fill" bug earlier.
    //
    // The feature-swatch wrapper is intentionally transparent — the
    // on-map marker appearance (coloured dot + ring + drop shadow)
    // is rendered by .feature-swatch::before whose fill lives in CSS.

    // ----- Peek-row POI toggles (aria-pressed buttons) ---------------
    //
    // Each button carries on/off state via aria-pressed. The wirePeekToggle
    // helper reads persisted state, sets the initial pressed value, and
    // wires the click handler. Buttons already hidden (no data) are skipped.
    function wirePeekToggle(id, lsKey, defaultOn, onChange) {
        const btn = document.getElementById(id);
        if (!btn || btn.classList.contains("hidden")) return;
        const initial = LS.get(lsKey, defaultOn);
        btn.setAttribute("aria-pressed", initial ? "true" : "false");
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const next = btn.getAttribute("aria-pressed") !== "true";
            btn.setAttribute("aria-pressed", next ? "true" : "false");
            LS.set(lsKey, next);
            onChange(next);
        });
    }

    // Trail markers — merged guideposts + emergency access points.
    // Proximity-filtered, same as features. updateMarkerProximity()
    // already triggers a decoration recompute (markers are obstacles
    // for arrow/diamond placement); the off-branch needs to do the
    // same explicitly because it bypasses proximity entirely.
    wirePeekToggle("toggle-markers", "mtb.poi.markers", true, (on) => {
        if (on) {
            updateMarkerProximity();  // already invalidates cache
        } else {
            for (const m of trailMarkerMarkers) m.remove();
            invalidateObstaclesCache();
            updateDecorationsSource();
        }
    });

    // Features — proximity-filtered.
    wirePeekToggle("toggle-features", "mtb.poi.features", true, (on) => {
        if (on) {
            updateMarkerProximity();  // already invalidates cache
        } else {
            for (const m of featureMarkers) m.remove();
            invalidateObstaclesCache();
            updateDecorationsSource();
        }
    });

    // Parking / trailheads — always shown when on (no proximity
    // filter). Toggling either flips the obstacle set, so recompute
    // decorations after the marker visibility flips.
    wirePeekToggle("toggle-parking", "mtb.poi.parking", true, (on) => {
        for (const m of parkingMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
        invalidateObstaclesCache();
        updateDecorationsSource();
    });
    wirePeekToggle("toggle-trailheads", "mtb.poi.trailheads", true, (on) => {
        for (const m of trailheadMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
        invalidateObstaclesCache();
        updateDecorationsSource();
    });

    // Difficulty — drives the decor-diamond layer. Uses aria-pressed
    // semantics to match the other peek icons.
    const difficultyBtn = document.getElementById("toggle-difficulty");
    if (CONFIG.showDifficulty && difficultyBtn) {
        const initial = LS.get("mtb.difficulty", true);
        difficultyBtn.setAttribute("aria-pressed", initial ? "true" : "false");
        if (map.getLayer("decor-diamond")) {
            map.setLayoutProperty("decor-diamond", "visibility",
                initial ? "visible" : "none");
        }
        difficultyBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const next = difficultyBtn.getAttribute("aria-pressed") !== "true";
            difficultyBtn.setAttribute("aria-pressed", next ? "true" : "false");
            LS.set("mtb.difficulty", next);
            if (map.getLayer("decor-diamond")) {
                map.setLayoutProperty("decor-diamond", "visibility",
                    next ? "visible" : "none");
            }
        });
    } else if (difficultyBtn) {
        difficultyBtn.classList.add("hidden");
    }

    // ----- Show Routes / Show Trails gating -----
    //
    // Hide the Finder section when neither routes nor trails are
    // configured to show; drop Labels options that match disabled
    // sections; hide the whole Labels row when "None" would be the
    // only choice.
    const showRoutes = CONFIG.showRoutes !== false;
    const showTrails = CONFIG.showTrails !== false;

    const finderSection = document.getElementById("finder-section");
    if (finderSection && !showRoutes && !showTrails) {
        finderSection.classList.add("hidden");
    }

    // ----- Map options: labels + basemap -----
    // Labels are a segmented control (Routes / Trails / None) rather
    // than a <select>. Semantics: exactly one radio pressed at any
    // time; aria-checked drives the active paint.
    const labelField = document.getElementById("label-field");
    const labelGroup = document.getElementById("label-segmented");
    if (labelGroup) {
        // Drop buttons that map to disabled sections.
        if (!showRoutes) {
            const btn = labelGroup.querySelector('[data-value="routes"]');
            if (btn) btn.remove();
        }
        if (!showTrails) {
            const btn = labelGroup.querySelector('[data-value="trails"]');
            if (btn) btn.remove();
        }
        const buttons = Array.from(labelGroup.querySelectorAll(".sheet-segmented-btn"));
        const available = buttons.map((b) => b.dataset.value);
        // If both section flags are off, "None" is the only choice —
        // hide the whole row (and force labelMode off).
        if (!showRoutes && !showTrails) {
            if (labelField) labelField.classList.add("hidden");
            labelMode = "none";
        } else if (!available.includes(labelMode)) {
            // Persisted labelMode refers to a removed option; coerce
            // to the first available.
            labelMode = available[0] || "none";
            LS.set("mtb.labels", labelMode);
        }
        const syncPressed = () => {
            for (const b of buttons) {
                b.setAttribute("aria-checked",
                    b.dataset.value === labelMode ? "true" : "false");
            }
        };
        syncPressed();
        for (const b of buttons) {
            b.addEventListener("click", () => {
                labelMode = b.dataset.value;
                LS.set("mtb.labels", labelMode);
                syncPressed();
                updateLabels();
            });
        }
    }

    const basemapField = document.getElementById("basemap-field");
    const basemapSelect = document.getElementById("basemap-select");
    const baseLayers = CONFIG.baseLayers || [];
    // The basemap selector lives inside the "Map style" collapsible
    // accordion section. Keep both in sync — when there are no
    // configured base layers, hide the whole section.
    const styleSection = document.getElementById("section-style");
    if (basemapSelect && baseLayers.length > 0) {
        // Default first
        const defaultOpt = document.createElement("option");
        defaultOpt.value = "default";
        defaultOpt.textContent = "Default";
        basemapSelect.appendChild(defaultOpt);
        for (const layer of baseLayers) {
            const opt = document.createElement("option");
            opt.value = `custom:${layer.id}`;
            opt.textContent = layer.name;
            basemapSelect.appendChild(opt);
        }
        basemapSelect.value = basemapMode;
        if (basemapField) basemapField.classList.remove("hidden");
        if (styleSection) styleSection.classList.remove("hidden");
        basemapSelect.addEventListener("change", (e) => {
            basemapMode = e.target.value;
            rebuildBasemapLayers();
        });
    } else {
        if (basemapField) basemapField.classList.add("hidden");
        if (styleSection) styleSection.classList.add("hidden");
    }

    // ----- Accordion sections (collapsible drawer groups) -----
    // Each .sheet-section-collapsible has a header button that toggles
    // the .is-open class on the section. Click on the header (not the
    // body) flips state and updates aria-expanded for screen readers.
    // Default-open state is set in HTML via the .is-open class on the
    // section element; the header's aria-expanded mirrors it.
    const collapsibleSections = document.querySelectorAll(
        ".sheet-section-collapsible");
    for (const section of collapsibleSections) {
        const header = section.querySelector(".sheet-section-header");
        if (!header) continue;
        header.addEventListener("click", (e) => {
            e.stopPropagation();
            const isOpen = section.classList.toggle("is-open");
            header.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
        header.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                header.click();
            }
        });
    }

    // ----- Peek search button -----
    // Phase 1 stub: tapping the magnifying-glass opens the drawer
    // (exposing the existing Finder section). Phase 3 will replace
    // this with a full-screen search overlay that includes POI
    // results and type-filter chips.
    const searchBtn = document.getElementById("toggle-search");
    if (searchBtn) {
        searchBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            openSheet();
            // Move keyboard focus to the finder input for fast typing
            // on desktop. Mobile keyboards open automatically via
            // input.focus() when the input gets focus.
            const finderInput = document.getElementById("finder-input");
            if (finderInput) {
                // Defer focus until after the sheet's open transition
                // starts so the keyboard doesn't fight the slide-up.
                setTimeout(() => finderInput.focus(), 50);
            }
        });
    }

    // ----- Finder -----
    setupFinder();

    // ----- Share button (B.3) -----
    setupShareButton();

    // ----- Highlight chip -----
    const chip = document.getElementById("highlight-chip");
    if (chip) {
        chip.addEventListener("click", () => clearHighlight());
        chip.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " " || e.key === "Escape") {
                e.preventDefault();
                clearHighlight();
            }
        });
    }

    // ----- Initial render -----
    applyVisibilityChange();

    // The whole bottom sheet starts invisible (visibility: hidden in
    // HTML) so first paint never shows the "all 6 icons wide" state
    // that would then shrink as buttons hide based on data
    // availability + CONFIG gates. Now that every peek button has its
    // correct .hidden class, reveal the sheet in a single paint.
    if (sheet) sheet.style.visibility = "";

    // Publish the actual peek height as a CSS custom property so the
    // logo overlay and attribution can sit exactly above the peek on
    // mobile (where the peek spans full width) instead of a hard-
    // coded 72 px that stale-dated the peek before it grew a title,
    // labels, etc. Desktop (≥ 768 px) uses a different CSS rule that
    // tucks them in the corners regardless — see style.css. Refreshed
    // on resize/orientation for font-scale or device-rotation
    // changes. ResizeObserver would be more precise but the peek
    // doesn't resize except in response to these events.
    function publishPeekHeight() {
        if (!peek || !handle) return;
        const h = Math.round(
            peek.getBoundingClientRect().height +
            handle.getBoundingClientRect().height);
        document.documentElement.style.setProperty("--peek-height", h + "px");
    }
    publishPeekHeight();
    window.addEventListener("resize", publishPeekHeight);
    window.addEventListener("orientationchange", publishPeekHeight);

    // ----- Day-of-week recheck for direction schedules -----
    // Each arrow's rotation is baked into its feature properties at
    // decoration-build time, so a flip in `reverseRoutesToday` forces
    // a full source recompute (cheap — we already do it on every
    // visibility change).
    setInterval(() => {
        const next = todaysReverseRoutes();
        const changed = next.size !== reverseRoutesToday.size
            || [...next].some((id) => !reverseRoutesToday.has(id));
        if (changed) {
            reverseRoutesToday = next;
            updateDecorationsSource();
        }
    }, 5 * 60 * 1000);
}

// ============================================================
// Finder (routes + trails search)
// ============================================================
function setupFinder() {
    const input = document.getElementById("finder-input");
    if (!input) return;
    input.addEventListener("input", () => rebuildFinderList());
    rebuildFinderList();
}

function rebuildFinderList() {
    const list = document.getElementById("finder-list");
    const empty = document.getElementById("finder-empty");
    if (!list) return;

    const input = document.getElementById("finder-input");
    const query = (input && input.value || "").trim().toLowerCase();
    const showRoutes = CONFIG.showRoutes !== false;
    const showTrails = CONFIG.showTrails !== false;

    list.innerHTML = "";

    // If both sections are hidden the whole Finder section is hidden by
    // setupBottomSheet(); we still bail early in case this ever runs.
    if (!showRoutes && !showTrails) {
        if (empty) empty.classList.add("hidden");
        return;
    }

    // Filter routes to the visible set, then by query.
    const routes = routeIndex.filter((r) => visibleRoutes.has(r.id));
    const visibleRouteIds = new Set(routes.map((r) => r.id));
    const trails = trailIndex.filter((t) =>
        t.routeIds.some((rid) => visibleRouteIds.has(rid)));

    const matchedRoutes = query
        ? routes.filter((r) => r.name.toLowerCase().includes(query))
        : routes;
    const matchedTrails = query
        ? trails.filter((t) => t.name.toLowerCase().includes(query))
        : trails;

    const routeCount = showRoutes ? matchedRoutes.length : 0;
    const trailCount = showTrails ? matchedTrails.length : 0;

    if (routeCount === 0 && trailCount === 0) {
        if (empty) empty.classList.remove("hidden");
        return;
    }
    if (empty) empty.classList.add("hidden");

    if (showRoutes && matchedRoutes.length > 0) {
        const h = document.createElement("div");
        h.className = "finder-section-header";
        h.textContent = "Routes";
        list.appendChild(h);
        for (const r of matchedRoutes) {
            list.appendChild(makeRouteRow(r));
        }
    }

    if (showTrails && matchedTrails.length > 0) {
        const h = document.createElement("div");
        h.className = "finder-section-header";
        h.textContent = "Trails";
        list.appendChild(h);
        for (const t of matchedTrails) {
            list.appendChild(makeTrailRow(t, visibleRouteIds));
        }
    }
}

// Build the stats string ("8.2 mi · ↑410 / ↓380 ft") for a route.
// Returns "" when neither stat is available (gates off, build
// couldn't fetch elevation, etc.) so the caller can decide to omit
// the stats span entirely. Distance and elevation are independent;
// gain and loss come together (computed in one pass).
function routeStatsText(r) {
    const parts = [];
    if (typeof r.distanceM === "number") {
        parts.push(formatDistance(r.distanceM));
    }
    const elev = formatElevationPair(r.elevationGainM, r.elevationLossM);
    if (elev) parts.push(elev);
    return parts.join(" · ");
}

function makeRouteRow(r) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "finder-row finder-row-route";
    row.setAttribute("role", "option");
    row.dataset.routeId = r.id;

    const swatch = document.createElement("span");
    swatch.className = "finder-row-swatch";
    swatch.style.background = r.color;
    swatch.setAttribute("aria-hidden", "true");

    const name = document.createElement("span");
    name.className = "finder-row-name";
    name.textContent = r.name;

    row.appendChild(swatch);
    row.appendChild(name);

    if (r.isCustom) {
        const tag = document.createElement("span");
        tag.className = "finder-row-tag";
        tag.textContent = "custom";
        row.appendChild(tag);
    }

    const stats = routeStatsText(r);
    if (stats) {
        const statsEl = document.createElement("span");
        statsEl.className = "finder-row-stats";
        statsEl.textContent = stats;
        row.appendChild(statsEl);
    }

    row.addEventListener("click", () => {
        highlightRoute(r.id);
        if (window.__closeBottomSheet) window.__closeBottomSheet();
    });

    return row;
}

function makeTrailRow(t, visibleRouteIds) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "finder-row finder-row-trail";
    row.setAttribute("role", "option");
    row.dataset.trailName = t.name;

    const mark = document.createElement("span");
    mark.className = "finder-row-trail-mark";
    mark.setAttribute("aria-hidden", "true");
    row.appendChild(mark);

    const name = document.createElement("span");
    name.className = "finder-row-name";
    name.textContent = t.name;
    row.appendChild(name);

    // Parent route names (only those currently visible). Truncate to 2 plus
    // an "+N more" tail for readability.
    const parents = t.routeIds
        .filter((rid) => visibleRouteIds.has(rid))
        .map((rid) => CONFIG.routes[rid] && CONFIG.routes[rid].name)
        .filter(Boolean);
    if (parents.length > 0) {
        const meta = document.createElement("span");
        meta.className = "finder-row-meta";
        if (parents.length <= 2) {
            meta.textContent = parents.join(", ");
        } else {
            meta.textContent = `${parents[0]}, ${parents[1]} +${parents.length - 2} more`;
        }
        row.appendChild(meta);
    }

    row.addEventListener("click", () => {
        highlightTrail(t.name);
        if (window.__closeBottomSheet) window.__closeBottomSheet();
    });

    return row;
}

// ============================================================
// Trail interactions (hover, click)
// ============================================================
function setupInteractions() {
    let parkingPopupOpen = false;
    for (const marker of [...trailMarkerMarkers,
                          ...parkingMarkers, ...trailheadMarkers,
                          ...featureMarkers]) {
        marker.getElement().addEventListener("click", () => {
            parkingPopupOpen = true;
            requestAnimationFrame(() => { parkingPopupOpen = false; });
        });
    }

    function attachTrailHandlers(layerId) {
        map.on("mouseenter", layerId, () => {
            map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layerId, () => {
            map.getCanvas().style.cursor = "";
        });
        map.on("click", layerId, (e) => {
            if (parkingPopupOpen) return;
            const props = e.features[0].properties;
            const hasTrailName = !!props.trail_name;

            // shared_routes may be a JSON string or array depending on source
            let shared = props.shared_routes;
            if (typeof shared === "string") {
                try { shared = JSON.parse(shared); } catch (_) { shared = []; }
            }
            if (!Array.isArray(shared)) shared = [];

            const matchedRoutes = shared
                .map((id) => CONFIG.routes[id])
                .filter(Boolean);
            const routeItems = matchedRoutes
                .map((rel) => {
                    const color = effectiveRouteColor(rel);
                    return `<div class="popup-routes"><span style="color:${color};">\u25CF</span> ${rel.name}</div>`;
                })
                .join("");

            let html = "";
            if (hasTrailName) {
                html += `<div class="popup-title">Trail: ${props.trail_name}</div>`;
            }
            if (routeItems) {
                const label = matchedRoutes.length === 1
                    ? "Part of Route:"
                    : "Part of Routes:";
                if (hasTrailName) {
                    html += `<hr class="popup-hr">`;
                }
                html += `<div class="popup-routes">${label}</div>`;
                html += routeItems;
            }

            new maplibregl.Popup({
                maxWidth: "220px",
                closeButton: false,
                focusAfterOpen: false,
            })
                .setLngLat(e.lngLat)
                .setHTML(html)
                .addTo(map);
        });
    }

    // Per-route casing layers (wider hit target for easier tapping)
    for (const routeId of Object.keys(CONFIG.routes)) {
        attachTrailHandlers(`trail-casing-${routeId}`);
    }
}

// ============================================================
// Promote basemap labels above trail layers
// ============================================================
function promoteBasemapLabels() {
    const style = map.getStyle();
    const basemapSymbolIds = style.layers
        .filter((l) => l.source === "basemap" && l.type === "symbol")
        .map((l) => l.id);

    const firstTrailLayer = style.layers.find(
        (l) => l.id.startsWith("trail-casing-") || l.id === "hillshade"
    );
    const beforeId = firstTrailLayer ? firstTrailLayer.id : undefined;

    for (const id of basemapSymbolIds) {
        map.moveLayer(id, beforeId);
    }
}

function suppressPathLabels() {
    if (!CONFIG.suppressPathLabels) return;
    if (map.getLayer("roads_labels_minor")) {
        map.setFilter("roads_labels_minor", ["in", "kind", "minor_road"]);
    }
}

function suppressBasemapPois() {
    if (!CONFIG.suppressBasemapPois) return;
    if (map.getLayer("pois")) {
        map.setLayoutProperty("pois", "visibility", "none");
    }
}

// ============================================================
// Basemap rebuild — only when user picks a different basemap from the
// (optional) base_layers selector. Light theme only; no theme rebuilds.
// ============================================================
function rebuildBasemapLayers() {
    const base = getBaseUrl();
    const currentStyle = map.getStyle();

    // Collect non-basemap layers (trails, hillshade, highlights, etc.)
    const overlayLayers = currentStyle.layers.filter(
        (l) => l.source !== "basemap" && l.id !== "custom-raster" && l.type !== "background"
    );

    let baseLayers;
    let spritePath;

    if (isCustomLayer()) {
        const layer = getCustomLayer();
        const sourceConfig = {
            type: "raster",
            tiles: [layer.url],
            tileSize: layer.tile_size || 256,
            attribution: layer.attribution || "",
        };
        if (layer.max_zoom) sourceConfig.maxzoom = layer.max_zoom;

        const newSources = { basemap: sourceConfig };
        for (const [key, val] of Object.entries(currentStyle.sources)) {
            if (key !== "basemap") newSources[key] = val;
        }
        baseLayers = [{ id: "custom-raster", type: "raster", source: "basemap", paint: {} }];
        spritePath = `${base}sprites/v4/light`;

        const newStyle = {
            ...currentStyle,
            sources: newSources,
            sprite: spritePath,
            layers: [...baseLayers, ...overlayLayers],
        };
        map.setStyle(newStyle, { diff: true });
    } else {
        baseLayers = basemaps.layers("basemap", basemaps.namedFlavor("light"), { lang: "en" });
        spritePath = `${base}sprites/v4/light`;

        const newStyle = {
            ...currentStyle,
            sprite: spritePath,
            layers: [...baseLayers, ...overlayLayers],
        };
        map.setStyle(newStyle, { diff: true });
    }

    // Re-promote basemap labels above trail layers after style rebuild
    promoteBasemapLabels();
    suppressPathLabels();
    suppressBasemapPois();

    // Re-register difficulty/arrow icons if lost during style rebuild.
    // The decoration layers themselves come back via the serialised
    // style (setStyle preserves overlayLayers); icons need to be
    // re-uploaded if dropped.
    if (CONFIG.showDifficulty && !map.hasImage("imba-0")) {
        registerDifficultyIcons();
    }
    if (!map.hasImage(ARROW_ICON_ID)) {
        registerArrowIcons();
    }
}

// ============================================================
// PWA update toast (B.7) — shows an auto-dismissing toast with a
// "Reload" button when the service worker has a new version waiting.
// ============================================================
//
// PWAs in standalone mode have no built-in reload affordance — the
// user would otherwise have to swipe up from the app switcher and
// reopen, which is meaningful friction. The Reload button calls
// skipWaiting on the waiting SW (via postMessage) then reloads on
// the controllerchange event, all inside the existing standalone
// window. One tap, no app close/reopen.
//
// Design intentionally simple:
//   * No dismiss button.
//   * Toast auto-dismisses after ~15s if the user doesn't interact.
//   * No localStorage tracking of dismissals — every page load with
//     a waiting SW shows the toast fresh.
//
// Why no per-version dismissal: a user who ignores the toast still
// gets the update naturally on next app close+reopen (browser default
// SW lifecycle: waiting SW activates as soon as all old-SW clients
// close). So "ignore for now" is a valid path that doesn't strand
// them on a stale version. The toast just nudges them to update
// sooner if they want to without leaving the app.
//
// CACHE_VERSION (content-hashed at build time) ticks on any deploy —
// code, data, PMTiles, icons — so this fires for any rebuild.
if (CONFIG.pwa && "serviceWorker" in navigator) {
    let _hasShownUpdateToast = false;

    function showSwUpdateToast(reg) {
        // Suppress within the current page load only. A future page
        // load that finds a waiting SW will show the toast again —
        // there's no persistent dismissal flag.
        if (_hasShownUpdateToast) return;
        _hasShownUpdateToast = true;
        showToast("Updated map available.", {
            // 15s gives the user time to read and decide; long
            // enough that a quick glance away doesn't miss it,
            // short enough that the toast doesn't obstruct the
            // map for long if ignored.
            timeoutMs: 15000,
            actions: [{
                label: "Reload",
                primary: true,
                onClick: () => {
                    // Reload immediately on the next controllerchange
                    // (which fires when the waiting SW activates after
                    // skipWaiting). Use a one-shot listener so we
                    // don't double-reload if controllerchange fires
                    // again later.
                    let reloaded = false;
                    navigator.serviceWorker.addEventListener(
                        "controllerchange",
                        () => {
                            if (reloaded) return;
                            reloaded = true;
                            window.location.reload();
                        },
                        { once: true }
                    );
                    if (reg.waiting) {
                        reg.waiting.postMessage({ type: "SKIP_WAITING" });
                    } else {
                        // Edge case: registration.waiting cleared
                        // between toast-show and click. Just reload —
                        // the page will pick up whatever SW is
                        // active.
                        window.location.reload();
                    }
                },
            }],
        });
    }

    function watchForSwUpdate(reg) {
        // Case A: a waiting SW is already present at page load
        // (deploy happened while a different tab was open).
        if (reg.waiting && navigator.serviceWorker.controller) {
            showSwUpdateToast(reg);
        }
        // Case B: a new SW is discovered during this session.
        reg.addEventListener("updatefound", () => {
            const installing = reg.installing;
            if (!installing) return;
            installing.addEventListener("statechange", () => {
                // "installed" + an existing controller means this is
                // an UPDATE (not a first install). First installs
                // shouldn't show the reload toast — there's nothing
                // to reload from.
                if (installing.state === "installed"
                        && navigator.serviceWorker.controller) {
                    showSwUpdateToast(reg);
                }
            });
        });
    }

    // Re-register on load so we capture the registration that
    // index.html's inline script created (idempotent — same scope =
    // same registration object). Safer than racing the inline
    // script.
    navigator.serviceWorker.register("sw.js").then(watchForSwUpdate)
        .catch((e) => console.warn("SW update watcher: registration failed", e));
}

// ============================================================
// PWA Install — promoted out of About into a dedicated bottom-sheet row.
// Per-map opt-in via CONFIG.pwaInstallPrompt (default false). Two modes:
//
//   pwaInstallPrompt: false (default) — DO NOT register beforeinstallprompt
//     at all. This silences the Chrome console warning ("Banner not
//     shown: beforeinstallpromptevent.preventDefault() called...") that
//     fires when a handler is registered but never calls prompt().
//     Chrome still shows its native mini-infobar / omnibox install icon
//     on its own (browser behavior, not ours), and Chrome's three-dot
//     menu still has "Install app". Our custom Install button is hidden
//     everywhere — this is the "no install promotion on this map" mode.
//
//   pwaInstallPrompt: true (per-map opt-in) — show-both strategy:
//     * Register beforeinstallprompt and DO NOT preventDefault — let
//       Chrome show its native mini-infobar (transient, one-shot per
//       cooldown). Stash the event for our button.
//     * Always show our custom Install button on Chrome alongside
//       Chrome's native UI. Chrome's mini-infobar is one-shot; our
//       button is the persistent surface for second-visit installs.
//     * Tapping our button calls prompt() on the stashed event and
//       awaits userChoice. After it resolves (accepted OR dismissed),
//       the event is spent — disable the button. A future
//       beforeinstallprompt re-arms it.
//     * On accepted (or window 'appinstalled' event from Chrome's
//       native UI), persist mtb.installed=true and hide the button
//       permanently across reloads.
//     * iOS Safari (no beforeinstallprompt) shows the manual
//       Add-to-Home-Screen instructions instead of the prompt-button
//       path.
//
// CONFIG.pwa still gates the entire block — maps without PWA support
// at build time skip all of this.
// ============================================================
if (CONFIG.pwa && CONFIG.pwaInstallPrompt) {
    let deferredInstallPrompt = null;
    let installed = LS.get("mtb.installed") === "true"
        || window.matchMedia("(display-mode: standalone)").matches;

    function revealInstallSection(showButton, showHint) {
        const section = document.getElementById("sheet-install-section");
        const btn = document.getElementById("install-btn");
        const hint = document.getElementById("ios-install-hint");
        if (btn)  btn.classList.toggle("hidden", !showButton);
        if (hint) hint.classList.toggle("hidden", !showHint);
        if (section) {
            section.classList.toggle("hidden", !(showButton || showHint));
        }
    }

    function setInstallButtonEnabled(enabled) {
        const btn = document.getElementById("install-btn");
        if (!btn) return;
        btn.disabled = !enabled;
        btn.classList.toggle("is-disabled", !enabled);
    }

    if (!installed) {
        window.addEventListener("beforeinstallprompt", (e) => {
            // NOTE: deliberately NOT calling e.preventDefault(). That
            // lets Chrome's native mini-infobar appear (free, one-shot
            // install affordance). We also stash the event so our own
            // Install button can call prompt() on it later — Chrome
            // allows both UIs as long as we eventually call prompt()
            // (which silences the "page must call prompt()" warning).
            deferredInstallPrompt = e;
            revealInstallSection(true, false);
            setInstallButtonEnabled(true);
        });
    }

    // 'appinstalled' fires regardless of which UI triggered the install
    // (mini-infobar, omnibox icon, our button). Persist a flag so the
    // button stays hidden on subsequent page loads even before
    // display-mode:standalone takes effect.
    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        installed = true;
        LS.set("mtb.installed", "true");
        revealInstallSection(false, false);
    });

    document.addEventListener("DOMContentLoaded", () => {
        if (installed) {
            // Already installed (either via prior session or current
            // standalone display mode). Don't surface install UI at all.
            revealInstallSection(false, false);
            return;
        }

        const installBtn = document.getElementById("install-btn");
        if (installBtn) {
            installBtn.addEventListener("click", async () => {
                if (!deferredInstallPrompt) return;
                setInstallButtonEnabled(false);
                deferredInstallPrompt.prompt();
                const result = await deferredInstallPrompt.userChoice;
                if (result.outcome === "accepted") {
                    installed = true;
                    LS.set("mtb.installed", "true");
                    revealInstallSection(false, false);
                }
                // Either way, the BeforeInstallPromptEvent is
                // single-use. Discard it; if Chrome re-fires the event
                // later (its own heuristics) the listener above will
                // re-arm.
                deferredInstallPrompt = null;
            });
        }

        // iOS detection — show manual instructions since iOS Safari
        // lacks beforeinstallprompt. Only when the page is *not* already
        // a standalone PWA (otherwise the user already installed it).
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
        const isStandalone = window.matchMedia("(display-mode: standalone)").matches;
        if (isIOS && !isStandalone) {
            revealInstallSection(false, true);
        }
    });
}

// ============================================================
// Off-screen location indicator
// ============================================================
// Distance helper that takes [lng, lat] tuples instead of four scalars
// — the off-screen indicator code naturally has lngLat objects/arrays
// from MapLibre. Delegates to the canonical haversineMeters defined
// near the decoration system (Earth radius 6378137, WGS-84 equatorial).
function haversineDistance(lngLat1, lngLat2) {
    return haversineMeters(lngLat1[0], lngLat1[1],
                           lngLat2[0], lngLat2[1]);
}

// Single distance formatter shared across the whole runtime: off-screen
// indicator pill, Finder route rows, highlight chip, any future
// distance display. Underlying values are always meters; this function
// converts to whatever CONFIG.distanceUnits says ("mi" default, or
// "km" for metric-region maps).
//
//   "mi": feet under 0.5 mi (~2640 ft), then decimal mi up to 10, then
//         integer mi. Feet at the close range matches what off-screen-
//         indicator users in imperial regions expect.
//   "km": meters under 1000, then decimal km up to 10, then integer km.
function formatDistance(meters) {
    if (CONFIG.distanceUnits === "km") {
        if (meters < 1000) return `${Math.round(meters)} m`;
        const km = meters / 1000;
        return km < 10 ? `${km.toFixed(1)} km` : `${Math.round(km)} km`;
    }
    // default: mi
    const mi = meters / 1609.344;
    if (mi < 0.5) {
        return `${Math.round(meters * 3.28084)} ft`;
    }
    return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
}

// Elevation gain formatter — meters → ft for "mi" units, kept in m for
// "km" units. The mixed convention matches what riders in each system
// actually expect (US trail maps quote "412 ft of climbing"; European
// maps quote "126 m").
function formatElevation(meters) {
    if (CONFIG.distanceUnits === "km") {
        return `${Math.round(meters)} m`;
    }
    return `${Math.round(meters * 3.28084)} ft`;
}

// Compact paired gain/loss display for route stats. Either or both
// values may be null (unknown / not computed). Returns "" if neither
// is present so the caller can omit the elevation portion entirely.
//
// Format: "↑NNN / ↓NNN ft" — the unit is shared across both numbers
// rather than repeated, both for compactness and to make "this is one
// physical quantity, two facets of it" visually clear. Single-value
// case (e.g. only gain available) collapses to "↑NNN ft" naturally.
//
// Why both directions: for loops gain ≈ loss; for one-way routes the
// asymmetry is informative without us having to claim a riding
// direction (OSM doesn't tell us, and our segment-walk gives an
// arbitrary feature-order direction anyway). Showing both lets riders
// who know the trail interpret correctly.
function formatElevationPair(gainM, lossM) {
    const haveGain = typeof gainM === "number";
    const haveLoss = typeof lossM === "number";
    if (!haveGain && !haveLoss) return "";
    const isMetric = CONFIG.distanceUnits === "km";
    const unit = isMetric ? "m" : "ft";
    const conv = (m) => isMetric ? Math.round(m) : Math.round(m * 3.28084);
    const parts = [];
    if (haveGain) parts.push(`↑${conv(gainM)}`);
    if (haveLoss) parts.push(`↓${conv(lossM)}`);
    return `${parts.join(" / ")} ${unit}`;
}

// Wire the off-screen indicator's click. Called once from init() after
// the GeolocateControl is set up; the indicator element itself is
// pre-rendered in index.html so we can attach the listener at boot
// without waiting for it to first appear.
function attachOffScreenIndicatorHandler() {
    const el = document.getElementById("off-screen-indicator");
    if (!el) return;
    el.addEventListener("click", () => {
        if (!userLocation) return;

        // Dismiss any toast currently visible. Most likely it's the
        // "tap the blue ▲" hint from the prior Locate-button tap;
        // leaving it up while the user actually performs the action
        // reads as "still buggy". The 4-s auto-fade would otherwise
        // outlive the click that addressed it.
        dismissToast();

        // Suppress the "your location is off-screen" toast for any
        // geolocate / userlocationfocus events that fire while we're
        // panning. The flag is checked first in maybeShowOffScreenToast
        // and also consumes _showToastOnNextFix there to close the
        // race where a fresh GPS fix arrives after moveend clears
        // suppress. Cleared on moveend (and by a safety timeout).
        _suppressOffScreenToast = true;
        const safetyTimer = setTimeout(() => {
            _suppressOffScreenToast = false;
        }, 2000);

        // Pan to the cached fix. If userLocation sits at or beyond the
        // edge of panBbox, MapLibre clamps the map center to keep the
        // viewport inside maxBounds — the user ends up at the viewport
        // edge rather than dead centre. We detect that post-pan and
        // show a clearer one-shot toast explaining why.
        //
        // trigger() to resume active follow-me tracking is DEFERRED to
        // moveend (below). Calling it here in parallel with flyTo
        // would race the movestart handler: trigger()'s auto-pan
        // fires movestart-with-geolocateSource, the cancel handler
        // map.stop()s, and our flyTo dies as collateral. By the time
        // moveend fires, our flyTo has already completed; trigger()'s
        // initial fitBounds is then the no-op MapLibre wants (we're
        // already at the user) and the cancel handler harmlessly
        // suppresses it.
        map.once("moveend", () => {
            clearTimeout(safetyTimer);
            _suppressOffScreenToast = false;
            const pt = map.project(userLocation);
            const cv = map.getCanvas();
            const offScreenAfterPan =
                pt.x < 0 || pt.x > cv.clientWidth ||
                pt.y < 0 || pt.y > cv.clientHeight;
            if (offScreenAfterPan) {
                showToast(
                    `Your location is at or beyond the edge of the ` +
                    `${CONFIG.name} area — the map can't pan further.`);
            }

            // Resume follow-me tracking. We're already at the user's
            // position (the flyTo above just got us there), so any
            // auto-pan MapLibre wants to do is a no-op — but we do
            // want subsequent per-fix easeTo updates to keep us
            // tracking. Set the same flags the Locate-button click
            // handler sets for a re-engagement, then trigger() if
            // we're not already active. (No trackuserlocationstart
            // handler arms these any more; the click handler is the
            // single source of truth — see the state-machine
            // docblock at the GeolocateControl creation.)
            if (geolocateControl) {
                const native = document.querySelector(".maplibregl-ctrl-geolocate");
                const isActive = native && native.classList
                    .contains("maplibregl-ctrl-geolocate-active");
                if (!isActive) {
                    _firstGeolocateMoveAfterTrigger = false;
                    _showToastOnNextFix = false;
                    _followUserOnGeolocate = true;
                    geolocateControl.trigger();
                } else {
                    // Already active. Re-arm follow in case a manual
                    // pan earlier in this session cleared the flag.
                    _followUserOnGeolocate = true;
                }
            }
        });
        map.flyTo({ center: userLocation, duration: 500 });
    });
}

function updateLocationIndicator() {
    let el = document.getElementById("off-screen-indicator");
    if (!userLocation || !map) {
        if (el) el.classList.add("hidden");
        return;
    }

    const point = map.project(userLocation);
    const canvas = map.getCanvas();
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;

    // Inset rectangle the indicator is allowed to occupy. The bottom
    // edge pulls up by the peek-bar height + safe-area inset because
    // the bottom sheet overlays the canvas's bottom strip. Without
    // this reserve, the indicator sits behind the peek bar — the user
    // gets the "tap the blue ▲" toast with no visible triangle to
    // tap. The logo overlay (bottom-left) is transparent, so an
    // indicator landing on top of it is fine; we don't reserve for
    // it. Reading peek-height live means sheet resize / orientation
    // change is reflected without a separate notification.
    const cs = getComputedStyle(document.documentElement);
    const peekH = parseFloat(cs.getPropertyValue("--peek-height")) || 72;
    const safeBottom = parseFloat(cs.getPropertyValue("--safe-bottom")) || 0;
    const edgeMargin = 48;
    const xLeft = edgeMargin;
    const xRight = w - edgeMargin;
    const yTop = edgeMargin;
    const yBottom = h - peekH - safeBottom - edgeMargin;

    // Degenerate rectangle (canvas too short for the reserve) — skip.
    if (xRight <= xLeft || yBottom <= yTop) {
        if (el) el.classList.add("hidden");
        return;
    }

    // Hide the indicator when the user is inside the visible-and-
    // unobstructed rectangle. Same threshold as the clamp below, so
    // the show/hide and clamp behaviors stay aligned.
    if (point.x >= xLeft && point.x <= xRight &&
        point.y >= yTop && point.y <= yBottom) {
        if (el) el.classList.add("hidden");
        return;
    }

    // The element is pre-rendered in index.html, so el is always
    // truthy here. The click listener is attached once at app init
    // (see attachOffScreenIndicatorHandler below) — historically the
    // listener was attached inside an `if (!el)` block here, which
    // never ran because the element pre-existed, leaving taps dead.
    el.classList.remove("hidden");

    const cx = w / 2;
    const cy = h / 2;
    const angle = Math.atan2(point.y - cy, point.x - cx);

    // Travel from (cx, cy) along the angle until we hit the inset
    // rectangle's nearest edge. For each axis, compute the parametric
    // t at which that edge is crossed; take the minimum positive t.
    // This handles asymmetric margins cleanly (the bottom reserve
    // makes yBottom asymmetric from yTop), unlike the previous
    // half-extent math which assumed equal margins on every side.
    const cosA = Math.cos(angle);
    const sinA = Math.sin(angle);
    const eps = 1e-6;
    let t = Infinity;
    if (cosA > eps)  t = Math.min(t, (xRight - cx) / cosA);
    if (cosA < -eps) t = Math.min(t, (xLeft  - cx) / cosA);
    if (sinA > eps)  t = Math.min(t, (yBottom - cy) / sinA);
    if (sinA < -eps) t = Math.min(t, (yTop    - cy) / sinA);
    const x = cx + cosA * t;
    const y = cy + sinA * t;

    const degrees = (angle * 180 / Math.PI) + 90;

    const center = map.getCenter();
    const dist = haversineDistance([center.lng, center.lat], userLocation);

    el.innerHTML = `<span class="off-screen-indicator-arrow" style="transform:rotate(${degrees}deg)">&#9650;</span>`
        + `<span class="off-screen-indicator-dist">${formatDistance(dist)}</span>`;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.style.display = "flex";
}

// ============================================================
// Toast notifications
// ============================================================
// Show a toast. Three forms:
//
//   showToast("message")           — transient hint; auto-dismisses after 4s.
//                                    The default for off-screen indicator
//                                    nudges, geolocation errors, copy
//                                    confirmations, etc.
//
//   showToast("message", {         — auto-dismiss with an action button.
//       timeoutMs: 15000,            Used by the SW-update toast (B.7) so
//       actions: [                   the user has time to read and decide
//           {label: "Reload",        about the action; the toast still
//            onClick: fn,            fades on its own if ignored.
//            primary: true},
//       ],
//   })
//
//   showToast("message", {         — persistent: stays until the ✕ or an
//       persistent: true,            action button is tapped. Reserved for
//       actions: [...],              critical notices that must be
//       onDismiss: fn,               acknowledged. Currently unused; kept
//   })                               available for future callers.
//
// The toast element is rebuilt on each call (not just text-replaced) so
// switching between forms works without stale buttons left behind. A
// small ✕ dismiss is added only in persistent mode.
function showToast(message, opts) {
    opts = opts || {};
    const persistent = !!opts.persistent;
    const actions = Array.isArray(opts.actions) ? opts.actions : [];

    let el = document.getElementById("map-toast");
    if (!el) {
        el = document.createElement("div");
        el.id = "map-toast";
        el.className = "map-toast";
        document.body.appendChild(el);
    }
    // Cancel any pending auto-dismiss from a prior toast call.
    clearTimeout(el._timeout);
    el._timeout = null;
    // Rebuild contents (clear then construct fresh DOM so the toast
    // never carries stale buttons from a previous persistent call).
    el.textContent = "";
    el.classList.toggle("map-toast-actionable", persistent || actions.length > 0);

    const messageEl = document.createElement("span");
    messageEl.className = "map-toast-message";
    messageEl.textContent = message;
    el.appendChild(messageEl);

    for (const action of actions) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "map-toast-action"
            + (action.primary ? " map-toast-action-primary" : "");
        btn.textContent = action.label;
        btn.addEventListener("click", () => {
            try {
                if (typeof action.onClick === "function") action.onClick();
            } finally {
                // Dismiss after action — caller can re-show if they want
                // a different post-action state (most won't).
                dismissToast();
            }
        });
        el.appendChild(btn);
    }

    if (persistent) {
        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "map-toast-close";
        closeBtn.setAttribute("aria-label", "Dismiss");
        closeBtn.innerHTML =
            '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" '
            + 'stroke="currentColor" stroke-width="2" stroke-linecap="round">'
            + '<line x1="2" y1="2" x2="12" y2="12"/>'
            + '<line x1="12" y1="2" x2="2" y2="12"/></svg>';
        closeBtn.addEventListener("click", () => {
            if (typeof opts.onDismiss === "function") opts.onDismiss();
            dismissToast();
        });
        el.appendChild(closeBtn);
    }

    el.classList.remove("hidden");
    el.classList.add("visible");

    if (!persistent) {
        // Default 4s for transient hints; callers can pass timeoutMs
        // for longer dismissal — used by the SW-update toast (B.7)
        // which needs ~15s so the user has time to read and decide
        // about the Reload action.
        const timeoutMs = typeof opts.timeoutMs === "number"
            ? opts.timeoutMs : 4000;
        el._timeout = setTimeout(() => {
            el.classList.remove("visible");
            el.classList.add("hidden");
        }, timeoutMs);
    }
}

// Hide any visible toast immediately. Used by interactions that act on
// the toast's instruction (e.g. tapping the off-screen indicator after
// being told to do so) — without this, the user sees their action AND
// the same nagging toast for up to 4 s, which reads as "didn't work".
function dismissToast() {
    const el = document.getElementById("map-toast");
    if (!el) return;
    clearTimeout(el._timeout);
    el._timeout = null;
    el.classList.remove("visible");
    el.classList.add("hidden");
}

// ============================================================
// Start
// ============================================================
init();
