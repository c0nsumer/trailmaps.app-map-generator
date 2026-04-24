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

function buildDifficultyFilter() {
    // Runs on the trail-centerlines source, which only contains
    // features for physical ways with at least one visible route —
    // so no route_id / visibility check is needed here, only the
    // valid-rating gate.
    const validImba = ["in", ["get", "imba_difficulty"], ["literal", ["0", "1", "2", "3", "4", "5"]]];
    // When the spotlight dim is active, restrict difficulty icons to the
    // highlighted route (or trail) so the non-highlighted areas read as
    // clean dim — IMBA diamonds otherwise punch through the tint.
    if (highlightDimActive()) {
        if (highlight.kind === "route") {
            return ["all", validImba, ["in", highlight.key, ["get", "shared_routes"]]];
        }
        return ["all", validImba, ["==", ["get", "trail_name"], highlight.key]];
    }
    return validImba;
}

function updateDifficultyFilter() {
    if (map.getLayer("trail-difficulty")) {
        map.setFilter("trail-difficulty", buildDifficultyFilter());
    }
}

function difficultyToggleOn() {
    return LS.get("mtb.difficulty", true) === true;
}

function addDifficultyLayer() {
    if (!map.getSource("trail-centerlines")) return;
    map.addLayer({
        id: "trail-difficulty",
        type: "symbol",
        source: "trail-centerlines",
        filter: buildDifficultyFilter(),
        layout: {
            "symbol-placement": "line",
            // Spacing along the line (pixels). Larger = fewer diamonds.
            // 800 is deliberately 2× the arrow layer's symbol-spacing
            // (400) — on the shared trail-centerlines source, a 2:1
            // ratio makes diamond anchors coincide with every-other
            // arrow anchor, so MapLibre's collision suppresses the
            // overlapping arrow at those positions with only
            // `icon-padding: 4`. In-between arrow anchors sit ~400 px
            // from the nearest diamond — well clear. Different-ratio
            // spacings (e.g. 400/500) produce beat-pattern overlaps
            // that padding alone can't reliably catch.
            "symbol-spacing": 800,
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
            "icon-size": [
                "interpolate", ["linear"], ["zoom"],
                12, 0.5,
                14, 0.7,
                18, 1.0,
            ],
            "icon-rotation-alignment": "viewport",
            "icon-allow-overlap": false,
            "icon-ignore-placement": false,
            // Modest pad is enough now that all decor layers share
            // one feature per physical way — anchors land on
            // identical line coordinates, so even a small collision
            // box catches arrow+diamond conflicts reliably.
            "icon-padding": 4,
            "visibility": difficultyToggleOn() ? "visible" : "none",
        },
    });
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

// Compute eagerly so addArrowsLayer() (called during initial trail load)
// gets the right rotation expression on first paint.
let reverseRoutesToday = todaysReverseRoutes();

function buildArrowRotation() {
    // Default: arrow follows the way's digitised direction (rotation 0).
    // For ways belonging to a relation whose reverse_days includes today,
    // flip 180°. shared_routes is the per-feature array of relation IDs.
    const reverseIds = [...reverseRoutesToday];
    if (reverseIds.length === 0) return 0;
    const checks = reverseIds.map((id) => ["in", id, ["get", "shared_routes"]]);
    return ["case",
        checks.length === 1 ? checks[0] : ["any", ...checks], 180,
        0,
    ];
}

function updateArrowsRotation() {
    if (map.getLayer("trail-arrows")) {
        map.setLayoutProperty("trail-arrows", "icon-rotate", buildArrowRotation());
    }
}

// Filter shape used by addArrowsLayer + updateArrowsFilter. The
// arrow layer renders on the trail-centerlines source, which already
// has one feature per physical way — so the base filter just needs
// to keep oneway ways.
function buildArrowBaseFilter() {
    return ["in", ["get", "oneway"], ["literal", ["yes", "reversible"]]];
}

function addArrowsLayer() {
    if (!map.getSource("trail-centerlines")) return;
    if (map.getLayer("trail-arrows")) {
        // After a basemap rebuild the layer comes back via the serialised
        // style — refresh rotation but no icon swap is needed (single icon).
        updateArrowsRotation();
        return;
    }
    map.addLayer({
        id: "trail-arrows",
        type: "symbol",
        // Centerlines source = one feature per physical way, same as
        // trail-difficulty and trail-centerline-labels. Sharing both
        // the source AND the feature geometry is what makes MapLibre's
        // collision detection reliable for symbols at the same line
        // position — prior splits (separate source / sibling offset
        // features) let coincident symbols slip through.
        source: "trail-centerlines",
        filter: buildArrowBaseFilter(),
        layout: {
            "symbol-placement": "line",
            // Spacing along the line (pixels). Larger = fewer arrows.
            // 400 keeps directionality readable without arrows every
            // few meters on a curvy trail. The difficulty layer uses
            // 800 (2× this) so their anchors interleave cleanly on
            // the shared centerlines source — see the comment in
            // addDifficultyLayer for why the ratio matters.
            "symbol-spacing": 400,
            "icon-image": ARROW_ICON_ID,
            // Arrows are always on; scaled down ~30 % from the previous
            // 0.7/1.0/1.4 sizing so they read as a subtle directional cue
            // rather than competing with the casings.
            "icon-size": [
                "interpolate", ["linear"], ["zoom"],
                12, 0.5,
                14, 0.7,
                18, 1.0,
            ],
            "icon-rotate": buildArrowRotation(),
            "icon-rotation-alignment": "map",
            "icon-allow-overlap": false,
            // true = arrows don't block trail-name label placement.
            "icon-ignore-placement": true,
            // Small default pad is enough now that arrows, diamonds,
            // and trail-name labels all share a single source and
            // single feature per way — their line-placement anchors
            // align on identical coordinates, so collision catches
            // conflicts deterministically.
            "icon-padding": 4,
        },
    });
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
let routeIndex = []; // [{ id, name, color, summer, winter, emergency, isCustom }]
let trailIndex = []; // [{ name, routeIds: [string] }]

// Marker arrays — trailMarkerMarkers covers the merged guidepost +
// emergency-access-point category (single "Markers" peek button).
let trailMarkerMarkers = [];
let parkingMarkers = [];
let trailheadMarkers = [];
let featureMarkers = [];
let userLocation = null; // [lng, lat] from geolocate control

// ============================================================
// Initialization
// ============================================================
async function init() {
    // Register PMTiles protocol
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    // Build map style (light theme only)
    const style = buildStyle();

    // Build transformRequest for base layers that require auth headers
    const headersByDomain = buildHeaderMap();

    // Create map
    //
    // `bounds` uses the tight CONFIG.bbox so the initial view frames
    // the trails snugly. `maxBounds` uses CONFIG.panBbox (built from
    // bbox + pan_padding) so the user has room to pan for context —
    // maxBounds clamps the map CENTER, not the viewport edge, and the
    // basemap/terrain PMTiles are extracted to match panBbox so real
    // tiles fill the full pannable envelope.
    const mapOptions = {
        container: "map",
        style: style,
        bounds: [
            [CONFIG.bbox[0], CONFIG.bbox[1]],
            [CONFIG.bbox[2], CONFIG.bbox[3]],
        ],
        fitBoundsOptions: { padding: 50 },
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
        // share. Controlled per-map via CONFIG.urlHash; default true
        // for backward compatibility with existing deployments.
        hash: CONFIG.urlHash,
    };

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
    const geolocate = new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: true,
    });
    map.addControl(geolocate, "top-left");

    // Set on user-initiated tracking starts (button clicks), consumed by
    // the next geolocate or userlocationfocus event.
    let _showToastOnNextFix = false;

    const maybeShowOffScreenToast = () => {
        if (!_showToastOnNextFix || !userLocation) return;
        _showToastOnNextFix = false;
        const pt = map.project(userLocation);
        const cv = map.getCanvas();
        if (pt.x < 0 || pt.x > cv.clientWidth || pt.y < 0 || pt.y > cv.clientHeight) {
            showToast("Your location is currently off-screen — tap the indicator to center the map");
        }
    };

    // Intercept the auto pan/zoom GeolocateControl triggers via fitBounds.
    // Defer to a microtask, which drains after easeTo finishes setup but
    // before any rAF fires — cancelling the animation before a single
    // frame draws.
    map.on("movestart", (e) => {
        if (!e.geolocateSource) return;
        queueMicrotask(() => map.stop());
    });

    geolocate.on("geolocate", (e) => {
        userLocation = [e.coords.longitude, e.coords.latitude];
        updateLocationIndicator();
        maybeShowOffScreenToast();
    });

    geolocate.on("trackuserlocationstart", () => {
        _showToastOnNextFix = true;
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
    geolocate.on("trackuserlocationend", () => updateLocationIndicator());

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
    }
    if (locateBtn) {
        locateBtn.addEventListener("click", (e) => {
            e.stopPropagation();
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

    let rafPending = false;
    map.on("move", () => {
        if (rafPending) return;
        rafPending = true;
        requestAnimationFrame(() => {
            rafPending = false;
            updateLocationIndicator();
        });
    });

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
        } finally {
            const sheet = document.getElementById("bottom-sheet");
            if (sheet) sheet.style.visibility = "";
        }
    });

    // Update page title
    document.title = CONFIG.title;

    // About This Map modal
    initAbout();
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

function casingColor(routeInfo) {
    const lum = colorLuminance(effectiveRouteColor(routeInfo));
    if (lum > 0.7) return "rgba(0,0,0,0.6)";
    return "#000000";
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

// Casing-color expression for difficulty mode (light theme)
function difficultyCasingExpr() {
    return [
        "match", ["get", "imba_difficulty"],
        "0", "rgba(0,0,0,0.6)",     // white fill → dark casing
        "1", "#000000",              // green fill → black casing
        "2", "#000000",              // blue fill → black casing
        "3", "rgba(255,255,255,0.6)", // black fill → light casing
        "4", "rgba(255,255,255,0.6)", // black fill → light casing
        "5", "#000000",              // orange fill → black casing
        colorLuminance(CONFIG.defaultTrailColor) > 0.7 ? "rgba(0,0,0,0.6)" : "#000000",
    ];
}

// ============================================================
// Proximity filtering helpers
// ============================================================
const POI_PROXIMITY_METERS = 10; // hard-coded; no longer configurable

// Threshold for "on or adjacent to the highlighted route" during the
// spotlight dim. Wider than POI_PROXIMITY_METERS because parking lots
// and trailheads are user-supplied at lot-entrance coordinates that sit
// 50-200m from the signed trail itself — they're "adjacent" even though
// the strict POI-on-trail check would drop them.
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
    const resp = await fetch("trails.geojson");
    routesData = await resp.json();

    // Optional sibling file: continuation arrowhead points at bbox-edge
    // endpoints of clipped relations.
    try {
        const clipResp = await fetch("clip_endpoints.geojson");
        if (clipResp.ok) clipEndpointsData = await clipResp.json();
    } catch (_) {
        clipEndpointsData = null;
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

    // Add centerlines source — one feature per physical way, carrying
    // the intrinsic trail properties (trail_name, imba_difficulty,
    // oneway). Used by trail-arrows, trail-difficulty, and the
    // trail-name label layer. Having all three decor layers on a
    // single source with a single feature per way ensures their
    // line-placement anchors align on identical coordinates so
    // MapLibre's collision reliably suppresses coincident symbols.
    map.addSource("trail-centerlines", {
        type: "geojson",
        data: computeTrailCenterlines(),
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
            const iconCol = [
                "case",
                [">=", ["get", "visible_count"], 2],
                sharedArrowColor(),
                casingColor(routeInfo),
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
                    "icon-size": ["interpolate", ["linear"], ["zoom"],
                        12, 2.0, 14, 2.5, 18, 3.5],
                },
                paint: {
                    "icon-color": iconCol,
                    "icon-opacity": 1.0,
                },
            });
        }
    }

    // ----- Highlight layers (above trail fills, below labels + arrows) -----
    // Four layers per highlight kind (route / trail): outline, glow,
    // stroke, core. Rendered above the fills so they read as a
    // "highlighted ribbon", but below labels + difficulty + arrows so
    // those stay readable / visible when a route or trail is
    // highlighted. All start with a no-match filter;
    // highlightRoute()/highlightTrail() swap in the real filter and
    // set the dynamic colour.
    const NONE_FILTER_ROUTE = ["==", ["get", "route_id"], "___NONE___"];
    const NONE_FILTER_TRAIL = ["==", ["get", "trail_name"], "___NONE___"];

    // Four-layer highlight (bottom → top):
    //   outline:  thick black (silhouette — pops against any basemap/trail)
    //   glow:     blurred amber (soft halo)
    //   stroke:   opaque amber (identity colour)
    //   core:     thin white (inner highlight — pulls it forward)
    // Combined, the highlighted line reads as a dark-bordered amber
    // ribbon with a bright centre — unmistakable against peach, slate,
    // amber, yellow, or sandy basemap tiles and in bright sunlight.
    map.addLayer({
        id: "route-highlight-outline",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_ROUTE,
        paint: {
            "line-color": "#000",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 14, 11, 18, 18],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    // NOTE: there was a `route-highlight-glow` layer here (blurred,
    // semi-transparent, wide amber stroke) to give the ribbon a halo.
    // It's been removed because the blur + additive alpha created
    // visible bright spikes at sharp switchback bends. The spotlight
    // dim (mapDimOnHighlight) does enough work to make the ribbon pop
    // on its own; the glow was redundant.
    map.addLayer({
        id: "route-highlight-stroke",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_ROUTE,
        paint: {
            "line-color": "#ffb700",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 18, 13],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    map.addLayer({
        id: "route-highlight-core",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_ROUTE,
        paint: {
            "line-color": "#ffffff",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 14, 2, 18, 3.5],
            "line-opacity": 0.85,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    map.addLayer({
        id: "trail-highlight-outline",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_TRAIL,
        paint: {
            "line-color": "#000",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 14, 11, 18, 18],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    // `trail-highlight-glow` was removed for the same reason as
    // `route-highlight-glow` — blur + additive alpha produced bright
    // spikes at sharp bends. See note above the stroke layer.
    map.addLayer({
        id: "trail-highlight-stroke",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_TRAIL,
        paint: {
            "line-color": "#ffb700",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 18, 13],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    map.addLayer({
        id: "trail-highlight-core",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_TRAIL,
        paint: {
            "line-color": "#ffffff",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 14, 2, 18, 3.5],
            "line-opacity": 0.85,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });

    // Route-name labels — one layer per route so each follows its
    // own offset line. Only rendered when labelMode === "routes";
    // trail-name labels render from the centerlines source below so
    // each trail name appears exactly once per physical way regardless
    // of how many routes share it.
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

    // Trail-name label — a single layer on the centerlines source so
    // each trail appears exactly once, regardless of shared routes.
    // Rendering it right here puts it in the same stacking position
    // as the per-route label layers (above highlights, below decor
    // symbols). Difficulty + arrows are added after and therefore
    // collision-win over long labels.
    map.addLayer({
        id: "trail-centerline-labels",
        type: "symbol",
        source: "trail-centerlines",
        filter: ["!=", ["get", "trail_name"], ""],
        layout: {
            "symbol-placement": "line",
            "text-field": ["get", "trail_name"],
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
            "text-padding": 3,
            "text-optional": true,
            "symbol-spacing": 150,
            "visibility": labelMode === "trails" ? "visible" : "none",
        },
        paint: {
            "text-color": "#1a1a1a",
            "text-halo-color": "rgba(255,255,255,0.9)",
            "text-halo-width": 2,
        },
    });

    // Solo-way route-name label — runs on the same centerlines source
    // as arrows / difficulty / trail-name so MapLibre's collision
    // sees everything on one line as one ordered group. This only
    // carries features for ways with exactly one visible route
    // (solo_route_name is populated in computeTrailCenterlines);
    // shared ways still put their per-route labels on the offset
    // trails-labels source, since those need N distinct displaced
    // copies — something a single centerline feature can't represent.
    map.addLayer({
        id: "trail-centerline-route-labels",
        type: "symbol",
        source: "trail-centerlines",
        filter: ["!=", ["get", "solo_route_name"], ""],
        layout: {
            "symbol-placement": "line",
            "text-field": ["get", "solo_route_name"],
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
            "text-padding": 3,
            "text-optional": true,
            "symbol-spacing": 150,
            "visibility": labelMode === "routes" ? "visible" : "none",
        },
        paint: {
            "text-color": "#1a1a1a",
            "text-halo-color": "rgba(255,255,255,0.9)",
            "text-halo-width": 2,
        },
    });

    // Difficulty symbols — above labels + highlights.
    if (CONFIG.showDifficulty) {
        registerDifficultyIcons();
        addDifficultyLayer();
    }

    // Direction arrows on one-way trails — added last so they render
    // on top of labels, difficulty, and the highlight ribbon.
    registerArrowIcons();
    addArrowsLayer();
}

function updateLabels() {
    const dim = highlightDimActive();

    // Per-route route-name label layers — each scoped to one route via
    // its baseline filter. Source data (computeLabelData) only carries
    // shared-way features now; solo-way labels live on the centerline
    // layer below. Visible only in "routes" mode; trail highlights
    // hide them all (route names don't correspond to a particular
    // trail), and route highlights keep only the matching layer.
    for (const routeId of Object.keys(CONFIG.routes)) {
        const layerId = `trail-label-${routeId}`;
        if (!map.getLayer(layerId)) continue;

        let visible = labelMode === "routes" && visibleRoutes.has(routeId);
        if (visible && dim) {
            if (highlight.kind === "route") {
                visible = routeId === highlight.key;
            } else {
                visible = false;
            }
        }
        map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    }

    // Solo-way route-name labels on the centerline source — one
    // feature per way with exactly one visible route, labelled with
    // that route's name. Visible only in "routes" mode. Under dim:
    // route highlight narrows to that route; trail highlight hides
    // all route names (same semantics as the per-route layers above).
    // The base filter (solo_route_name non-empty) stays layered into
    // every highlight-case filter.
    if (map.getLayer("trail-centerline-route-labels")) {
        const SOLO_BASE = ["!=", ["get", "solo_route_name"], ""];
        let visible = labelMode === "routes";
        let filter = SOLO_BASE;
        if (visible && dim) {
            if (highlight.kind === "route") {
                filter = ["all",
                    SOLO_BASE,
                    ["==", ["get", "solo_route_id"], highlight.key],
                ];
            } else {
                visible = false;
            }
        }
        map.setLayoutProperty("trail-centerline-route-labels",
            "visibility", visible ? "visible" : "none");
        if (visible) map.setFilter("trail-centerline-route-labels", filter);
    }

    // Single centerline trail-name label layer — one feature per
    // physical way, so each trail name appears exactly once regardless
    // of how many routes share the way. Visible only in "trails" mode.
    // Under dim: filter to the highlighted route's shared-ways (route
    // highlight) or the highlighted trail_name (trail highlight).
    // Base filter (trail_name non-empty) is layered into every case.
    if (map.getLayer("trail-centerline-labels")) {
        const TRAIL_BASE = ["!=", ["get", "trail_name"], ""];
        const visible = labelMode === "trails";
        map.setLayoutProperty("trail-centerline-labels", "visibility",
            visible ? "visible" : "none");
        if (visible) {
            if (dim) {
                if (highlight.kind === "route") {
                    map.setFilter("trail-centerline-labels",
                        ["all", TRAIL_BASE,
                            ["in", highlight.key, ["get", "shared_routes"]]]);
                } else {
                    map.setFilter("trail-centerline-labels",
                        ["all", TRAIL_BASE,
                            ["==", ["get", "trail_name"], highlight.key]]);
                }
            } else {
                map.setFilter("trail-centerline-labels", TRAIL_BASE);
            }
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

// Per-way "centerline" features for decor layers (direction arrows,
// difficulty diamonds, trail-name labels). These properties are
// intrinsic to the physical way — they don't belong to any specific
// route variant — so rendering them from per-route offset copies was
// the source of pile-ups (two offset siblings' line-placement anchors
// landing at slightly different screen pixels, defeating collision).
//
// This function emits exactly ONE feature per physical way with its
// raw un-offset geometry. Arrows + diamonds + trail names all read
// from this single source, so their line-placement anchors align
// identically and MapLibre's intra-feature collision is deterministic.
//
// Canonical owner = the feature whose route_id is the lowest-sorted
// visible route in shared_routes. Features for non-canonical owners
// are skipped (they'd be exact duplicates, since raw coords are
// identical across siblings pre-offset).
function computeTrailCenterlines() {
    if (!routesData) return { type: "FeatureCollection", features: [] };

    const allRouteIds = Object.keys(CONFIG.routes).slice().sort(ROUTE_ID_COMPARE);
    const globalRank = new Map();
    allRouteIds.forEach((id, i) => globalRank.set(id, i));

    const features = [];
    for (const f of routesData.features) {
        const props = f.properties;
        const routeId = props.route_id;
        const shared = props.shared_routes || [routeId];
        const visibleShared = shared
            .filter((id) => visibleRoutes.has(id))
            .sort((a, b) => globalRank.get(a) - globalRank.get(b));

        // Way has no currently-visible routes → no decor.
        if (visibleShared.length === 0) continue;
        // Not the canonical owner → skip (siblings share raw coords).
        if (visibleShared[0] !== routeId) continue;

        // Solo way = exactly one visible route on this way. On such
        // ways we carry the route's name + id forward so a dedicated
        // centerline-route-label layer can render the route label
        // here rather than on the per-route offset source. Putting
        // the route label on the same source as arrows / difficulty /
        // trail names means MapLibre's collision runs one coherent
        // pass — no more near-coincident anchors from different
        // sources competing for the same pixel.
        const soloRouteName = visibleShared.length === 1
            ? (props.route_name || "") : "";
        const soloRouteId = visibleShared.length === 1 ? routeId : "";

        features.push({
            type: "Feature",
            geometry: f.geometry,
            properties: {
                trail_name: props.trail_name || "",
                imba_difficulty: props.imba_difficulty || "",
                oneway: props.oneway || "",
                shared_routes: shared,
                // The canonical owner's route_id — used by
                // buildArrowRotation's reverse-day check alongside
                // shared_routes (routes in direction_schedules flip
                // the arrow 180°).
                route_id: routeId,
                // Solo-way route label fields (empty string on shared
                // ways — their labels still render on trails-labels).
                solo_route_name: soloRouteName,
                solo_route_id: soloRouteId,
            },
        });
    }

    return { type: "FeatureCollection", features };
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
        // trail-centerline-route-labels layer on the centerlines source
        // so arrows / difficulty / trail names / route names all live
        // in one collision pass. Shared ways (>=2 visible routes) still
        // go here because each route variant needs its own offset-
        // displaced label feature; the centerline source can only carry
        // one route label per way.
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

    // Centerlines feed the decor layers (arrows, difficulty, trail
    // names). Recompute on every visibility change so ways whose only
    // visible route just toggled off drop out.
    const centerlineSource = map.getSource("trail-centerlines");
    if (centerlineSource) centerlineSource.setData(computeTrailCenterlines());

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

    // Arrow + difficulty + trail-name label layers render on the
    // trail-centerlines source, which we just refreshed above. Their
    // feature set (one per currently-visible physical way) flows
    // through the source's setData — no separate layer-update calls.
    recomputeClipEndpointVisibility();

    // applyDimState() rebuilds labels / difficulty / arrows-filter /
    // clip-arrow visibility in a single pass, respecting whether the
    // spotlight dim is currently active. It's a superset of the plain
    // updateDifficultyFilter() call it replaces, so the no-dim case is
    // still O(n_routes) and cheap.
    applyDimState();
}

// ============================================================
// Highlight system
// ============================================================
// Layer groups — the dark outline + amber glow + amber stroke render
// as a single unit. All three layers take the same filter; only the
// stroke and glow take the dynamic colour (the outline stays dark).
// Four layers each (bottom → top): outline, glow, stroke, core.
// Only the stroke takes the route's accent colour; the black outline
// and the white core never change colour — that's what gives the
// highlight its "dark-bordered bright ribbon" look against any
// background. (A wider blurred "glow" layer used to sit between the
// outline and stroke; it was removed because the blur created bright
// spikes at sharp switchback bends.)
const ROUTE_HIGHLIGHT_LAYERS = [
    "route-highlight-outline",
    "route-highlight-stroke",
    "route-highlight-core",
];
const TRAIL_HIGHLIGHT_LAYERS = [
    "trail-highlight-outline",
    "trail-highlight-stroke",
    "trail-highlight-core",
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

// Narrow trail-arrows to only arrows belonging to the highlighted
// route/trail when the dim is on. The arrow layer's base filter
// (is_canonical_arrow) is always required — the layer is on the
// shared `trails` source, which contains every route's features,
// not just oneway canonicals. So the highlight filter is layered
// ON TOP of the base, never replacing it.
function updateArrowsFilter() {
    if (!map.getLayer("trail-arrows")) return;
    const base = buildArrowBaseFilter();
    if (!highlightDimActive()) {
        map.setFilter("trail-arrows", base);
        return;
    }
    if (highlight.kind === "route") {
        map.setFilter("trail-arrows",
            ["all", base, ["in", highlight.key, ["get", "shared_routes"]]]);
    } else {
        map.setFilter("trail-arrows",
            ["all", base, ["==", ["get", "trail_name"], highlight.key]]);
    }
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
    updateDifficultyFilter();
    updateArrowsFilter();
    updateClipArrowsDim();
    updateMarkerDimState();
}

function highlightRoute(routeId) {
    const info = CONFIG.routes[routeId];
    if (!info) return;
    highlight = { kind: "route", key: routeId };

    // Amber, not the route's native colour — keeps "highlighted" as an
    // unambiguous UI state regardless of how close the route's own
    // colour is to the rest of the map. (The chip shows the route's
    // native colour for attribution.)
    const amber = "#ffb700";
    const routeFilter = ["==", ["get", "route_id"], routeId];
    for (const layerId of ROUTE_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, routeFilter);
        }
    }
    for (const layerId of ROUTE_TINTED_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setPaintProperty(layerId, "line-color", amber);
        }
    }
    const color = effectiveRouteColor(info);
    // Clear trail highlights (single-highlight invariant)
    for (const layerId of TRAIL_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, TRAIL_NONE_FILTER);
        }
    }

    // Fit bounds to the route
    fitToRouteOrTrail({ routeId });

    // Show chip
    showHighlightChip({ label: info.name, color });

    // Spotlight dim (no-op unless CONFIG.mapDimOnHighlight is on)
    applyDimState();
}

function highlightTrail(trailName) {
    highlight = { kind: "trail", key: trailName };

    const amber = "#ffb700";
    const trailFilter = ["==", ["get", "trail_name"], trailName];
    for (const layerId of TRAIL_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, trailFilter);
        }
    }
    for (const layerId of TRAIL_TINTED_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setPaintProperty(layerId, "line-color", amber);
        }
    }
    // Clear route highlights
    for (const layerId of ROUTE_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setFilter(layerId, ROUTE_NONE_FILTER);
        }
    }

    fitToRouteOrTrail({ trailName });
    showHighlightChip({ label: trailName, color: amber });

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

function showHighlightChip({ label, color }) {
    const chip = document.getElementById("highlight-chip");
    if (!chip) return;
    const swatch = chip.querySelector(".highlight-chip-swatch");
    const labelEl = chip.querySelector(".highlight-chip-label");
    if (swatch) swatch.style.background = color;
    if (labelEl) labelEl.textContent = label;
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
    const resp = await fetch("pois.geojson");
    poisData = await resp.json();

    // Count features by type in a single pass. Trail markers merge
    // guideposts + emergency access points into one category.
    const poiCounts = { trail_marker: 0, parking: 0, trailhead: 0, feature: 0 };
    for (const f of poisData.features) {
        const t = f.properties.poi_type;
        if (t in poiCounts) poiCounts[t]++;
    }
    const { trail_marker: tmCount, parking: pkCount,
            trailhead: thCount, feature: ftCount } = poiCounts;

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

    if (CONFIG.showFeatures && ftCount > 0) {
        addFeatureMarkers(ftDefault);
    } else {
        hidePeekBtn("toggle-features");
    }
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
            const popup = new maplibregl.Popup({ offset: 14, maxWidth: popupMaxWidth, closeButton: false })
                .setHTML(popupHtmlFn(props, coords));
            marker.setPopup(popup);
        }

        if (addToMap) marker.addTo(map);
        targetArray.push(marker);
    }
}

// Single helper covering the merged trail-marker POI category — OSM
// guideposts and emergency-access points now render with the same
// style. Shown/hidden together via the "Markers" peek toggle.
function addTrailMarkers(addToMap) {
    createPoiMarkers({
        poiType: "trail_marker",
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
        poiType: "parking",
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
        poiType: "trailhead",
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
        poiType: "feature",
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
    function openSheet() {
        sheet.classList.add("is-open");
        handle.setAttribute("aria-expanded", "true");
        expanded.setAttribute("aria-hidden", "false");
    }
    function closeSheet() {
        sheet.classList.remove("is-open");
        handle.setAttribute("aria-expanded", "false");
        expanded.setAttribute("aria-hidden", "true");
    }
    function toggleSheet() {
        if (sheet.classList.contains("is-open")) closeSheet();
        else openSheet();
    }

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
                pointerId: e.pointerId,
                captureEl,
            };
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
            sheet.style.maxHeight = (wasOpen ? drag.openH : drag.peekH) + "px";
            // Expose .sheet-expanded during drag so max-height has
            // content to reveal. Without this, dragging from closed
            // state was a no-op: the CSS `.sheet-expanded { display:
            // none }` rule left the sheet at peek height regardless
            // of any inline max-height we set. Most visible in
            // Firefox, where the tap-fallback didn't always salvage
            // short drags. Clear in end/cancel.
            if (!wasOpen) sheet.classList.add("is-dragging");
            // Capture so moves keep firing even outside the element. Use
            // the *listener* element, not e.target — some browsers throw
            // NotFoundError when capturing on a child text run.
            try { captureEl.setPointerCapture(e.pointerId); } catch (_) {}
        }

        function moveDrag(e) {
            if (!drag || e.pointerId !== drag.pointerId) return;
            const dy = e.clientY - drag.startY;
            drag.lastY = e.clientY;
            drag.lastT = performance.now();
            if (Math.abs(dy) > TAP_MAX_DIST) drag.moved = true;

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

            // Tap → toggle. Use both distance AND time bounds so a slow
            // finger that barely moved still counts as a tap.
            if (!moved && Math.abs(dy) < TAP_MAX_DIST && dt < TAP_MAX_TIME) {
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
    const seasonBtn = document.getElementById("toggle-season");
    const seasonLabel = document.getElementById("toggle-season-label");
    const seasonSwatch = seasonBtn && seasonBtn.querySelector(".season-swatch");
    // CONFIG.routes and CONFIG.customRoutes are objects keyed by route id.
    // A route participates in winter mode iff its info carries `winter: true`.
    const anyRouteHas = (flag) => {
        const check = (coll) => coll && Object.values(coll).some(
            (r) => r && typeof r === "object" && r[flag] === true);
        return check(CONFIG.routes) || check(CONFIG.customRoutes);
    };
    const hasWinter = anyRouteHas("winter");
    if (seasonBtn && hasWinter) {
        const reflectSeason = () => {
            const isSummer = seasonMode === "summer";
            seasonBtn.setAttribute("aria-label",
                isSummer ? "Switch to winter" : "Switch to summer");
            if (seasonSwatch) {
                seasonSwatch.innerHTML = isSummer ? SUN_SVG : SNOW_SVG;
                // Summer colour (warm forest green) lives in CSS; winter
                // is a cold slate-blue applied inline so the two palettes
                // read as distinct seasonal moods beyond the glyph.
                seasonSwatch.style.background = isSummer ? "" : "#3d6b9c";
            }
            if (seasonLabel) seasonLabel.textContent = isSummer ? "Summer" : "Winter";
        };
        seasonBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            seasonMode = seasonMode === "summer" ? "winter" : "summer";
            LS.set("mtb.seasonMode", seasonMode);
            reflectSeason();
            applyVisibilityChange();
        });
        reflectSeason();
    } else if (seasonBtn) {
        seasonBtn.classList.add("hidden");
        // Force summer regardless of persisted state so renders are silent.
        seasonMode = "summer";
    }

    // ----- Emergency Access switch (expanded sheet, single authority) -
    //
    // Shown only when the current map has at least one route with
    // `emergency: true`. The peek row has no duplicate.
    const emSwitch = document.getElementById("toggle-emergency-routes");
    const emRow = document.getElementById("emergency-access-row");
    const hasEmergencyRoutes = anyRouteHas("emergency");
    if (emSwitch && hasEmergencyRoutes) {
        emSwitch.checked = emergencyOn;
        if (emRow) emRow.classList.remove("hidden");
        emSwitch.addEventListener("change", (e) => {
            emergencyOn = !!e.target.checked;
            LS.set("mtb.emergencyOn", emergencyOn);
            applyVisibilityChange();
        });
    } else {
        if (emRow) emRow.classList.add("hidden");
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
    // Proximity-filtered, same as features.
    wirePeekToggle("toggle-markers", "mtb.poi.markers", true, (on) => {
        if (on) {
            updateMarkerProximity();
        } else {
            for (const m of trailMarkerMarkers) m.remove();
        }
    });

    // Features — proximity-filtered.
    wirePeekToggle("toggle-features", "mtb.poi.features", true, (on) => {
        if (on) updateMarkerProximity();
        else for (const m of featureMarkers) m.remove();
    });

    // Parking / trailheads — always shown when on (no proximity filter).
    wirePeekToggle("toggle-parking", "mtb.poi.parking", true, (on) => {
        for (const m of parkingMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
    });
    wirePeekToggle("toggle-trailheads", "mtb.poi.trailheads", true, (on) => {
        for (const m of trailheadMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
    });

    // Difficulty — drives the IMBA sprite layer. Uses aria-pressed semantics
    // to match the other peek icons.
    const difficultyBtn = document.getElementById("toggle-difficulty");
    if (CONFIG.showDifficulty && difficultyBtn) {
        const initial = LS.get("mtb.difficulty", true);
        difficultyBtn.setAttribute("aria-pressed", initial ? "true" : "false");
        if (map.getLayer("trail-difficulty")) {
            map.setLayoutProperty("trail-difficulty", "visibility", initial ? "visible" : "none");
        }
        difficultyBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const next = difficultyBtn.getAttribute("aria-pressed") !== "true";
            difficultyBtn.setAttribute("aria-pressed", next ? "true" : "false");
            LS.set("mtb.difficulty", next);
            if (map.getLayer("trail-difficulty")) {
                map.setLayoutProperty("trail-difficulty", "visibility",
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
        basemapSelect.addEventListener("change", (e) => {
            basemapMode = e.target.value;
            rebuildBasemapLayers();
        });
    } else if (basemapField) {
        basemapField.classList.add("hidden");
    }

    // ----- Finder -----
    setupFinder();

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
    setInterval(() => {
        const next = todaysReverseRoutes();
        const changed = next.size !== reverseRoutesToday.size
            || [...next].some((id) => !reverseRoutesToday.has(id));
        if (changed) {
            reverseRoutesToday = next;
            updateArrowsRotation();
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

    row.addEventListener("click", () => {
        highlightRoute(r.id);
        document.getElementById("bottom-sheet").classList.remove("is-open");
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
        document.getElementById("bottom-sheet").classList.remove("is-open");
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

            new maplibregl.Popup({ maxWidth: "220px", closeButton: false })
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

    // Re-register difficulty/arrow icons if lost during style rebuild
    if (CONFIG.showDifficulty && !map.hasImage("imba-0")) {
        registerDifficultyIcons();
    }
    if (!map.hasImage(ARROW_ICON_ID)) {
        registerArrowIcons();
    }
    addArrowsLayer();
}

// ============================================================
// PWA Install — promoted out of About into a dedicated bottom-sheet row.
// Visible only when an install action is actually available.
// ============================================================
if (CONFIG.pwa) {
    let deferredInstallPrompt = null;

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

    window.addEventListener("beforeinstallprompt", (e) => {
        e.preventDefault();
        deferredInstallPrompt = e;
        revealInstallSection(true, false);
    });

    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        revealInstallSection(false, false);
    });

    document.addEventListener("DOMContentLoaded", () => {
        const installBtn = document.getElementById("install-btn");
        if (installBtn) {
            installBtn.addEventListener("click", async () => {
                if (!deferredInstallPrompt) return;
                deferredInstallPrompt.prompt();
                const result = await deferredInstallPrompt.userChoice;
                if (result.outcome === "accepted") {
                    revealInstallSection(false, false);
                }
                deferredInstallPrompt = null;
            });
        }

        // iOS detection — show manual instructions since iOS lacks
        // beforeinstallprompt. Only when the page is *not* already a
        // standalone PWA (otherwise the user already installed it).
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
function haversineDistance(lngLat1, lngLat2) {
    const toRad = (d) => d * Math.PI / 180;
    const R = 6371000; // Earth radius in meters
    const dLat = toRad(lngLat2[1] - lngLat1[1]);
    const dLng = toRad(lngLat2[0] - lngLat1[0]);
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(toRad(lngLat1[1])) * Math.cos(toRad(lngLat2[1])) *
              Math.sin(dLng / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function formatDistance(meters) {
    const mi = meters / 1609.344;
    if (mi < 0.1) {
        return `${Math.round(meters * 3.28084)} ft`;
    }
    return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
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
    const margin = 40;

    if (point.x >= margin && point.x <= w - margin &&
        point.y >= margin && point.y <= h - margin) {
        if (el) el.classList.add("hidden");
        return;
    }

    if (!el) {
        el = document.createElement("div");
        el.id = "off-screen-indicator";
        el.className = "off-screen-indicator";
        document.getElementById("map").appendChild(el);
        el.addEventListener("click", () => {
            if (userLocation) {
                map.flyTo({ center: userLocation, duration: 500 });
            }
        });
    } else {
        el.classList.remove("hidden");
    }

    const cx = w / 2;
    const cy = h / 2;
    const angle = Math.atan2(point.y - cy, point.x - cx);

    const edgeMargin = 48;
    const maxX = w / 2 - edgeMargin;
    const maxY = h / 2 - edgeMargin;
    const scale = Math.min(
        Math.abs(maxX / Math.cos(angle)) || Infinity,
        Math.abs(maxY / Math.sin(angle)) || Infinity,
    );
    const x = cx + Math.cos(angle) * scale;
    const y = cy + Math.sin(angle) * scale;

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
function showToast(message) {
    let el = document.getElementById("map-toast");
    if (!el) {
        el = document.createElement("div");
        el.id = "map-toast";
        el.className = "map-toast";
        document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.remove("hidden");
    el.classList.add("visible");
    clearTimeout(el._timeout);
    el._timeout = setTimeout(() => {
        el.classList.remove("visible");
        el.classList.add("hidden");
    }, 4000);
}

// ============================================================
// Start
// ============================================================
init();
