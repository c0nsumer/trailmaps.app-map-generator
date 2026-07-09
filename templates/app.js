/* trailmaps.app Map Generator, Map Viewer */
/* global maplibregl, pmtiles, basemaps */

/*__CONFIG__*/

// The brand element's content (logo image OR title text) is rendered
// at template-substitution time by the build pipeline, see
// scripts/build.py's __BRAND_TITLE__ replacement and the brand-img
// strip when no logo is configured. No JS-side title injection
// needed; the first paint carries the real brand.

// Push YAML-configured POI colors onto :root as CSS custom
// properties so the Options-overlay swatches, on-map markers, and
// popup badges all read the same single source of truth. Done
// before any markers or stylesheets evaluate against the default
// values so the first paint carries the real colors.
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
    // Hubs (named on-trail intersections, distinct from Trailheads)
    if (CONFIG.hubColor)             root.style.setProperty("--hub-color",             CONFIG.hubColor);
    if (CONFIG.hubTextColor)         root.style.setProperty("--hub-text-color",        CONFIG.hubTextColor);
    if (CONFIG.hubBorderColor)       root.style.setProperty("--hub-border-color",      CONFIG.hubBorderColor);
    // Features (inner dot + outer ring)
    if (CONFIG.featureColor)         root.style.setProperty("--feature-color",         CONFIG.featureColor);
    if (CONFIG.featureRingColor)     root.style.setProperty("--feature-ring-color",    CONFIG.featureRingColor);
    // Event POIs (always-on markers from event_mode.pois)
    if (CONFIG.eventPoiColor)        root.style.setProperty("--event-poi-color",       CONFIG.eventPoiColor);
    // Per-map UI accent (active toggle pills, focus rings, link color,
    // FAB pressed state, etc.). The build derives a 4-value palette, a
    // deep light-mode shade + a lightened dark-mode shade, each with its
    // best on-accent text color, so the accent reads correctly in
    // BOTH schemes. We set the four BASE vars here (not --accent
    // directly); style.css maps --accent / --on-accent from them per
    // [data-color-scheme], so the scheme toggle switches the accent for
    // free with no re-injection. Setting --accent inline would pin it
    // and defeat that mapping (inline style beats the stylesheet rule).
    if (CONFIG.accentLight)   root.style.setProperty("--accent-light",     CONFIG.accentLight);
    if (CONFIG.accentDark)    root.style.setProperty("--accent-dark",      CONFIG.accentDark);
    if (CONFIG.onAccentLight) root.style.setProperty("--on-accent-light",  CONFIG.onAccentLight);
    if (CONFIG.onAccentDark)  root.style.setProperty("--on-accent-dark",   CONFIG.onAccentDark);
})();

// ============================================================
// Color scheme (light / dark) helpers, Bundle 4B
// ============================================================
//
// The data-color-scheme attribute on <html> is the single source of
// truth at runtime. It's set by the inline bootstrap script in <head>
// (see build.py's __COLOR_SCHEME_BOOTSTRAP__ injection) BEFORE any
// stylesheet renders, so first paint already has the correct scheme
// and there's no light→dark FOUC.
//
// Resolution order is encoded in the bootstrap script:
//   1. localStorage <slug>.mtb.colorScheme (rider's last toggle)
//   2. CONFIG.defaultColorScheme (curator default; "light" by default)
//   3. "auto" → matchMedia(prefers-color-scheme: dark)
//
// Once the page is up, JS reads/writes through these helpers.
// ============================================================

function currentColorScheme() {
    // Always returns "light" or "dark", the bootstrap script
    // resolves "auto" before this runs, so the attribute is one of
    // those two concrete values.
    return document.documentElement.dataset.colorScheme === "dark"
        ? "dark" : "light";
}

// Per-scheme paint tokens for MapLibre layers that can't read CSS
// vars. Walked through applyMapPaintForScheme() on init AND every
// scheme change. Trail/route labels, direction arrows, and any
// future MapLibre-rendered text get their colors from here.
const MAP_PAINT_TOKENS = {
    light: {
        labelText:  "#1a1a1a",
        labelHalo:  "rgba(255, 255, 255, 0.9)",
        // Arrow icon ID, we register two canvas-rendered variants
        // (light-bg / dark-bg), distinct images, swap via
        // setLayoutProperty.
        arrowIcon:  "arrow-light-bg",
        // Hillshade, bright highlight + dark shadow give classic
        // "lit-from-NW" 3D shading on a light basemap. The dark
        // override below uses a much subtler highlight; bright
        // white on a dark basemap reads as clouds, not terrain.
        hillshadeShadow:    "#3d3d3d",
        hillshadeHighlight: "#ffffff",
        // Basemap-contrasting edge colors, every trail/route edge
        // treatment (casing, clip-arrow halo, highlight outline) contrasts
        // the basemap the same way: dark on the light basemap, light on the
        // dark basemap. Re-applied on scheme toggle by applyMapPaintForScheme.
        // (Casing 0.6 alpha; its line-opacity 0.5 → ~0.3 effective. Arrow
        // halo 0.85. Outline opaque.)
        trailCasing:      "rgba(0,0,0,0.6)",
        arrowHalo:        "rgba(0,0,0,0.85)",
        highlightOutline: "#000000",
    },
    dark: {
        labelText:  "#f0f0f0",
        labelHalo:  "rgba(0, 0, 0, 0.7)",
        arrowIcon:  "arrow-dark-bg",
        // Pure-black shadow deepens valleys BELOW the dark basemap
        // so topography reads. Highlight kept at low-alpha white
        // (~15%), provides a subtle lift on the lit slopes
        // without the bright "cloud-over-the-background" effect
        // that plain #ffffff produces.
        hillshadeShadow:    "#000000",
        hillshadeHighlight: "rgba(255, 255, 255, 0.15)",
        trailCasing:      "rgba(255,255,255,0.6)",
        arrowHalo:        "rgba(255,255,255,0.85)",
        highlightOutline: "#ffffff",
    },
};

function applyMapPaintForScheme(scheme) {
    const t = MAP_PAINT_TOKENS[scheme] || MAP_PAINT_TOKENS.light;
    if (!map) return;
    if (map.getLayer("decor-trail-name")) {
        map.setPaintProperty("decor-trail-name", "text-color", t.labelText);
        map.setPaintProperty("decor-trail-name", "text-halo-color", t.labelHalo);
    }
    if (map.getLayer("decor-route-name")) {
        map.setPaintProperty("decor-route-name", "text-color", t.labelText);
        map.setPaintProperty("decor-route-name", "text-halo-color", t.labelHalo);
    }
    for (const ptLayer of ["decor-route-name-pt", "decor-trail-name-pt"]) {
        if (map.getLayer(ptLayer)) {
            map.setPaintProperty(ptLayer, "text-color", t.labelText);
            map.setPaintProperty(ptLayer, "text-halo-color", t.labelHalo);
        }
    }
    // Per-route overlay layers that carry a scheme-dependent color and
    // survive a scheme toggle verbatim (setStyle({diff:true}) preserves
    // overlays), so their color is re-applied here rather than only at
    // build time:
    //   trail-label-<id>: name text + halo
    //   trail-casing-<id>: basemap-contrasting outline halo
    //   clip-arrow-<id>: continuation-arrow halo (basemap-contrasting)
    // Without the label flip they'd freeze at light-mode values (the "some
    // labels dark-inner light-outer" bug); without the casing/halo flip the
    // edges would freeze at the build-time scheme.
    const arrowHalo = clipArrowHaloExpr();
    if (map.getStyle && map.getStyle()) {
        for (const layer of map.getStyle().layers) {
            if (layer.id.startsWith("trail-label-")) {
                map.setPaintProperty(layer.id, "text-color", t.labelText);
                map.setPaintProperty(layer.id, "text-halo-color", t.labelHalo);
            } else if (layer.id.startsWith("trail-casing-")) {
                map.setPaintProperty(layer.id, "line-color", t.trailCasing);
            } else if (layer.id.startsWith("clip-arrow-")) {
                map.setPaintProperty(layer.id, "icon-halo-color", arrowHalo);
            }
        }
    }
    // Highlight silhouettes. The TRAIL outline is a constant scheme-
    // contrasting silhouette (black on the light basemap, white on dark).
    // The ROUTE outline is luminance-matched to the highlighted route's
    // own color, or the scheme silhouette for single-color dashed routes
    // (see routeHighlightOutlineColor); re-applied here so a scheme
    // toggle mid-highlight recomputes it (the dashed case follows the
    // scheme, the rest keep their stroke-contrast pick).
    if (map.getLayer("trail-highlight-outline")) {
        map.setPaintProperty("trail-highlight-outline", "line-color", t.highlightOutline);
    }
    if (map.getLayer("route-highlight-outline")) {
        const routeInfo = highlight && highlight.kind === "route"
            ? CONFIG.routes[highlight.key] : null;
        map.setPaintProperty("route-highlight-outline", "line-color",
            routeInfo ? routeHighlightOutlineColor(routeInfo)
                : t.highlightOutline);
    }
    if (map.getLayer("decor-arrow")) {
        map.setLayoutProperty("decor-arrow", "icon-image", t.arrowIcon);
    }
    if (map.getLayer("hillshade")) {
        map.setPaintProperty("hillshade", "hillshade-shadow-color", t.hillshadeShadow);
        map.setPaintProperty("hillshade", "hillshade-highlight-color", t.hillshadeHighlight);
    }
}

// Shared "recede the background" scrim density, used by BOTH the in-map
// highlight wash (the dim-tint layer) and the CSS overlay backdrops
// (--scrim-opacity, published in init() from this value), so the
// highlight wash and the menu backdrops read as one density. Driven by
// scrim_opacity; clamped to a valid alpha.
const SCRIM_OPACITY = Math.max(0, Math.min(1,
    typeof CONFIG.scrimOpacity === "number" ? CONFIG.scrimOpacity : 0.40));

// Apply a chosen scheme to the live page. Three concerns:
//   1. Persist preference to LS so subsequent visits use it.
//   2. Resolve "auto" → "light"|"dark" via prefers-color-scheme.
//   3. Update <html data-color-scheme>, then rebuild the basemap
//      (Protomaps flavor follows scheme via buildPmtilesStyle), then
//      re-apply the per-scheme map paint tokens once the new style
//      finishes loading.
//
// Used by:
//   - The Options Appearance segmented control (rider toggle)
//   - The OS prefers-color-scheme listener when the rider's stored
//     preference is "auto"
function applyColorScheme(rawScheme) {
    // rawScheme is the *intent*: "light", "dark", or "auto". Persist
    // intent (so "auto" stays "auto"), but resolve to a concrete
    // scheme for the data-color-scheme attribute.
    LS.set("mtb.colorScheme", rawScheme);
    let resolved = rawScheme;
    if (resolved === "auto") {
        resolved = window.matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark" : "light";
    }
    if (resolved !== "light" && resolved !== "dark") resolved = "light";
    document.documentElement.setAttribute("data-color-scheme", resolved);
    updateThemeColorMeta(resolved);

    // Rebuild basemap layers via the existing helper that's already
    // careful to preserve trail/decoration/highlight overlays. A
    // naive map.setStyle(buildStyle()) would replace the WHOLE style
    // including those overlays, trails would vanish until the next
    // page load. rebuildBasemapLayers extracts the overlay layers
    // from the current style, swaps in the new flavor's basemap,
    // and stitches the overlays back in via setStyle({diff: true}).
    // It also re-registers icons and re-applies map paint tokens.
    if (map) rebuildBasemapLayers();
}

// Sync the <meta name="theme-color"> tag with the current scheme so
// installed-PWA mode on Android (and any other UA that paints
// browser chrome from theme-color) flips its status bar between
// the light and dark accent shades. The static value in
// index.html is just the boot-time default; once app.js runs we
// own the value and update it on every applyColorScheme call.
function updateThemeColorMeta(resolved) {
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
        meta = document.createElement("meta");
        meta.setAttribute("name", "theme-color");
        document.head.appendChild(meta);
    }
    // Brand the browser chrome with the per-map accent, matching the
    // resolved scheme's shade. The literal fallbacks mirror
    // style.css's framework-default accent for maps built without a
    // CONFIG palette.
    meta.setAttribute("content", resolved === "dark"
        ? (CONFIG.accentDark || "#258cd0")
        : (CONFIG.accentLight || "#1d6fa5"));
}

// Wire the OS prefers-color-scheme listener. Only takes effect when
// the rider's stored preference is "auto", explicit "light" or
// "dark" wins over OS changes. Fires e.g. on iOS sunset shift if the
// device is set to auto-switch.
function watchSystemColorScheme() {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
        const stored = LS.get("mtb.colorScheme",
            CONFIG.defaultColorScheme || "light");
        if (stored === "auto") applyColorScheme("auto");
    };
    if (mql.addEventListener) {
        mql.addEventListener("change", handler);
    } else if (mql.addListener) {
        // Safari < 14 fallback.
        mql.addListener(handler);
    }
}

// ============================================================
// localStorage helpers, UI state persists per-map under
// `<slug>.mtb.*` keys. localStorage is scoped by origin, not by
// path, so without the slug prefix every map served from the same
// domain (e.g. trailmaps.app/bloomer, trailmaps.app/dte) would
// share state, toggling winter on one would flip the other on
// next visit. Prefixing with CONFIG.slug isolates each map's UI
// prefs. All values are purely-functional UI prefs (not personal
// data); falls under the ePrivacy "strictly necessary" exemption,
// no consent banner required. Unprefixed keys: mtb.seasonMode,
// mtb.emergencyOn, mtb.poi.markers (merged guidepost + emergency
// trail-marker layer), mtb.poi.parking, mtb.poi.trailheads,
// mtb.poi.features, mtb.labels, mtb.difficulty.
// ============================================================
// Per-map "what's visible by default on first visit" gate. The build
// emits CONFIG.defaultVisible as a list of layer names that should
// default to ON; everything else defaults to OFF. Empty list (the
// framework default) → minimal map, rider opts in via Options. The
// YAML knob `default_visible: all` expands to the full layer list at
// build time, so the runtime always sees a clean array. Persistence
// semantics unchanged: once the rider toggles a layer, their LS
// preference wins on subsequent visits, defaults only apply on
// first visit (empty LS for that key).
function isDefaultVisible(name) {
    return (CONFIG.defaultVisible || []).includes(name);
}

// Curator-forced visibility: when a layer name appears in
// CONFIG.forcedVisible, the toggle row is hidden in setupFloatingChrome
// and the layer is rendered visible at boot regardless of LS state /
// default_visible. Use for safety-critical layers (direction arrows on
// flow trails, etc.) or maps where a layer must always be present.
// CONFIG.forcedVisible is the resolved list (after "all" expansion at
// build time). Same shape and accepted-name set as defaultVisible.
function isForcedVisible(name) {
    return (CONFIG.forcedVisible || []).includes(name);
}

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

// Sort comparator for route ids, handles numeric OSM ids and string
// custom ids consistently. With {numeric:true}, "2" sorts before "10",
// and string ids sort lexicographically among themselves.
const ROUTE_ID_COMPARE = (a, b) =>
    String(a).localeCompare(String(b), undefined, { numeric: true });

// ============================================================
// Constants
// ============================================================
// Zoom-interpolated offset expression, multiplies offset_index (from
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
    // Event mode forces difficulty off: the rider's attention belongs
    // on the featured course, and IMBA diamonds are placed on the
    // underlying OSM ways (which span every background route too).
    // There's no clean way to limit them to "the course" because the
    // course is a custom polyline whose synthesized geometry doesn't
    // backflow into OSM ways' shared_routes. The toggle row is hidden
    // in setupOptionsOverlay; this guard also handles initial layer
    // visibility at addAllTrailLayers time.
    if (CONFIG.eventModeActive) return false;
    return LS.get("mtb.difficulty", isDefaultVisible("difficulty")) === true;
}

// ============================================================
// Direction arrows (one-way trails, optional day-of-week alternation)
// ============================================================
const WEEKDAY_NAMES = ["sunday", "monday", "tuesday", "wednesday",
                      "thursday", "friday", "saturday"];

// Bold chevron icon, drawn in canvas like the IMBA difficulty icons so we
// control color, weight, and halo. Light theme only, black arrow with a
// light halo always reads against the casings (which are also black/dark).
//
// The arrow points RIGHT (positive-X). `symbol-placement: line` with
// `icon-rotation-alignment: map` aligns the icon's positive-X axis with the
// line's direction of travel, so an icon drawn pointing right ends up
// pointing along the line; 0° = forward along digitization, 180° =
// reversed (alternating days).
function drawArrow(ctx, size, fillColor, haloColor) {
    const cy    = size / 2;
    const tip   = size * 0.94;   // tip x
    const back  = size * 0.14;   // back-edge x (tail corners)
    const notch = size * 0.32;   // notch x, close to the back so the head
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

// Two arrow icon variants, one tuned for each color scheme.
// MapLibre's icon-image layout property swaps between them via
// applyMapPaintForScheme(). Both are registered at init so the swap
// is instant; setStyle() drops icons but the registerArrowIcons
// re-registration in onStyleLoaded re-uploads both.
const ARROW_ICON_LIGHT_BG_ID = "arrow-light-bg";   // black fill on light basemap
const ARROW_ICON_DARK_BG_ID  = "arrow-dark-bg";    // white fill on dark basemap
const ARROW_VARIANTS = [
    { id: ARROW_ICON_LIGHT_BG_ID, fill: "#000000",            halo: "rgba(255,255,255,0.9)" },
    { id: ARROW_ICON_DARK_BG_ID,  fill: "#ffffff",            halo: "rgba(0,0,0,0.7)" },
];

function registerArrowIcons() {
    const size = 22;
    const ratio = 4;
    for (const variant of ARROW_VARIANTS) {
        if (map.hasImage(variant.id)) continue;
        const canvas = document.createElement("canvas");
        canvas.width = size * ratio;
        canvas.height = size * ratio;
        const ctx = canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        drawArrow(ctx, size, variant.fill, variant.halo);
        map.addImage(variant.id, {
            width: size * ratio,
            height: size * ratio,
            data: ctx.getImageData(0, 0, size * ratio, size * ratio).data,
        }, { pixelRatio: ratio });
    }
}

function todaysReverseRoutes() {
    const now = new Date();
    const weekday = WEEKDAY_NAMES[now.getDay()];
    // Parity token tracks calendar day-of-month parity. Simple getDate()%2,
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

// Recomputed on day-tick (see setInterval in setupFloatingChrome); each
// arrow's rotation is baked into its feature properties at decoration
// build time, so a change here forces a full updateDecorationsSource().
let reverseRoutesToday = todaysReverseRoutes();

// ============================================================
// Decoration system, pre-deconflicted Point features that replace
// the old per-layer `symbol-placement: line` setup. MapLibre places
// line symbols PER TILE: the same feature gets re-anchored inside
// each tile bucket independently, so cross-layer alignment (arrows
// over diamonds over labels) breaks at tile seams regardless of
// `symbol-spacing` math. Pre-computing the decoration positions in
// JS, then rendering them as point features with `icon-allow-overlap:
// true`, sidesteps the entire collision pipeline, the layout we
// compute is exactly what the user sees.
//
// One source (`trail-decorations`), four kinds (trail_name,
// route_name, diamond, arrow), each with `min_zoom` for tiered
// density. Layer filter: `kind == X && min_zoom <= zoom`.
// ============================================================

// String enums for decoration kinds (the `kind` property on every
// trail-decorations feature) and POI types (the `poi_type` property
// on every pois.geojson feature). Centralized so a typo anywhere
// produces a missing-property reference at evaluation time instead
// of silently filtering nothing. The values are the literal strings
// MapLibre filters and the build-time POI emitter compare against,
// if you change a value here you must also update fetch_pois.py.
const KIND = Object.freeze({
    TRAIL_NAME: "trail_name",
    ROUTE_NAME: "route_name",
    DIAMOND:    "diamond",
    ARROW:      "arrow",
    // Point-placed name labels shown only at overview zoom (a single
    // loop/trail name at a representative point), distinct from the
    // curve-following TRAIL_NAME/ROUTE_NAME LineString labels.
    ROUTE_LABEL_PT: "route_label_pt",
    TRAIL_LABEL_PT: "trail_label_pt",
});
const POI = Object.freeze({
    TRAIL_MARKER:    "trail_marker",
    PARKING:         "parking",
    TRAILHEAD:       "trailhead",
    HUB:             "hub",       // named on-trail intersections (curator YAML)
    FEATURE:         "feature",
    TOILET:          "toilet",
    DRINKING_WATER:  "drinking_water",
    EVENT:           "event",   // event_mode.pois, always rendered, no toggle
});

// Escape HTML special characters so untrusted strings (OSM `name=` tags,
// curator-supplied YAML strings that may have been pasted from external
// sources) can be safely interpolated into innerHTML / setHTML sinks.
// Covers <, >, &, ", ', the standard XSS-prevention set; the resulting
// string is safe both as element content and inside attribute values.
//
// We use string interpolation + setHTML for popup construction (rather
// than DOM-builder helpers) because MapLibre's Popup API takes an HTML
// string. Wrapping every interpolated value in escapeHtml() preserves
// that ergonomics without the XSS exposure. Defense in depth: applies
// to BOTH OSM-sourced and curator-sourced strings, the latter is
// "trusted" in the framework's threat model but a curator copy-pasting
// from a wiki page could carry markup unintentionally.
const _ESCAPE_HTML_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
};
function escapeHtml(s) {
    if (s == null) return "";
    return String(s).replace(/[&<>"']/g, (c) => _ESCAPE_HTML_MAP[c]);
}

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
// Calibrated to zoom 15 (~3.5 m/px at lat 42°), at higher zooms the
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
// not WebGL features, so MapLibre's collision can't see them, we
// have to pre-exclude. `_map` check skips markers detached by the
// proximity filter (they're hidden right now).
//
// Cached at module scope: a single user gesture (season toggle,
// emergency mode flip) often triggers several updateDecorationsSource
// calls in a row, all asking for the same obstacle set. invalidate-
// ObstaclesCache() must be called at every site that adds/removes a
// marker (initial creation, updateMarkerProximity, layer-toggle
// handlers), otherwise stale obstacles will let labels/arrows
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
                       trailheadMarkers, hubMarkers, featureMarkers,
                       toiletMarkers, drinkingWaterMarkers]) {
        for (const m of arr) {
            if (m._map !== map) continue;
            const ll = m.getLngLat();
            out.push({ lngLat: [ll.lng, ll.lat], radiusM: r });
        }
    }
    _obstaclesCache = out;
    return out;
}

// Spatial-hash index for O(1)-expected collision lookups.
//
// Both placedCollides() and clipCoordsAroundObstacles() were O(n)
// per check. computeDecorations() makes hundreds of placement
// attempts in its 4-pass loop, net O(n²) on the placed-array
// length. On dense maps (200+ POIs + dozens of decorations) the
// label-clipping + collision-check work was 100-200ms.
//
// The index buckets items into square cells whose side length is
// chosen ≥ the largest collision-radius sum we'll ever check
// (label radius 60 + label radius 60 = 120m → cell 150m gives
// margin). A 3×3-cell query around any candidate covers all
// possible collisions; the worst-case query inspects O(items per
// cell) ≈ a handful on typical maps. Insertions are O(1).
//
// COS-of-anchor-latitude approximation for the lat→meters
// projection introduces <0.5% cell-size error within ±50 km of the
// anchor, irrelevant given the cell-size margin.
const _SPATIAL_INDEX_CELL_M = 150;
const _LAT_M_PER_DEG = 111320;
function makeSpatialIndex(anchorLat) {
    const cosLat = Math.cos((anchorLat * Math.PI) / 180);
    const lngMPerDeg = _LAT_M_PER_DEG * cosLat;
    const grid = new Map();
    function cellKey(lng, lat) {
        const cx = Math.floor((lng * lngMPerDeg) / _SPATIAL_INDEX_CELL_M);
        const cy = Math.floor((lat * _LAT_M_PER_DEG) / _SPATIAL_INDEX_CELL_M);
        return cx * 65537 + cy;  // integer key beats string concatenation
    }
    return {
        add(item) {
            const k = cellKey(item.lngLat[0], item.lngLat[1]);
            const bucket = grid.get(k);
            if (bucket) bucket.push(item);
            else grid.set(k, [item]);
        },
        nearby(lng, lat) {
            // Inline 3×3 scan: explicit unrolling beats nested loops
            // here because the function is in the hot path and 9 keys
            // is small enough to be fully predictable for the JIT.
            const cx = Math.floor((lng * lngMPerDeg) / _SPATIAL_INDEX_CELL_M);
            const cy = Math.floor((lat * _LAT_M_PER_DEG) / _SPATIAL_INDEX_CELL_M);
            const out = [];
            for (let dx = -1; dx <= 1; dx++) {
                for (let dy = -1; dy <= 1; dy++) {
                    const bucket = grid.get((cx + dx) * 65537 + (cy + dy));
                    if (bucket) {
                        for (let i = 0; i < bucket.length; i++) out.push(bucket[i]);
                    }
                }
            }
            return out;
        },
    };
}

function placedCollides(lng, lat, radiusM, placedIndex) {
    const candidates = placedIndex.nearby(lng, lat);
    for (let i = 0; i < candidates.length; i++) {
        const p = candidates[i];
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
// interpolating new vertices at the disc boundary, vertex spacing on
// trail data is already fine enough that the small over/under-clip is
// invisible at viewing zooms.
//
// Uses the spatial index for O(1)-expected obstacle lookups per
// coord vertex; on a 200-vertex way against 100 obstacles, this
// drops from 20k haversine calls to a few hundred.
function clipCoordsAroundObstacles(coords, obstaclesIndex, radius) {
    if (coords.length < 2) return [coords];
    const runs = [];
    let runStart = null;
    for (let i = 0; i < coords.length; i++) {
        const [lng, lat] = coords[i];
        let blocked = false;
        const candidates = obstaclesIndex.nearby(lng, lat);
        for (let j = 0; j < candidates.length; j++) {
            const obs = candidates[j];
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

    const globalRank = modeAwareGlobalRank();

    const ways = [];
    for (const f of routesData.features) {
        if (!featurePassesModeFilter(f)) continue;
        if (f.properties.isStub === true) continue;
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
//
// `placedIndex` is a spatial-hash index (see makeSpatialIndex);
// successful placements add themselves to it so subsequent calls
// see them in collision checks.
function tryPlaceDecoration(way, candidateArcs, kind, minZoom,
                            decorations, placedIndex, extraPropsFn) {
    // Arrows never come through here, they're placed by
    // placeArrowTierAlongChains, which builds its features directly.
    const radius = (kind === KIND.DIAMOND) ? DECOR_RADIUS_M.diamond
                 :                           DECOR_RADIUS_M.label;
    for (const arc of candidateArcs) {
        if (arc < 0 || arc > way.totalLength) continue;
        const pt = pointAtArcLength(way.segments, way.totalLength, arc);
        if (!pt) continue;
        if (placedCollides(pt.lng, pt.lat, radius, placedIndex)) continue;
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
        placedIndex.add({ lngLat: [pt.lng, pt.lat], radiusM: radius });
        return true;
    }
    return false;
}

// ---- Decoration density (min_zoom gates; eye-tunable) ----
// Direction arrows AND difficulty diamonds share the ladder below. The
// overview band shows markers spaced along each connected run so every
// stretch reads at a glance without per-way speckle; the ladder then
// layers on one rung of markers per zoom level.
const DECOR_MZ_RUN     = 0;   // run-spaced overview markers
const DECOR_MZ_PER_WAY = 14;  // 2 diamonds per physical way

// ---- Screen-space density ladder ----
// Fixed meter cadences (the old 500 m overview / 400 m / 200 m tiers)
// double their on-screen spacing across every zoom level inside a tier,
// then snap back where the next tier gates in, a 2-4x density sawtooth
// that read as "sometimes sparse, sometimes busy" (4 px overview spacing
// at z10 on a big network; 450 px gaps at z18). The ladder replaces
// them: one rung per zoom level, each rung's ground cadence chosen so
// markers sit ~DECOR_TARGET_SPACING_PX apart ON SCREEN at the zoom where
// the rung gates in. Coarse rungs place first and finer rungs nest into
// their gaps via the shared collision index, so placements stay put as
// the rider zooms in.
const DECOR_TARGET_SPACING_PX = 110;
// Finest cadence the ladder emits. Caps total feature count on big
// networks (a 100 km one-way system stays ~1-2k arrows, not 10k); past
// the zoom where the floor engages, on-screen spacing grows again,
// acceptable close-in, where the rider is inspecting a specific trail.
const DECOR_CADENCE_FLOOR_M = 100;

// Meters of ground per CSS pixel at the map's own latitude (Web
// Mercator), for converting a target screen spacing into a ground
// cadence per zoom level.
function decorMetersPerPixel(zoom) {
    const lat = (CONFIG.bbox[1] + CONFIG.bbox[3]) / 2;
    return 156543.03392 * Math.cos(lat * Math.PI / 180) / Math.pow(2, zoom);
}

// Build [{ minZoom, cadenceM }] rungs covering fromZoom..toZoom. Stops
// early once the floor engages, finer rungs would just repeat it.
function decorCadenceLadder(targetPx, floorM, fromZoom, toZoom) {
    const rungs = [];
    for (let z = fromZoom; z <= toZoom; z++) {
        const c = targetPx * decorMetersPerPixel(z);
        rungs.push({ minZoom: z, cadenceM: Math.max(c, floorM) });
        if (c <= floorM) break;
    }
    return rungs;
}

// Icon ladder: first rung one stop above the map minimum (the run tier
// owns the map-wide overview), last at maxZoom or wherever the cadence
// floor engages.
const DECOR_LADDER = decorCadenceLadder(DECOR_TARGET_SPACING_PX,
    DECOR_CADENCE_FLOOR_M, CONFIG.minZoom + 1, CONFIG.maxZoom);

// Ground spacing between overview (run-tier) markers, the same screen
// target, anchored at the map's minimum zoom where the whole system is
// in view. Every run still gets at least one marker regardless.
const DECOR_OVERVIEW_SPACING_M =
    DECOR_TARGET_SPACING_PX * decorMetersPerPixel(CONFIG.minZoom);

// Zoom at which the curve-following on-path labels become ELIGIBLE (their
// minzoom). Set to 16, deliberately ABOVE the per-way arrow tier
// (DECOR_MZ_PER_WAY = 14). When this aliased 14, on-path labels switched on
// at the same zoom as the dense per-way arrows and, because everything is
// compressed together on screen at that zoom, drew straight over them.
// Holding on-path labels to 16 lets the map spread out first, so the text
// lands in the gaps between arrows, and keeps the single overview point
// label as the shown label across more of the zoom range.
const POINT_LABEL_MAX_ZOOM = 16;

// On-path line layers' minzoom. Clamped one stop under the map's own maxZoom
// so a low-maxZoom regional map that never reaches 16 still gets an on-path
// band rather than overview-only.
const LABEL_CROSSOVER_ZOOM = Math.min(POINT_LABEL_MAX_ZOOM, CONFIG.maxZoom - 1);

// Overview point labels (the single on-path name per route/trail) stay
// eligible until one stop INTO the on-path band, then hand off via this
// maxzoom. The one-zoom overlap with LABEL_CROSSOVER_ZOOM is intentional: on
// twisty switchbacked trails the curve-following line label can't fit a 45°
// run right at the crossover, so the overview label persists through the
// overlap instead of leaving a dead band with no label at all. On straight
// trails the line label places immediately and the overview point label
// drops out one zoom later, a brief, single-zoom co-existence, not a
// permanent double.
const OVERVIEW_LABEL_MAX_ZOOM = LABEL_CROSSOVER_ZOOM + 1;
// Set false to drop the trails-mode point label (keep routes-mode only)
// if per-trail overview labels prove too cluttered.
const POINT_LABELS_IN_TRAILS_MODE = true;

// Endpoint key for way-connectivity matching. OSM-derived way geometry
// shares exact node coordinates at junctions; rounding to ~0.1 m guards
// against float drift from any upstream processing.
function endpointKey(lng, lat) {
    return lng.toFixed(6) + "," + lat.toFixed(6);
}

// Group ways into maximal connected runs. `isEligible(way)` selects the
// participating ways; two eligible ways join when they meet at an endpoint,
// share at least one route id (so two loops crossing at a junction stay
// separate), AND share the same `groupKey(way)`. For arrows the key is
// constant (direction only); for diamonds it's the difficulty, so a black
// pitch inside a blue loop forms its own run and keeps its own overview
// marker. Returns one entry per run: { ways: [...] }.
function computeConnectedRuns(ways, isEligible, groupKey) {
    const eligible = ways.filter(isEligible);
    const parent = eligible.map((_, i) => i);
    const find = (x) => {
        while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
    };
    const union = (a, b) => {
        const ra = find(a), rb = find(b);
        if (ra !== rb) parent[ra] = rb;
    };

    const atNode = new Map();   // endpoint key -> [{ idx, routes, gk }]
    for (let i = 0; i < eligible.length; i++) {
        const c = eligible[i].coords;
        const routes = new Set(
            (eligible[i].sharedRoutes && eligible[i].sharedRoutes.length)
                ? eligible[i].sharedRoutes : [eligible[i].routeId]);
        const gk = groupKey(eligible[i]);
        for (const e of [c[0], c[c.length - 1]]) {
            const k = endpointKey(e[0], e[1]);
            let bucket = atNode.get(k);
            if (!bucket) { bucket = []; atNode.set(k, bucket); }
            for (const other of bucket) {
                if (other.gk !== gk) continue;
                for (const r of routes) {
                    if (other.routes.has(r)) { union(i, other.idx); break; }
                }
            }
            bucket.push({ idx: i, routes, gk });
        }
    }

    const runs = new Map();
    for (let i = 0; i < eligible.length; i++) {
        const root = find(i);
        let run = runs.get(root);
        if (!run) { run = { ways: [] }; runs.set(root, run); }
        run.ways.push(eligible[i]);
    }
    return Array.from(runs.values());
}

// Place run-tier overview markers: walk each run's member ways longest-
// first and drop a marker every ~spacingM of straight-line distance
// (tracked in `acceptedPts`), guaranteeing at least one per run even when a
// neighbor run already sits within spacing. Straight-line (not arc)
// spacing keeps overview density even in screen space, so tight switchback
// clusters don't each demand a marker. Each placement clears POI/marker
// footprints via the shared `placed` index and registers itself there so
// detail-tier markers don't stack on it. extraPropsFn(way, pt) supplies the
// kind-specific feature properties.
function placeOverviewRuns(runs, kind, radius, minZoom, spacingM,
                           decorations, placed, extraPropsFn) {
    const acceptedPts = [];
    const farEnough = (lng, lat) => {
        for (const p of acceptedPts) {
            if (haversineMeters(lng, lat, p[0], p[1]) < spacingM) return false;
        }
        return true;
    };
    const place = (w, frac, ignoreSpacing) => {
        const L = w.totalLength;
        for (const f of [frac, frac - 0.06, frac + 0.06]) {
            if (f <= 0 || f >= 1) continue;
            const pt = pointAtArcLength(w.segments, L, L * f);
            if (!pt) continue;
            if (!ignoreSpacing && !farEnough(pt.lng, pt.lat)) continue;
            if (placedCollides(pt.lng, pt.lat, radius, placed)) continue;
            decorations.push({
                type: "Feature",
                geometry: { type: "Point", coordinates: [pt.lng, pt.lat] },
                properties: { kind, min_zoom: minZoom, ...extraPropsFn(w, pt) },
            });
            placed.add({ lngLat: [pt.lng, pt.lat], radiusM: radius });
            acceptedPts.push([pt.lng, pt.lat]);
            return true;
        }
        return false;
    };
    for (const run of runs) {
        const members = run.ways.slice()
            .sort((a, b) => b.totalLength - a.totalLength);
        let placedAny = false;
        for (const w of members) {
            const n = Math.max(1, Math.round(w.totalLength / spacingM));
            for (let kk = 1; kk <= n; kk++) {
                if (place(w, kk / (n + 1), false)) placedAny = true;
            }
        }
        if (!placedAny && members.length) {
            place(members[0], 0.5, true);   // guarantee >=1 per run
        }
    }
}

// Order a connected one-way run's ways into maximal head-to-tail chains.
// A chain is a simple path: it breaks at branch nodes (degree >= 3) and
// run endpoints, so the detail arrow tiers can space arrows with one
// continuous arc cursor across way boundaries (no per-segment phase
// reset, that reset is what made arrows bunch where a route changes
// trail name or difficulty). Coordinates are never flipped, bearings
// must keep encoding one-way travel direction, so each entry's `forward`
// flag only tells the parameterizer which way the cursor runs that way.
function buildChains(run) {
    const ways = run.ways;
    const hk = new Map();   // way -> head endpoint key
    const tk = new Map();   // way -> tail endpoint key
    const adj = new Map();  // endpoint key -> [{ way, end }]
    for (const w of ways) {
        const c = w.coords;
        const h = endpointKey(c[0][0], c[0][1]);
        const t = endpointKey(c[c.length - 1][0], c[c.length - 1][1]);
        hk.set(w, h);
        tk.set(w, t);
        for (const [k, end] of [[h, "head"], [t, "tail"]]) {
            let b = adj.get(k);
            if (!b) { b = []; adj.set(k, b); }
            b.push({ way: w, end });
        }
    }
    const degree = (k) => (adj.get(k) || []).length;
    const used = new Set();
    // Walk a maximal simple chain, entering `startWay` through `entryKey`
    // and continuing only through degree-2 nodes.
    const walkFrom = (startWay, entryKey) => {
        const chain = [];
        let w = startWay;
        let entry = entryKey;
        while (w && !used.has(w)) {
            used.add(w);
            const forward = (entry === hk.get(w));
            chain.push({ way: w, forward });
            const exit = forward ? tk.get(w) : hk.get(w);
            if (degree(exit) !== 2) break;          // branch or dead end
            let next = null;
            for (const cand of adj.get(exit)) {
                if (cand.way !== w && !used.has(cand.way)) {
                    next = cand.way;
                    break;
                }
            }
            if (!next) break;                        // cycle closed onto a used way
            entry = exit;
            w = next;
        }
        return chain;
    };
    const chains = [];
    // Open chains start at degree-1 endpoints.
    for (const [k, bucket] of adj) {
        if (bucket.length === 1 && !used.has(bucket[0].way)) {
            chains.push(walkFrom(bucket[0].way, k));
        }
    }
    // Each unused arm of a branch node (degree >= 3) seeds its own chain.
    for (const [k, bucket] of adj) {
        if (bucket.length >= 3) {
            for (const cand of bucket) {
                if (!used.has(cand.way)) chains.push(walkFrom(cand.way, k));
            }
        }
    }
    // Remaining ways form pure (all-degree-2) cycles.
    for (const w of ways) {
        if (!used.has(w)) chains.push(walkFrom(w, hk.get(w)));
    }
    return chains;
}

// Flatten a chain into a continuous arc parameterization: one entry per
// source segment, in cursor order, tagged with the owning way and the
// segment's own arcStart. `fwd` = the cursor runs this segment head->tail.
function chainParam(chain) {
    const entries = [];
    let cursor = 0;
    for (const { way, forward } of chain) {
        const segs = way.segments;
        if (forward) {
            for (let i = 0; i < segs.length; i++) {
                const s = segs[i];
                entries.push({ owner: way, chainStart: cursor,
                    lengthM: s.lengthM, fwd: true, segArcStart: s.arcStart });
                cursor += s.lengthM;
            }
        } else {
            for (let i = segs.length - 1; i >= 0; i--) {
                const s = segs[i];
                entries.push({ owner: way, chainStart: cursor,
                    lengthM: s.lengthM, fwd: false, segArcStart: s.arcStart });
                cursor += s.lengthM;
            }
        }
    }
    return { entries, chainLength: cursor };
}

// Resolve a chain-arc distance to a map point + owning way. Bearing comes
// from the owner's own segment (head->tail = the one-way travel
// direction), regardless of which way the cursor ran the chain.
function sampleChain(entries, a) {
    for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        if (a <= e.chainStart + e.lengthM) {
            const t = e.lengthM === 0 ? 0 : (a - e.chainStart) / e.lengthM;
            const ownerArc = e.fwd
                ? e.segArcStart + t * e.lengthM
                : e.segArcStart + (1 - t) * e.lengthM;
            const pt = pointAtArcLength(e.owner.segments,
                e.owner.totalLength, ownerArc);
            return pt ? { pt, owner: e.owner } : null;
        }
    }
    return null;
}

// Place direction arrows at an even `cadenceM` ground spacing along each
// one-way chain, phase carried across way boundaries so spacing stays
// consistent where a route changes trail name or difficulty. Shares the
// `placed` collision index (so arrows dodge POIs, diamonds, and each
// other) and registers every placement; call coarse rungs before fine so
// finer rungs nest into the gaps. `guarantee` forces at least one arrow
// per chain, passed on a single mid-ladder rung (see the ladder pass)
// so every chain shows direction by detail zoom without speckling the
// overview zooms of branchy networks with one arrow per chain arm.
// Arrow-specific (rotation / reverse-day); add an extraPropsFn to reuse
// the chain machinery for other icon kinds.
function placeArrowTierAlongChains(chains, cadenceM, minZoom, decorations,
                                   placed, guarantee) {
    const R = DECOR_RADIUS_M.arrow;
    const reverseSet = reverseRoutesToday;
    for (const chain of chains) {
        const { entries, chainLength } = chainParam(chain);
        if (chainLength === 0) continue;
        const emit = (a) => {
            const s = sampleChain(entries, a);
            if (!s) return false;
            if (placedCollides(s.pt.lng, s.pt.lat, R, placed)) return false;
            const reverse = reverseSet.has(s.owner.routeId)
                || s.owner.sharedRoutes.some((id) => reverseSet.has(id));
            decorations.push({
                type: "Feature",
                geometry: { type: "Point", coordinates: [s.pt.lng, s.pt.lat] },
                properties: {
                    kind: KIND.ARROW,
                    min_zoom: minZoom,
                    rotation: arrowRotateForBearing(s.pt.bearing, reverse),
                    trail_name: s.owner.trailName,
                    shared_routes: s.owner.sharedRoutes,
                },
            });
            placed.add({ lngLat: [s.pt.lng, s.pt.lat], radiusM: R });
            return true;
        };
        let placedAny = false;
        for (let a = cadenceM / 2; a < chainLength; a += cadenceM) {
            if (emit(a)) placedAny = true;
        }
        if (!placedAny && guarantee) {
            // Short or fully-contested chain: still mark direction once.
            for (const f of [0.5, 0.4, 0.6, 0.3, 0.7]) {
                if (emit(chainLength * f)) break;
            }
        }
    }
}

// Pick a point ON `way` to anchor an overview label, preferring a stretch
// clear of already-placed obstacles (POIs / markers). Tries the arc-length
// midpoint first, then points either side, and returns the first with
// clearance; falls back to the midpoint if none are clear (the label still
// renders and MapLibre resolves any residual overlap at draw time). Returns
// null only for a degenerate, segment-less way.
function chooseOnPathLabelPoint(way, placed, radiusM) {
    const fracs = [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74];
    let fallback = null;
    for (const fr of fracs) {
        const pt = pointAtArcLength(way.segments, way.totalLength,
            way.totalLength * fr);
        if (!pt) continue;
        if (!fallback) fallback = pt;
        if (!placedCollides(pt.lng, pt.lat, radiusM, placed)) return pt;
    }
    return fallback;
}

// Build the full decoration FeatureCollection for the current visible
// route set. Order matters:
//   Pass 1: line labels (symbol-placement: line; text follows the
//              trail curve; not pre-deconflicted)
//   Pass 1.5: run-tier overview markers: arrows and diamonds spaced
//              along each connected run, placed first so detail-tier
//              markers can't double up on them
//   Pass 1.7: overview point labels (one loop/trail name pinned on the
//              trail; placed AFTER the run-tier markers so those keep
//              priority, reserves its footprint so the per-way/sweep tiers
//              below deconflict around it; maxzoom hands to line labels)
//   Pass 2: per-way mandatory diamonds (2 per applicable way) so
//              even short trails get difficulty markings
//   Pass 3: density ladder: one diamond sweep + one arrow sweep per
//              zoom rung (see DECOR_LADDER), coarse to fine
// Arrows and diamonds share the DECOR_LADDER rungs above. Each rung's
// candidates are deconflicted against everything already placed,
// including obstacle markers gathered up front.
// Event mode restricts route-name labels to the featured route(s) so
// the muted background network never labels itself. Non-event maps
// label every route, so this gate is a no-op there. Mirrors the
// per-route shared-way label layers, which are only created for
// featured routes (see the trail-label-<id> addLayer loop).
function routeLabelAllowed(rid) {
    if (!CONFIG.eventModeActive) return true;
    return !!(CONFIG.routes[rid] && CONFIG.routes[rid].featured);
}

function computeDecorations() {
    const decorations = [];
    // Build a spatial-hash index of all collision targets. Seed with
    // the cached obstacle markers (POIs the placer must avoid), then
    // grow it as decorations land via tryPlaceDecoration. Anchor the
    // lat→meters projection at the map's bbox-center lat so cell
    // sizing is accurate within the visible area.
    const anchorLat = (CONFIG.bbox[1] + CONFIG.bbox[3]) / 2;
    const placed = makeSpatialIndex(anchorLat);
    const obstacleArr = gatherObstacles();
    for (let i = 0; i < obstacleArr.length; i++) placed.add(obstacleArr[i]);
    const ways = collectCanonicalWays();

    // Process longer ways first so their labels claim space before
    // shorter ways' labels, short trails are more likely to drop
    // their label, which is the desired tradeoff.
    ways.sort((a, b) => b.totalLength - a.totalLength);

    const reverseSet = reverseRoutesToday;
    const arrowsAllowed = CONFIG.showDirectionArrows !== false;

    // ---- Pass 1: labels (one or more LineString runs per way;
    //      MapLibre's symbol-placement:line auto-curves text along the
    //      trail). The way coords are clipped around DOM markers so
    //      labels never cross a marker icon. Diamonds and arrows
    //      placed in later passes don't need JS clipping, they're
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
        if (way.soloRouteName && routeLabelAllowed(way.soloRouteId)) {
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

    // ---- Pass 1.5: run-tier overview markers, arrows and diamonds
    //      spaced along each connected run so every directional stretch
    //      and difficulty band reads when zoomed out, without the per-way
    //      speckle. Placed before the per-way passes so they seed the
    //      collision index and coincident detail-tier markers get
    //      suppressed. ----
    if (arrowsAllowed) {
        const arrowRuns = computeConnectedRuns(ways,
            (w) => w.oneway === "yes" || w.oneway === "reversible",
            () => "");
        placeOverviewRuns(arrowRuns, KIND.ARROW, DECOR_RADIUS_M.arrow,
            DECOR_MZ_RUN, DECOR_OVERVIEW_SPACING_M, decorations, placed,
            (w, pt) => {
                const reverse = reverseSet.has(w.routeId)
                    || w.sharedRoutes.some((id) => reverseSet.has(id));
                return {
                    rotation: arrowRotateForBearing(pt.bearing, reverse),
                    trail_name: w.trailName,
                    shared_routes: w.sharedRoutes,
                };
            });
    }
    const diamondRuns = computeConnectedRuns(ways,
        (w) => ["0", "1", "2", "3", "4", "5"].includes(w.imba),
        (w) => w.imba);
    placeOverviewRuns(diamondRuns, KIND.DIAMOND, DECOR_RADIUS_M.diamond,
        DECOR_MZ_RUN, DECOR_OVERVIEW_SPACING_M, decorations, placed,
        (w) => ({
            imba_difficulty: w.imba,
            trail_name: w.trailName,
            shared_routes: w.sharedRoutes,
        }));

    // ---- Pass 1.7: overview point labels, one name per visible route, and
    //      (trails mode) per named trail, pinned ON the trail at a low-clutter
    //      point on its longest way. Placed AFTER the run-tier overview
    //      markers above so those sparse, zoomed-out arrows/diamonds keep
    //      priority and aren't starved, but BEFORE the per-way and sweep
    //      tiers below, whose markers it reserves a footprint against so they
    //      don't drop on the label text at runtime. Only the modes the map
    //      actually supports are emitted: a routes-only map doesn't reserve
    //      trail-label discs (labels that could never show, yet would thin the
    //      marker field). The point-label layers carry a maxzoom that hands
    //      these back to the curve-following line labels up close;
    //      symbol_sort_key (negative length) lets the longest names win
    //      MapLibre's collision drop among themselves. ----
    const emitOverviewLabel = (text, way, extraProps) => {
        // Reserve a modest clear disc around the label so the per-way / sweep
        // marker passes don't drop an icon on the text. Kept small (scaled by
        // name length) so it nudges the dense tiers aside without punching
        // holes in the marker field, the run-tier markers above are already
        // placed and untouched. Meters: the on-screen clearance it buys grows
        // with zoom, biting hardest at z15-16 where the tiers are densest.
        const r = Math.min(120, 45 + text.length * 6);
        const pt = chooseOnPathLabelPoint(way, placed, r);
        if (!pt) return;
        decorations.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: [pt.lng, pt.lat] },
            properties: Object.assign({
                min_zoom: 0,
                text: text,
                symbol_sort_key: -Math.round(way.totalLength),
                shared_routes: way.sharedRoutes,
            }, extraProps),
        });
        placed.add({ lngLat: [pt.lng, pt.lat], radiusM: r });
    };
    // Route name labels, gated per-route by routeLabelAllowed (which
    // reflects the current label mode); the muted event-mode network
    // stays unlabeled there.
    {
        const routeAgg = new Map();   // routeId -> { n, longest }
        for (const way of ways) {
            const rids = (way.sharedRoutes && way.sharedRoutes.length)
                ? way.sharedRoutes : [way.routeId];
            for (const rid of rids) {
                if (!visibleRoutes.has(rid)) continue;
                let agg = routeAgg.get(rid);
                if (!agg) { agg = { n: 0, longest: way }; routeAgg.set(rid, agg); }
                agg.n++;
                if (way.totalLength > agg.longest.totalLength) agg.longest = way;
            }
        }
        for (const [rid, agg] of routeAgg) {
            if (!routeLabelAllowed(rid)) continue;
            const name = (CONFIG.routes[rid] && CONFIG.routes[rid].name) || "";
            if (!name || agg.n === 0) continue;
            emitOverviewLabel(name, agg.longest,
                { kind: KIND.ROUTE_LABEL_PT, solo_route_id: rid });
        }
    }
    if (POINT_LABELS_IN_TRAILS_MODE && CONFIG.showTrails !== false) {
        const trailLongest = new Map();   // trailName -> longest way
        for (const way of ways) {
            if (!way.trailName) continue;
            const cur = trailLongest.get(way.trailName);
            if (!cur || way.totalLength > cur.totalLength) {
                trailLongest.set(way.trailName, way);
            }
        }
        for (const [tname, way] of trailLongest) {
            emitOverviewLabel(tname, way,
                { kind: KIND.TRAIL_LABEL_PT, trail_name: tname });
        }
    }

    // Order the one-way runs into head-to-tail chains once, so the detail
    // arrow tiers below space arrows with a single continuous arc cursor
    // across way boundaries (no per-segment phase reset).
    const arrowChains = arrowsAllowed
        ? computeConnectedRuns(ways,
            (w) => w.oneway === "yes" || w.oneway === "reversible",
            () => "").flatMap(buildChains)
        : [];

    // ---- Pass 2: per-way mandatory diamonds, 2 per applicable way so
    //      even short trails get clear difficulty markings. (Arrows are
    //      handled by the ladder's continuous chain sweeps below.) ----
    for (const way of ways) {
        const L = way.totalLength;
        const hasDiamond = ["0", "1", "2", "3", "4", "5"].includes(way.imba);

        if (hasDiamond) {
            // Two anchors: ~quarter and ~three-quarter, each with
            // local fallbacks if the primary slot collides.
            const anchors = [
                [L * 0.25, L * 0.15, L * 0.35, L * 0.10],
                [L * 0.75, L * 0.85, L * 0.65, L * 0.90],
            ];
            for (const cand of anchors) {
                tryPlaceDecoration(way, cand, KIND.DIAMOND, DECOR_MZ_PER_WAY,
                    decorations, placed, () => ({
                        imba_difficulty: way.imba,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
    }
    // ---- Pass 3: density ladder, one rung per zoom level (see
    //      DECOR_LADDER). Each rung sweeps diamonds along every rated
    //      way and arrows along every one-way chain at the rung's
    //      cadence. Diamonds place first within a rung so difficulty
    //      icons keep priority and arrows fill the space around them.
    //      On finer rungs, candidates coincident with coarser
    //      placements get dropped by the collision check, so each rung
    //      fills gaps rather than stacking. ----
    let arrowGuaranteeSpent = false;
    for (let ri = 0; ri < DECOR_LADDER.length; ri++) {
        const rung = DECOR_LADDER[ri];
        for (const way of ways) {
            if (!["0", "1", "2", "3", "4", "5"].includes(way.imba)) continue;
            for (let arc = rung.cadenceM; arc < way.totalLength;
                 arc += rung.cadenceM) {
                tryPlaceDecoration(way, [arc], KIND.DIAMOND, rung.minZoom,
                    decorations, placed, () => ({
                        imba_difficulty: way.imba,
                        trail_name: way.trailName,
                        shared_routes: way.sharedRoutes,
                    }));
            }
        }
        if (arrowsAllowed) {
            // Spend the per-chain guarantee exactly once, on the first
            // rung at or past the per-way zoom (or the final rung on a
            // low-maxZoom map whose ladder never reaches it): by detail
            // zoom every chain shows direction, while the coarse
            // overview rungs stay free of per-chain speckle.
            const guarantee = !arrowGuaranteeSpent
                && (rung.minZoom >= DECOR_MZ_PER_WAY
                    || ri === DECOR_LADDER.length - 1);
            if (guarantee) arrowGuaranteeSpent = true;
            placeArrowTierAlongChains(arrowChains, rung.cadenceM,
                rung.minZoom, decorations, placed, guarantee);
        }
    }

    return { type: "FeatureCollection", features: decorations };
}

// Refresh the trail-decorations source. Cheap to recompute (~ms for
// most maps); called on initial load, route-visibility change, marker
// proximity change, and day-tick (when reverseRoutesToday flips).
// updateDecorationsSource, schedule a decoration recompute.
// computeDecorations() is the expensive pass (50-200 ms on dense maps:
// 4-pass placement + collision-checked label clipping), and callers
// legitimately overlap: applyVisibilityChange() alone reaches here
// twice per toggle (updateTrailDisplay + updateMarkerProximity). Two
// mechanisms keep it to one real pass:
//
//   - Boot gate: the FIRST pass is deferred to map.once("idle") (see
//     loadTrails) so basemap + trail lines paint before the placer
//     runs. Calls arriving earlier (setupFloatingChrome →
//     applyVisibilityChange runs inside style.load, before that idle)
//     are dropped, they used to run the placer 2-3× synchronously
//     on the critical path, defeating the documented deferral.
//   - rAF coalescing: after boot, back-to-back calls within one frame
//     collapse into a single recompute on the next frame. State reads
//     (visibleRoutes, obstacle cache, toggles) happen inside the rAF
//     callback, so the pass always sees the final state of the burst.
let _decorationsReady = false;   // flipped by loadTrails' map.once("idle")
let _decorationsScheduled = false;

function updateDecorationsSource() {
    if (!_decorationsReady || _decorationsScheduled) return;
    _decorationsScheduled = true;
    requestAnimationFrame(() => {
        _decorationsScheduled = false;
        const src = map.getSource("trail-decorations");
        if (src) src.setData(computeDecorations());
    });
}

// Add the four decor layers, kind-filtered with a tier gate
// (`min_zoom <= zoom`) so density grows with zoom.
//
// Icons (diamond, arrow) use `icon-allow-overlap: true` because their
// positions were already deconflicted in computeDecorations and we
// want exactly the placements we chose. They use `icon-ignore-
// placement: false` so they DO register in MapLibre's collision index
// which lets the label layers see them and shift labels out of the
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
        minzoom: LABEL_CROSSOVER_ZOOM,
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
        minzoom: LABEL_CROSSOVER_ZOOM,
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
            // Initial icon-image keyed off current scheme; swapped on
            // scheme change by applyMapPaintForScheme.
            "icon-image": MAP_PAINT_TOKENS[currentColorScheme()].arrowIcon,
            "icon-size": ["interpolate", ["linear"], ["zoom"],
                12, 0.5, 14, 0.7, 18, 1.0],
            "icon-rotate": ["get", "rotation"],
            "icon-rotation-alignment": "map",
            "icon-allow-overlap": true,
            // Register in the collision index so labels avoid us; see
            // the matching comment on decor-diamond above.
            "icon-ignore-placement": false,
            // Initial visibility from the rider's persisted toggle
            // state (or the per-map default_visible default if no
            // LS value yet). Wired to the Direction-arrows toggle in
            // setupFloatingChrome.
            "visibility": directionArrowsToggleOn() ? "visible" : "none",
        },
    });

    // Overview point labels, a single loop/trail name pinned ON the trail
    // (computeDecorations Pass 1.6 reserves its footprint so arrows/diamonds
    // deconflict around it). maxzoom OVERVIEW_LABEL_MAX_ZOOM hands off to the
    // curve-following line label (eligible one stop earlier, at
    // LABEL_CROSSOVER_ZOOM): below the crossover this point label is the sole,
    // reliably-placed overview name; the one-zoom overlap covers twisty trails
    // where curve text can't fit yet (see the OVERVIEW_LABEL_MAX_ZOOM
    // comment). symbol-sort-key (negative length) makes the longest names win
    // MapLibre's overlap drop among themselves.
    map.addLayer({
        id: "decor-route-name-pt",
        type: "symbol",
        source: "trail-decorations",
        maxzoom: OVERVIEW_LABEL_MAX_ZOOM,
        filter: ["all",
            ["==", ["get", "kind"], KIND.ROUTE_LABEL_PT],
            ["<=", ["get", "min_zoom"], ["zoom"]],
        ],
        layout: {
            "symbol-placement": "point",
            "text-field": ["get", "text"],
            "text-font": ["Noto Sans Regular"],
            // Track the line label's growth (10->14:13, 18:16) so where the
            // overview label persists into the on-path band it doesn't read
            // frozen-small next to its neighbors.
            "text-size": ["interpolate", ["linear"], ["zoom"], 10, 11, 13, 13, 18, 15],
            "text-padding": 4,
            "symbol-sort-key": ["get", "symbol_sort_key"],
            "visibility": labelMode === "routes" ? "visible" : "none",
        },
        paint: {
            "text-color": "#1a1a1a",
            "text-halo-color": "rgba(255,255,255,0.9)",
            "text-halo-width": 2,
        },
    });
    map.addLayer({
        id: "decor-trail-name-pt",
        type: "symbol",
        source: "trail-decorations",
        maxzoom: OVERVIEW_LABEL_MAX_ZOOM,
        filter: ["all",
            ["==", ["get", "kind"], KIND.TRAIL_LABEL_PT],
            ["<=", ["get", "min_zoom"], ["zoom"]],
        ],
        layout: {
            "symbol-placement": "point",
            "text-field": ["get", "text"],
            "text-font": ["Noto Sans Regular"],
            // See decor-route-name-pt above, match the line label's growth.
            "text-size": ["interpolate", ["linear"], ["zoom"], 10, 11, 13, 13, 18, 15],
            "text-padding": 4,
            "symbol-sort-key": ["get", "symbol_sort_key"],
            "visibility": labelMode === "trails" ? "visible" : "none",
        },
        paint: {
            "text-color": "#1a1a1a",
            "text-halo-color": "rgba(255,255,255,0.9)",
            "text-halo-width": 2,
        },
    });
}

function directionArrowsToggleOn() {
    // Curator-suppressed: when CONFIG.showDirectionArrows is false,
    // the layer is force-hidden, the toggle row is hidden in
    // setupFloatingChrome, and computeDecorations skips arrow
    // placement. Wins over forced_visible (the "show" gate is the
    // outer envelope; force-on only applies when arrows are shown at
    // all). Use for aesthetic maps that should never display
    // directional indicators regardless of OSM tagging.
    if (CONFIG.showDirectionArrows === false) return false;
    // Curator-forced visibility: when "direction_arrows" is in
    // CONFIG.forcedVisible, the layer is always visible regardless
    // of LS / default. The toggle row is hidden in
    // setupFloatingChrome so the rider never sees an off control.
    // Use for safety-critical maps where wrong-way travel on
    // directional flow trails would be dangerous.
    if (isForcedVisible("direction_arrows")) return true;
    return LS.get("mtb.directionArrows", isDefaultVisible("direction_arrows")) === true;
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
// changes. Name-label visibility is handled in updateLabels(): all name
// labels narrow to a highlighted ROUTE there, while TRAIL highlights
// leave labels un-narrowed for wayfinding, only diamonds / arrows
// narrow here.
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
// Labels: 3-state mode (routes / trails / none). CONFIG.defaultLabels
// is the per-map default (defaults to "none" framework-wide). Once
// the rider picks a mode, LS persists their choice.
//
// Event mode override: when CONFIG.eventModeActive is true, the label
// mode is locked to "routes" (so the featured route's name shows)
// and the rider's persisted preference is ignored. The Labels
// segmented control is hidden in setupOptionsOverlay so they have
// no way to flip it. Route labels then render exactly as on a normal
// map (overview point label at low zoom handing off to curve-following
// on-path labels up close), but only for the featured route(s): the
// per-route shared-way layers are created for featured routes alone,
// and the trail-decorations route labels are gated at emission via
// routeLabelAllowed() so the muted background network stays unlabeled.
//
// forced_labels override: when CONFIG.forcedLabels is set (one of
// "routes", "trails", "none"), labelMode is locked to that value
// and the segmented control is hidden, same as event mode. Distinct
// from defaultLabels (which seeds the initial value but lets the
// rider override via Options). Validated at build time so a config
// can't force a mode that contradicts show_trails.
let labelMode = CONFIG.eventModeActive
    ? "routes"
    : (CONFIG.forcedLabels
        ? CONFIG.forcedLabels
        : LS.get("mtb.labels", CONFIG.defaultLabels || "none"));

// Bucket-model state
let seasonMode = LS.get("mtb.seasonMode", "summer"); // "summer" | "winter"
let emergencyOn = LS.get("mtb.emergencyOn", isDefaultVisible("emergency"));

// Single-highlight invariant: at most one route OR trail highlighted.
let highlight = null; // { kind: "route"|"trail", key: string } | null

// Indexes derived once at startup
let routeIndex = []; // [{ id, name, color, summer, winter, emergency, isCustom, distanceM, elevationGainM, elevationLossM }]
let trailIndex = []; // [{ name, routeIds: [string] }]
let poiIndex = [];   // [{ uid, type, name, lng, lat, ref }]
// Active filter for the search overlay: "all" | "route" | "trail" | "poi".
// Defaults to "all". Updated by chip clicks; consumed by rebuildFinderList.
let currentSearchFilter = "all";

// Index (within the rendered .finder-row buttons) of the currently
// keyboard-active row. -1 means no row is active, the user hasn't
// pressed a navigation key yet, or has typed since (which resets it).
// Drives the .is-active class + aria-activedescendant on the input.
let _finderActiveIndex = -1;

// Marker arrays, trailMarkerMarkers covers the merged guidepost +
// emergency-access-point category (single "Markers" toggle).
let trailMarkerMarkers = [];
let parkingMarkers = [];
let trailheadMarkers = [];
let hubMarkers = [];
let featureMarkers = [];
let toiletMarkers = [];
let drinkingWaterMarkers = [];
// event_mode.pois, always-on, no rider toggle. Held in its own
// array so the proximity / toggle filter passes don't touch it.
let eventPoiMarkers = [];
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
    "bbox", "panBbox",
    "minZoom", "maxZoom",
    "routes",
];

function validateConfigShape() {
    if (typeof CONFIG !== "object" || CONFIG === null) {
        throw new Error("CONFIG is missing or not an object, check that the build's template substitution succeeded.");
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

// Snapshot of the framework's canonical view target, the same
// fitBounds(CONFIG.bbox + padding 50) the rider would see arriving
// at the map with a clean URL. The Reset View FAB always replays
// this regardless of how the rider arrived, so the control's
// behavior is predictable: tapping Reset means "show me the whole
// trail system" every time. Riders who want their original
// share-link view can use the browser's Back button.
//
// Was previously context-aware (share-link arrivals reset to the
// deep-linked center/zoom instead of the bbox), but that made the
// same control behave differently depending on how the rider
// arrived, surprising on a permanent UI affordance, and the
// fit-to-page icon semantics imply "the map's canonical view," not
// "your entry view."
let _initialViewTarget = null;

// Apply a highlight that was parsed from an incoming share link.
// Called once, after trails + indexes are loaded. The view portion
// (zoom / center) of the share link is already in effect via map
// construction options. Best-effort: silently no-ops if the
// referenced route, trail, or POI no longer exists in the data (so a
// stale link doesn't render an error).
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
    } else if (h.kind === "poi") {
        // h.key is a Finder row descriptor (poi:/group:/category:).
        // highlightPoiByRef resolves it against the live POI index and
        // re-creates the single / group / category highlight, or no-ops
        // if nothing matches.
        highlightPoiByRef(h.key);
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
    } else if (_poiHighlightRef) {
        // POI highlight (single, name-group, or category). The ref is the
        // Finder row's uid; mutually exclusive with `highlight` by the
        // single-highlight invariant. consumeShareHash + highlightPoiByRef
        // re-expand it against live data on the receiving side.
        path += `/p/${encodeURIComponent(_poiHighlightRef)}`;
    }
    const url = new URL(window.location.href);
    url.hash = path;
    return url.toString();
}

// Build the human-readable share-sheet title, appears as email
// subject in Mail-style apps and as the share-sheet header in iOS
// /Android UIs. Most messaging apps actually rely on OG tags fetched
// from the URL itself for their preview cards, so this is mostly a
// helpful default for email.
function buildShareTitle() {
    const baseTitle = CONFIG.title || CONFIG.name || "Trail Map";
    if (highlight && highlight.kind === "route" && highlight.key
            && CONFIG.routes && CONFIG.routes[highlight.key]) {
        return `${baseTitle}: ${CONFIG.routes[highlight.key].name}`;
    }
    if (highlight && highlight.kind === "trail" && highlight.key) {
        return `${baseTitle}: ${highlight.key}`;
    }
    if (_poiHighlightRef && _highlightedPois.length) {
        const n = _highlightedPois.length;
        if (n === 1) return `${baseTitle}: ${_highlightedPois[0].name}`;
        const parsed = _parsePoiRef(_poiHighlightRef);
        const groupName = parsed
            ? (parsed.mode === "category"
                ? (POI_TYPE_FALLBACK_NAME[parsed.type] || parsed.type)
                : parsed.name)
            : "";
        if (groupName) return `${baseTitle}: ${groupName} (× ${n})`;
    }
    return baseTitle;
}

// Share-button click handler. Tries Web Share API first (native
// share sheet on mobile, gives the user real choice, Messages /
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
            // failure, don't fall through to clipboard (would feel
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
            .then(() => showToast("View URL copied. Paste anywhere to share."))
            .catch(() => showToast("Couldn't copy URL. Try again or use the URL bar."));
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
            showToast("View URL copied. Paste anywhere to share.");
        } catch (e) {
            showToast("Couldn't copy URL. Try again or use the URL bar.");
        }
    }
}

// Reveal + wire up the Share button. Called from setupFloatingChrome
// during boot. Does nothing when share_button: false at build time
// (the whole button is stripped from index.html before render, so
// getElementById returns null and we early-return).
function setupShareButton() {
    const btn = document.getElementById("share-btn");
    if (!btn) return;
    btn.classList.remove("hidden");
    btn.addEventListener("click", shareCurrentView);
}

// Parse a "#share=zoom/lat/lon[/r/<routeId>|/t/<trailName>|/p/<poiRef>]"
// hash and return {center: [lon, lat], zoom, highlight: {kind, key} | null} or
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
        else if (kindCode === "p") highlight = { kind: "poi", key };
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
            // Bypass the browser's HTTP cache, we want the origin's
            // actual response. (The service worker may still intercept
            // and return 206 from its own cache once it's active; that
            // produces a "false negative" on subsequent visits, which
            // is the correct behavior, once the file is cached
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
                "After the service worker caches the file, this stops mattering, " +
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
    // Publish the shared scrim density to CSS so the overlay backdrops
    // (--overlay-scrim) match the in-map highlight wash (dim-tint) exactly
    // one continuous wash as the rider moves between a highlight and an
    // open menu. Both read from the same SCRIM_OPACITY / scrim_opacity.
    document.documentElement.style.setProperty(
        "--scrim-opacity", String(SCRIM_OPACITY));
    try {
        validateConfigShape();
    } catch (e) {
        // Surface the error visibly on the map div so it's not buried
        // in DevTools, operators deploying a broken build will see
        // this immediately.
        console.error(e);
        const container = document.getElementById("map");
        if (container) {
            // Build the failure card via DOM nodes rather than
            // template-literal innerHTML so the error message, which
            // can come from anywhere upstream and may eventually
            // include curator-controlled or OSM-derived strings,
            // can't smuggle markup into the page. textContent
            // neutralizes any HTML in e.message.
            container.replaceChildren();
            const wrap = document.createElement("div");
            wrap.style.cssText = "padding:24px;font-family:system-ui;color:#b00";
            const strong = document.createElement("strong");
            strong.textContent = "Map failed to start.";
            wrap.appendChild(strong);
            wrap.appendChild(document.createElement("br"));
            const code = document.createElement("code");
            code.style.whiteSpace = "pre-wrap";
            code.textContent = e.message;
            wrap.appendChild(code);
            container.appendChild(wrap);
        }
        // Tear down the initial-load bar, a config-broken build renders
        // the error card above instead of a map, and a bar spinning over
        // it reads as "still working" when it's actually dead.
        if (window.__hideMapLoading) window.__hideMapLoading();
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
    // visit, which is exactly when it matters. Silent on success;
    // logs a console.error with diagnostic detail on failure. No toast,
    // no UI noise, this is curator-with-DevTools-open territory.
    checkPMTilesRangeSupport();

    // Build map style (light theme only)
    const style = buildStyle();

    // Build transformRequest for base layers that require auth headers
    const headersByDomain = buildHeaderMap();

    // Detect a "#share=..." URL (from the Share button on another
    // session) BEFORE map construction. If present, we use its center
    // /zoom as the initial view and stash any highlight for application
    // after trails load. The share hash is also stripped from the URL
    // here, it's a one-shot view restoration, not ambient state.
    const shareState = consumeShareHash();
    if (shareState && shareState.highlight) {
        _pendingShareHighlight = shareState.highlight;
    }

    // Create map
    //
    // Default: `bounds` uses the tight CONFIG.bbox so the initial
    // view frames the trails snugly. `maxBounds` uses CONFIG.panBbox
    // (built from bbox + pan_padding) so the user has room to pan
    // for context, maxBounds clamps the map CENTER, not the
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
        // URL hash (#zoom/lat/lon), makes views shareable and reload-
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
    // Reset View target is always the framework canonical view
    // (fitBounds CONFIG.bbox + padding 50), regardless of whether
    // the rider arrived via share link. See _initialViewTarget
    // declaration for rationale.
    _initialViewTarget = {
        kind: "bounds",
        bbox: CONFIG.bbox.slice(),  // defensive copy
        padding: 50,
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
    // NavigationControl (zoom +/-) is intentionally not added, pinch,
    // scroll-wheel, and double-tap cover zoom on every platform the
    // app targets, and dropping the button reclaims the top-left
    // corner for a cleaner map. GeolocateControl IS added (hidden via
    // CSS) because we still need its event/state machine; the
    // Locate FAB drives it via geolocate.trigger().
    //
    // ============================================================
    // GeolocateControl + off-screen indicator state machine
    // ============================================================
    // MapLibre's GeolocateControl owns the actual GPS tracking state.
    // We don't replace it, we hide its DOM control via CSS, drive
    // it through .trigger(), and modify its camera behavior.
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
    // Stock MapLibre camera behavior at each transition:
    //   IDLE → ACTIVE         easeTo(user, fitBoundsOptions), pan + zoom
    //   ACTIVE per-fix        easeTo(user), just pan
    //   ACTIVE → BACKGROUND   no camera move (user is in control)
    //   BACKGROUND fix update no camera move (dot moves, not the map)
    //   BACKGROUND → ACTIVE   easeTo(user), re-center, no zoom
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
    //     flags BEFORE calling trigger(), single decision point
    //   - Off-screen indicator click handler: own flyTo path; sets
    //     flags before triggering re-engage
    //   - `movestart` filter: consumes flags to allow / cancel the
    //     geolocate-source camera moves
    //   - mirrorLocateState: clears userLocation when state goes
    //     idle / disabled (handles the "tracking actually ended"
    //     transition without depending on which event fires)
    //
    // We deliberately do NOT use `trackuserlocationstart` to arm
    // flags, MapLibre 5.x fires it for IDLE → WAITING but NOT for
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
    //     just enabled tracking; might not realize their position
    //     is off the trail bbox). Set false on background→active
    //     (user knows what they want, to be re-centered).
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
            // when the OS / browser can't acquire a fix, most often
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
        // the indicator itself, the toast would be circular advice.
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
            showToast("Your location is off-screen. Tap the blue ▲ at the map edge to center on it.");
        }
    };

    // movestart filter for geolocate-sourced camera moves. Three branches:
    //
    //  1. Move is user-initiated (no geolocateSource): they want to look
    //     around, exit follow-me mode. Don't cancel, the user IS the
    //     camera now.
    //  2. Move is the FIRST geolocate-source move after entering active
    //     tracking (the fitBounds-zoom yank): always cancel. Defer to a
    //     microtask, which drains after easeTo finishes setup but before
    //     any rAF fires, canceling the animation before a frame draws.
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
        // panned away during tracking, they're STILL being tracked,
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

    // Wire the Locate FAB to the GeolocateControl and mirror its FULL
    // state machine (idle / waiting / active / background /
    // active-error / background-error / disabled) so the floating
    // button renders identically to MapLibre's native control,
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
        // idle/waiting/disabled). The base aria-pressed=false off-state
        // styling is overridden in CSS for #toggle-locate so idle
        // doesn't render the slash, our state classes control
        // appearance.
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
                //
                // Keep follow-me OFF here so NO geolocate-source ease
                // moves the camera on initial enable, not the first
                // fitBounds-zoom (canceled below) and not the next
                // per-fix easeTo either. Otherwise the second GPS fix
                // would auto-center a few seconds after the indicator
                // + toast appeared, contradicting the "we're not
                // moving you, tap the ▲ to center" promise. Follow-me
                // is opted into explicitly via the indicator-click and
                // BACKGROUND → ACTIVE re-engage paths.
                _firstGeolocateMoveAfterTrigger = true;
                _showToastOnNextFix = true;
                _followUserOnGeolocate = false;
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
            // happen in practice, map.addControl above adds it).
            locateBtn.classList.add("locate-state-idle");
        }
    }

    // NOTE: no auto-locate on load. Even with permission already
    // granted, automatically calling geolocate.trigger() would put
    // the GPS into high-accuracy continuous mode (we set
    // enableHighAccuracy: true above), which is a meaningful battery
    // drain on a multi-hour ride. Tracking is strictly user-initiated
    // they tap the Locate button when they want it.

    // Refresh the off-screen indicator on every visible-area change.
    // `move` covers pan + zoom + pitch + rotate in MapLibre, but we
    // also wire `zoom` + `resize` defensively, pinch-zoom centered
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

    // Attribution: compact (i) form, but EXPANDED at page load so the
    // first impression of the map shows OSM / Protomaps / Mapterhorn
    // credits prominently. Auto-collapses on the rider's first
    // user-driven map gesture (mouse-down for pan, wheel for zoom,
    // touch for pan/pinch), at that point they've already seen the
    // attribution and want their screen real estate back. The (i)
    // icon stays visible after collapse; one tap re-expands.
    //
    // Why user-input listeners (mousedown/touchstart/wheel) instead
    // of MapLibre's `movestart` event: programmatic camera changes
    // at load time (a share-link flyTo, fitBounds on a deep-linked
    // highlight, etc.) also fire `movestart` and would dismiss the
    // attribution before the rider has even seen it. Raw input
    // events only fire on actual user gestures.
    const attrControl = new maplibregl.AttributionControl({ compact: true });
    map.addControl(attrControl, "bottom-left");
    attrControl._container.classList.add("maplibregl-compact-show");

    const collapseAttribution = () => {
        attrControl._container.classList.remove("maplibregl-compact-show");
        const canvasContainer = map.getCanvasContainer();
        canvasContainer.removeEventListener("mousedown", collapseAttribution);
        canvasContainer.removeEventListener("touchstart", collapseAttribution);
        canvasContainer.removeEventListener("wheel", collapseAttribution);
    };
    const canvasContainer = map.getCanvasContainer();
    canvasContainer.addEventListener("mousedown", collapseAttribution, { passive: true });
    canvasContainer.addEventListener("touchstart", collapseAttribution, { passive: true });
    canvasContainer.addEventListener("wheel", collapseAttribution, { passive: true });

    // Load data and add layers once map is ready.
    map.once("style.load", async () => {
        await loadTrails();
        await loadPOIs();
        buildRouteIndex();
        buildTrailIndex();
        buildPoiIndex();
        setupFloatingChrome();
        // Routes panel docked-state wiring + boot form. After
        // setupFloatingChrome: its initial applyVisibilityChange()
        // populated visibleRoutes (which decides the panel's default
        // docked state) and already ran the first
        // rebuildRoutePanel() row build. Before setupFabLabels: the
        // panel's discovery label only mounts when the panel booted
        // collapsed, so the collapsed class must be settled first.
        initRoutePanel();
        // First-visit-per-map FAB-label discoverability cue. Mounts
        // labels under each FAB, dismisses on tap or 5 s timeout.
        // Self-suppresses on subsequent visits via LS flag. Must
        // run after setupFloatingChrome so the FABs' main click
        // handlers are wired (our dismiss listener piggybacks).
        setupFabLabels();
        setupInteractions();
        promoteBasemapLabels();
        suppressBasemapPathLabels();
        suppressBasemapPois();
        suppressBasemapOnewayArrows();
        // Apply share-link highlight, if any. Done here (after both
        // trails and route/trail indexes are built) so we can resolve
        // route IDs / trail names against real data. Best-effort,
        // silently skip if the referenced route or trail no longer
        // exists (e.g. the map has been rebuilt since the link was
        // shared and the relation changed).
        if (_pendingShareHighlight) {
            applyPendingShareHighlight();
        }

        // Hide the initial-load progress bar once the opening viewport is
        // painted. When this map has terrain we can't use the first global
        // map 'idle', the terrain raster-dem pmtiles source loads its
        // header asynchronously, and an 'idle' can fire after basemap +
        // trails settle but before the hillshade tiles paint, hiding the
        // bar too early (most visible on a hard reload). Instead we wait on
        // _terrainTilesLoaded (armed in addTerrainLayers), which resolves
        // at an idle, once terrain has SETTLED for the view: tiles
        // loaded, or none needed (config gate / out of coverage). It
        // resolves AT an idle, so we hide directly here; registering a
        // fresh once('idle') could wait for a busy→idle transition that
        // never comes. __hideMapLoading is idempotent; the 30s inline
        // safety net covers a stalled terrain header (rare, HEAD passed).
        const _hideBar = () => {
            if (window.__hideMapLoading) window.__hideMapLoading();
        };
        if (CONFIG.showTerrain && map.getSource("terrain") && _terrainTilesLoaded) {
            _terrainTilesLoaded.then(_hideBar);
        } else {
            map.once("idle", _hideBar);
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
// FAB stack / routes-vs-trails distinction / where the About button
// lives. Shown ONCE per map per browser; dismissal stored in
// localStorage under `mtb.welcomed`. The LS helper already
// per-map-prefixes every key with `<slug>.`, so this becomes
// `<slug>.mtb.welcomed` on disk, no need to put the slug in the
// key body. Subsequent visits skip it entirely (no flicker).
//
// The body copy is built from CONFIG so it reflects the actual
// features available on the current map (routes vs trails sections,
// share button, install affordance) rather than describing things
// that aren't there.
function initWelcomeModal() {
    const modal = document.getElementById("welcome-modal");
    if (!modal) return;
    // Welcome can be suppressed entirely per-map by setting
    // `welcome: false` in the YAML, useful for embeds or maps
    // where the curator doesn't want a first-visit overlay.
    if (CONFIG.welcome === false) return;
    const flagKey = "mtb.welcomed";
    if (LS.get(flagKey) === true) return;

    const welcome = (CONFIG.welcome && typeof CONFIG.welcome === "object")
        ? CONFIG.welcome : {};
    const showControlsHint = welcome.show_controls_hint !== false;

    const titleEl = document.getElementById("welcome-modal-title");
    const bodyEl = document.getElementById("welcome-modal-body");
    const closeBtn = document.getElementById("welcome-modal-close");
    const cta = document.getElementById("welcome-modal-cta");

    if (titleEl) {
        titleEl.textContent = welcome.title
            || `Welcome to ${CONFIG.title || CONFIG.name || "this trail map"}`;
    }

    // Body assembly. Three optional sections, each rendered only
    // when there's something to say. Curator content first (so the
    // map-specific intro reads as the headline), then the controls
    // hint (skippable via show_controls_hint: false if the curator
    // explained the controls in body), then the sober attribution
    // footer (always, both legal cover and a thoughtful
    // acknowledgment). All curator-supplied strings rendered as
    // textContent to keep XSS surface minimal.
    if (bodyEl) {
        bodyEl.innerHTML = "";

        if (welcome.body) {
            // Split body into paragraphs on blank lines so YAML's
            // `|` block scalar reads naturally without HTML.
            const paragraphs = welcome.body.split(/\n\s*\n/);
            for (const para of paragraphs) {
                const text = para.trim();
                if (!text) continue;
                const p = document.createElement("p");
                p.className = "welcome-modal-body-p";
                p.textContent = text;
                bodyEl.appendChild(p);
            }
        }

        if (showControlsHint) {
            bodyEl.appendChild(buildWelcomeControlsHint());
        }
    }

    function dismissWelcome() {
        LS.set(flagKey, true);
        modal.classList.add("hidden");
        syncModalOpenClass();
    }

    if (closeBtn) closeBtn.addEventListener("click", dismissWelcome);
    if (cta) cta.addEventListener("click", dismissWelcome);

    // Escape key dismisses too, matches About modal behavior so the
    // keyboard pattern is consistent.
    function onKeydown(e) {
        if (e.key === "Escape" && !modal.classList.contains("hidden")) {
            dismissWelcome();
        }
    }
    document.addEventListener("keydown", onKeydown);

    // Show the modal on the next frame so the floating chrome has
    // settled into place first (otherwise the modal can flash before
    // the brand + FAB stack render on slow first paints).
    requestAnimationFrame(() => {
        modal.classList.remove("hidden");
        syncModalOpenClass();
    });
}

// MDI SVG paths for the welcome controls hint. Same paths as the
// FAB buttons, keeps the rider's mental model consistent ("the
// icon I see in the welcome is the icon I'll see on the map").
// Inlined here rather than queried from the DOM so the welcome
// renders correctly even if the FABs haven't mounted yet.
// mdi:crosshairs-gps (Apache 2.0, Pictogrammers), matches the
// Locate FAB glyph (templates/index.html).
const _WELCOME_ICON_LOCATE     = "M12,8A4,4 0 0,1 16,12A4,4 0 0,1 12,16A4,4 0 0,1 8,12A4,4 0 0,1 12,8M3.05,13H1V11H3.05C3.5,6.83 6.83,3.5 11,3.05V1H13V3.05C17.17,3.5 20.5,6.83 20.95,11H23V13H20.95C20.5,17.17 17.17,20.5 13,20.95V23H11V20.95C6.83,20.5 3.5,17.17 3.05,13M12,5A7,7 0 0,0 5,12A7,7 0 0,0 12,19A7,7 0 0,0 19,12A7,7 0 0,0 12,5Z";
// mdi:image-filter-center-focus (Apache 2.0, Pictogrammers),
// matches the Reset View FAB glyph (templates/index.html). Four
// corner brackets + center dot read as "frame this content".
const _WELCOME_ICON_RESET_VIEW = "M5,15H3V19A2,2 0 0,0 5,21H9V19H5M5,5H9V3H5A2,2 0 0,0 3,5V9H5M19,3H15V5H19V9H21V5A2,2 0 0,0 19,3M19,19H15V21H19A2,2 0 0,0 21,19V15H19M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9Z";
// mdi:cog (Apache 2.0, Pictogrammers), matches the Options FAB
// glyph (templates/index.html). Reverted from a brief mdi:tune
// experiment that read as "audio equalizer" rather than "settings".
const _WELCOME_ICON_OPTIONS    = "M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.21,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.21,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.67 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z";
// mdi:format-list-bulleted (Apache 2.0, Pictogrammers), matches the
// routes-panel chip glyph (templates/index.html): dot-per-row list
// reads as "route rows".
const _WELCOME_ICON_ROUTES     = "M7,5H21V7H7V5M7,13V11H21V13H7M4,4.5A1.5,1.5 0 0,1 5.5,6A1.5,1.5 0 0,1 4,7.5A1.5,1.5 0 0,1 2.5,6A1.5,1.5 0 0,1 4,4.5M4,10.5A1.5,1.5 0 0,1 5.5,12A1.5,1.5 0 0,1 4,13.5A1.5,1.5 0 0,1 2.5,12A1.5,1.5 0 0,1 4,10.5M7,19V17H21V19H7M4,16.5A1.5,1.5 0 0,1 5.5,18A1.5,1.5 0 0,1 4,19.5A1.5,1.5 0 0,1 2.5,18A1.5,1.5 0 0,1 4,16.5Z";
// mdi:magnify (Apache 2.0, Pictogrammers), matches the routes
// panel's Search-row glyph (templates/index.html).
const _WELCOME_ICON_SEARCH     = "M9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.44,13.73L14.71,14H15.5L20.5,19L19,20.5L14,15.5V14.71L13.73,14.44C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 3,9.5A6.5,6.5 0 0,1 9.5,3M9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5Z";
// mdi:download (Apache 2.0, Pictogrammers), matches the GPX FAB
// glyph (templates/index.html). Event maps with event_mode.gpx only.
const _WELCOME_ICON_GPX        = "M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z";

// Count-aware GPX wording, shared by every GPX surface: the FAB
// discovery label + aria-label, the download-sheet title, and the
// welcome-modal controls row. Singular/plural follows the configured
// file count so a one-file event map never promises "files", and no
// two surfaces can disagree because they all read from here.
function _gpxWording() {
    const plural = (CONFIG.gpxDownloads || []).length > 1;
    const noun = plural ? "GPX files" : "GPX file";
    return { label: noun, action: "Download " + noun, plural };
}


function _welcomeIconSvg(pathD) {
    // Returns the SVG markup for one welcome-icon glyph. 18×18
    // matches comfortably with the body text size, currentColor so
    // it picks up the row's text color.
    return `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="${pathD}"/></svg>`;
}

// Build the controls-hint section: one row per corner-anchored
// control, each with its icon + name + a one-line description. Helps
// a first-visit rider learn the chrome (Locate + Reset View + Options
// FABs top-right, the routes panel with its search entry
// bottom-right) without leaving the welcome modal.
// Join a list of phrases with comma + Oxford "and", "x", "x and y",
// "x, y, and z". Used by the dynamic welcome descriptions to read
// naturally regardless of how many items survive the per-map filter.
function _joinHumanList(items) {
    if (!items.length) return "";
    if (items.length === 1) return items[0];
    if (items.length === 2) return `${items[0]} and ${items[1]}`;
    return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

// Build the Search row description from what's actually present on
// this map. Trails are gated by CONFIG.showTrails; every map has
// routes (a geometry source is required). POI types come from
// CONFIG.poiCounts (populated at build time from pois.geojson).
// Avoids the previous claim that every map has "(parking, water,
// toilets, trailheads)" regardless of reality.
function _welcomeSearchDescription() {
    const targets = ["routes"];
    if (CONFIG.showTrails !== false) targets.push("trails");

    // Specific POI types, order roughly follows the rhythm of a
    // ride: where you start (parking, trailheads), what you may
    // encounter on-trail (features), amenities along the way
    // (water, toilets). Only include types that have at least one
    // feature in the build's pois.geojson (or a curator-supplied
    // YAML entry for parking/trailheads, both folded together at
    // build time).
    const counts = CONFIG.poiCounts || {};
    const poiNames = [];
    if (counts.parking)        poiNames.push("parking");
    if (counts.trailhead)      poiNames.push("trailheads");
    if (counts.hub)            poiNames.push("hubs");
    if (counts.feature)        poiNames.push("features");
    if (counts.drinking_water) poiNames.push("water");
    if (counts.toilet)         poiNames.push("toilets");
    // Trail markers are intentionally NOT mentioned, they're
    // numbered guideposts / emergency-access points scattered
    // through the trail network, not search targets in the way
    // amenities and features are.

    if (poiNames.length) {
        targets.push(`places (${poiNames.join(", ")})`);
    }

    if (!targets.length) return "Find anything on the map.";
    return `Find ${_joinHumanList(targets)}.`;
}

// Build the list of categories the search box actually returns,
// based on the same gates renderResults() uses (CONFIG.showTrails,
// CONFIG.poiCounts; routes are always present). Returns an array of
// human labels (e.g. ["routes", "trails", "places"]) so callers can
// compose either the comma-form used by the placeholder or the
// human-list form used by aria-labels.
function _searchTargets() {
    const targets = ["routes"];
    if (CONFIG.showTrails !== false) targets.push("trails");
    const counts = CONFIG.poiCounts || {};
    const hasPois = !!(counts.parking || counts.trailhead || counts.hub
                       || counts.feature || counts.drinking_water
                       || counts.toilet);
    if (hasPois) targets.push("places");
    return targets;
}

// Build the Options row description from what affordances are
// actually wired into this map. The lead clause ("turn map markers
// and overlays on or off") covers the most rider-relevant action,
// the layer toggles, in concrete language; appearance follows as
// the other always-present display control; share / install /
// About each appear only when their corresponding feature is
// enabled. "Season" is omitted from the default copy because
// detecting "this map has both summer + winter routes" requires
// extra build-time plumbing, added separately if needed.
function _welcomeOptionsDescription() {
    const items = [
        "turn map markers and overlays on or off",
        // Appearance segmented control (Light / Dark / Auto) is
        // always present in the Options overlay, so this entry is
        // unconditional. Mention only "light and dark" rather than
        // enumerate Auto, the welcome is a quick orientation, not
        // a feature spec.
        "switch between light and dark mode",
    ];
    if (CONFIG.shareButton) items.push("share the view");
    if (CONFIG.pwa && CONFIG.pwaInstallPrompt) items.push("install as an app");
    items.push("see info about this map");
    // Capitalize the first letter of the joined imperative list so
    // it reads as a sentence; final period anchors it.
    const joined = _joinHumanList(items);
    return joined.charAt(0).toUpperCase() + joined.slice(1) + ".";
}

function buildWelcomeControlsHint() {
    const wrap = document.createElement("div");
    wrap.className = "welcome-modal-controls";

    const heading = document.createElement("h3");
    heading.className = "welcome-modal-section-heading";
    heading.textContent = "How to use this map";
    wrap.appendChild(heading);

    const list = document.createElement("ul");
    list.className = "welcome-modal-controls-list";

    // Order matches the on-screen chrome reading top-to-bottom on
    // the right edge: the FAB stack (Locate → Reset View → Options)
    // followed by the routes panel (Routes key + its Search row) at
    // bottom-right. Same sequence the rider sees on the map keeps
    // the mental mapping cheap.
    //
    // Routes gets its own row (chip's list icon) rather than a
    // sentence tacked onto Search: the panel is the map key, the
    // first thing a rider orients by, and it deserves the same
    // billing as the FABs. Every map has a key (a geometry source is
    // required, so there's always ≥1 route), so the row is
    // unconditional. Search keeps its own row for the verb a
    // first-visit rider needs taught.
    const rows = [
        { icon: _WELCOME_ICON_LOCATE,     name: "Locate",
            desc: "Track your position on the map." },
        { icon: _WELCOME_ICON_RESET_VIEW, name: "Reset view",
            desc: "Reset the map to its starting view." },
        { icon: _WELCOME_ICON_OPTIONS,    name: "Options",
            desc: _welcomeOptionsDescription() },
        { icon: _WELCOME_ICON_ROUTES,     name: "Routes",
            desc: "Each route's color and name."
                + " Tap a route to highlight it on the map"
                + " or collapse the panel." },
        { icon: _WELCOME_ICON_SEARCH,     name: "Search",
            desc: _welcomeSearchDescription() },
    ];

    // GPX download FAB, event maps with event_mode.gpx only.
    // Inserted after Options to match the on-screen order (it sits
    // at the bottom of the top-right stack, above the bottom-right
    // panel's Routes + Search rows).
    if ((CONFIG.gpxDownloads || []).length) {
        const w = _gpxWording();
        rows.splice(3, 0, { icon: _WELCOME_ICON_GPX, name: w.label,
            desc: w.plural
                ? "Download route GPX files for your bike computer."
                : "Download the route's GPX file for your bike computer." });
    }

    for (const r of rows) {
        const li = document.createElement("li");
        li.className = "welcome-modal-control-row";

        const iconSpan = document.createElement("span");
        iconSpan.className = "welcome-modal-control-icon";
        iconSpan.innerHTML = _welcomeIconSvg(r.icon);
        li.appendChild(iconSpan);

        const textSpan = document.createElement("span");
        textSpan.className = "welcome-modal-control-text";
        const nameStrong = document.createElement("strong");
        nameStrong.textContent = r.name;
        textSpan.appendChild(nameStrong);
        textSpan.appendChild(document.createTextNode(": " + r.desc));
        li.appendChild(textSpan);

        list.appendChild(li);
    }

    wrap.appendChild(list);
    return wrap;
}

// ============================================================
// About This Map modal
// ============================================================
function initAbout() {
    const btn = document.getElementById("about-btn");
    const modal = document.getElementById("about-modal");
    const closeBtn = modal.querySelector(".about-modal-close");

    btn.addEventListener("click", openAboutModal);
    // Close via the X, Escape, or backdrop click, matches the
    // dismissal pattern of the Search and Options overlays so any
    // window over the map closes the same way.
    closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        closeAboutModal();
    });
    // Backdrop dismissal: click on the backdrop layer (outside the
    // .about-modal-content card) closes. The `e.target === backdrop`
    // discriminator means clicks inside the card don't bubble out
    // and dismiss accidentally.
    const backdrop = modal.querySelector(".about-modal-backdrop");
    if (backdrop) {
        backdrop.addEventListener("click", (e) => {
            if (e.target === backdrop) closeAboutModal();
        });
    }

    buildAboutModalContent();
}

// Mirror "an About/Welcome modal is on screen" onto <body class="modal-
// open"> so the single-scrim CSS can suppress the Search/Options overlay
// scrim underneath it. Both modals share the .about-modal styling and
// open over an already-open Options overlay; without this their backdrop
// (z-index 100) stacks on the overlay scrim (z-index 7) into a darker-
// than-intended double dim. Computed from the live DOM so About and
// Welcome can't get out of sync.
function syncModalOpenClass() {
    document.body.classList.toggle("modal-open", anyModalOpen());
    // The in-map wash yields to a modal (see refreshSpotlightDim), so its
    // opacity + .has-spotlight depend on modal state, refresh them here.
    refreshSpotlightDim();
}

function openAboutModal() {
    document.getElementById("about-modal").classList.remove("hidden");
    syncModalOpenClass();
}

function closeAboutModal() {
    document.getElementById("about-modal").classList.add("hidden");
    syncModalOpenClass();
}

// Build an external link for the About modal. Validates the URL
// scheme: only http://, https://, and mailto: pass through; anything
// else (javascript:, data:, vbscript:, file:, about:, etc.) is
// neutralized by setting href="#" and logging a warning. Defends
// against a curator (or curator-supplied YAML pasted from external
// sources) introducing a script-execution vector via about.curator.url
// or about.links[].url. The label still renders so the rider sees the
// item; only the dangerous href is blocked.
const _SAFE_URL_SCHEMES = ["http:", "https:", "mailto:"];
function _isSafeExternalUrl(url) {
    if (typeof url !== "string" || !url) return false;
    try {
        // Use URL parsing to handle whitespace, mixed case, and
        // schemeless URLs (treated as relative, also unsafe in the
        // External-link context). location.origin is the base for
        // resolving schemeless inputs.
        const u = new URL(url, window.location.origin);
        return _SAFE_URL_SCHEMES.includes(u.protocol);
    } catch (_) {
        return false;
    }
}
function aboutExtLink(url, label) {
    const a = document.createElement("a");
    if (_isSafeExternalUrl(url)) {
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
    } else {
        a.href = "#";
        // Surface the rejection so a curator notices their typo or
        // misuse. Console-only, the visible link still renders so
        // the rider isn't confronted with a broken UI.
        console.warn(
            `aboutExtLink: rejected unsafe URL scheme, `
            + `expected http(s):// or mailto:, got ${JSON.stringify(url)}`);
    }
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

    // Map Curator \u2014 h3 section with the curator's name on the next
    // line (optionally linked). "Curator" rather than "Author"
    // because the framework just generates the map; the human
    // chooses the data, configures the styling, and decides what's
    // shown \u2014 that's curation, not authorship of the underlying
    // data. Section is omitted entirely when no curator is given.
    if (about.curator && (about.curator.name || about.curator.url)) {
        const h = document.createElement("h3");
        h.textContent = "Map Curator";
        body.appendChild(h);
        const p = document.createElement("p");
        if (about.curator.url && about.curator.name) {
            p.appendChild(aboutExtLink(about.curator.url, about.curator.name));
        } else if (about.curator.url) {
            p.appendChild(aboutExtLink(about.curator.url, about.curator.url));
        } else {
            p.textContent = about.curator.name;
        }
        body.appendChild(p);
    }

    // More info \u2014 single section combining what was previously two
    // (more_information + extra_links). Curator-supplied bulleted
    // list of related links: official trail-system pages, club
    // pages, related orgs, etc. Order matches the YAML.
    if (Array.isArray(about.links) && about.links.length) {
        const h = document.createElement("h3");
        h.textContent = "More info";
        body.appendChild(h);
        const ul = document.createElement("ul");
        about.links.forEach((link) => {
            if (!link || !link.url) return;
            const li = document.createElement("li");
            li.appendChild(aboutExtLink(link.url, link.label || link.url));
            ul.appendChild(li);
        });
        body.appendChild(ul);
    }

    // Built with \u2014 combined section for framework-generated meta
    // (versions + credits, previously two separate h3 sections).
    // Always rendered. Order: framework name credit (so the rider
    // knows what generated this map), then versions (recency cue),
    // then per-source credits (data + libraries). Terrain credit is
    // conditional on show_terrain \u2014 no point crediting a source
    // whose tiles aren't loaded for this map.
    const builtWithHeader = document.createElement("h3");
    builtWithHeader.textContent = "Built with";
    tail.appendChild(builtWithHeader);

    const credit = (prefix, url, label, suffix) => {
        const p = document.createElement("p");
        p.className = "about-modal-credit";
        if (prefix) p.appendChild(document.createTextNode(prefix));
        p.appendChild(aboutExtLink(url, label));
        if (suffix) p.appendChild(document.createTextNode(suffix));
        tail.appendChild(p);
    };

    // Framework credit \u2014 the entire phrase "trailmaps.app Map
    // Generator" is the link text (replaces the older "Generated by
    // <a>trailmaps.app</a> Map Generator." phrasing). The two version
    // lines that follow indent visually under this credit via the
    // .about-modal-version CSS, so the relationship reads as
    // metadata about the framework generation, not as standalone items.
    credit("",
        "https://github.com/c0nsumer/trailmaps.app-map-generator",
        "trailmaps.app Map Generator",
        "");

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

    credit("Map data \u00a9 ",
        "https://www.openstreetmap.org/copyright",
        "OpenStreetMap contributors",
        " (ODbL).");
    credit("Basemap tiles \u00a9 ",
        "https://protomaps.com",
        "Protomaps",
        " (BSD).");
    if (CONFIG.showTerrain) {
        credit("Terrain tiles \u00a9 ",
            "https://mapterhorn.com",
            "Mapterhorn",
            " \u2014 aggregates public-domain elevation sources (USGS 3DEP, EU-DEM, JAXA AW3D30, etc.).");
    }
    // USGS 3DEP credit is shown only when the map actually displays
    // route elevation. Detected at runtime by looking for any route
    // metadata entry with computed elevation_gain_m \u2014 present iff
    // show_route_elevation was true at build time AND the 3DEP fetch
    // succeeded. Avoids crediting a data source whose output isn't
    // actually surfaced to the rider.
    const hasRouteElevation = Object.values(CONFIG.routes || {}).some(
        (r) => typeof r.elevation_gain_m === "number");
    if (hasRouteElevation) {
        credit("Route elevation profiles from ",
            "https://www.usgs.gov/3d-elevation-program",
            "USGS 3DEP",
            " (US government, public domain).");
    }
    credit("UI iconography from ",
        "https://pictogrammers.com/library/mdi/",
        "Material Design Icons",
        " by Pictogrammers (Apache 2.0).");
    credit("Map rendering by ",
        "https://maplibre.org",
        "MapLibre GL JS",
        " (BSD-3-Clause).");
    credit("Map labels rendered with fonts under the ",
        "https://openfontlicense.org/",
        "SIL Open Font License",
        ".");
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

    // Protomaps flavor follows current color scheme: "light" or
    // "dark". The bootstrap script in <head> sets data-color-scheme
    // before the runtime initializes, so first-paint tile selection
    // is already correct, no light→dark flicker on load. Scheme
    // toggles at runtime trigger map.setStyle(buildStyle()) which
    // re-runs this and gets the new flavor.
    const flavor = currentColorScheme() === "dark" ? "dark" : "light";
    const basemapLayers = basemaps.layers("basemap", basemaps.namedFlavor(flavor), { lang: "en" });

    // Attribution: each source gets its own © assertion so it reads
    // unambiguously about who owns what. Terrain credit (Mapterhorn)
    // only appears when terrain is actually loaded, no point
    // crediting a source whose data isn't on the map.
    //
    // target=_blank + rel=noopener: tapping a credit shouldn't yank
    // the rider out of the map (loses zoom/pan/highlight/geolocate
    // state, and is genuinely disorienting in standalone PWA mode
    // where there's no browser back button visible).
    const ATTR_LINK_ATTRS = 'target="_blank" rel="noopener"';
    const attrParts = [
        `&copy; <a href="https://www.openstreetmap.org/copyright" ${ATTR_LINK_ATTRS}>OpenStreetMap</a>`,
        `&copy; <a href="https://protomaps.com" ${ATTR_LINK_ATTRS}>Protomaps</a>`,
    ];
    if (CONFIG.showTerrain) {
        attrParts.push(`&copy; <a href="https://mapterhorn.com" ${ATTR_LINK_ATTRS}>Mapterhorn</a>`);
    }

    return {
        version: 8,
        glyphs: `${base}fonts/{fontstack}/{range}.pbf`,
        sprite: `${base}sprites/v4/${flavor}`,
        sources: {
            basemap: {
                type: "vector",
                url: "pmtiles://basemap.pmtiles",
                attribution: attrParts.join(" "),
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
// Resolves once terrain has settled for the opening viewport, hillshade
// tiles loaded, or none needed (out of coverage / config gate). Stays
// null when this map has no terrain. Set by addTerrainLayers(), consumed
// by the initial-load progress bar's hide gate in the style.load handler.
let _terrainTilesLoaded = null;
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

    // Initial paint comes from the current scheme's tokens so dark-
    // mode visitors don't see a flash of light-mode hillshade before
    // applyMapPaintForScheme() patches the layer. Subsequent scheme
    // toggles route through applyMapPaintForScheme.
    const t = MAP_PAINT_TOKENS[currentColorScheme()] || MAP_PAINT_TOKENS.light;
    map.addLayer({
        id: "hillshade",
        type: "hillshade",
        source: "terrain",
        paint: {
            "hillshade-illumination-direction": 315,
            "hillshade-illumination-anchor": "map",
            "hillshade-exaggeration": 0.4,
            "hillshade-shadow-color": t.hillshadeShadow,
            "hillshade-highlight-color": t.hillshadeHighlight,
        },
    }, beforeLayer);

    // Resolve when terrain has SETTLED for the opening viewport, used to
    // time the initial-load progress bar hide. "Settled" means either the
    // hillshade tiles have loaded, OR the view genuinely needs none (it's
    // outside the terrain pan_bbox, or at a zoom with no terrain). Both
    // are valid: terrain is a config gate, so we must never hang waiting
    // for tiles that will never come.
    //
    // The trick is to evaluate isSourceLoaded("terrain") ONLY inside an
    // 'idle' handler. A *synchronous* isSourceLoaded read is unreliable:
    // right after the pmtiles header loads there's a window where the
    // source reports loaded() === true with zero tiles requested yet, and
    // acting on that hides the bar before any elevation paints (the
    // early-hide bug, most visible on a hard reload). That window cannot
    // coexist with 'idle', by the time the map is idle its render loop
    // has run sourceCache.update() for the terrain source, so a true
    // reading here is genuine (tiles loaded, or none needed). If the
    // header is still in flight, isSourceLoaded is false and we stay armed
    // for the next idle. Listener removes itself on resolve.
    _terrainTilesLoaded = new Promise((resolve) => {
        const onIdle = () => {
            if (map.isSourceLoaded("terrain")) {
                map.off("idle", onIdle);
                resolve();
            }
        };
        map.on("idle", onIdle);
    });
}

// Resolve the color a route appears as on the map, in priority order:
//   1. dashed_relations[id].colors[0], explicit dash colors beat anything
//   2. routeInfo.colour, from OSM `colour` tag or relation_colors override
//   3. CONFIG.defaultTrailColor
function effectiveRouteColor(routeInfo) {
    if (!routeInfo) return CONFIG.defaultTrailColor;
    if (Array.isArray(routeInfo.dashColors) && routeInfo.dashColors.length > 0) {
        return routeInfo.dashColors[0];
    }
    if (routeInfo.colour) return routeInfo.colour;
    return CONFIG.defaultTrailColor;
}

// Trail casing (outline halo) color. A casing's job is to separate the
// trail from the BASEMAP, so its contrast follows the basemap, which
// tracks the color scheme (light basemap → dark casing; dark basemap →
// light casing), not the trail's own fill. Keying off the fill (the
// prior casingFromFill approach) gave dark fills a translucent-light
// casing that vanished on the light basemap, so blue/black trails read
// skinny (and, symmetrically, light trails would in dark mode). Both
// relation mode and color_by:trail share this one value, so every trail
// gets the same visible halo regardless of difficulty/route color. The
// colors live in MAP_PAINT_TOKENS so applyMapPaintForScheme() can
// re-apply them on a scheme toggle.
function trailCasingColor() {
    const t = MAP_PAINT_TOKENS[currentColorScheme()] || MAP_PAINT_TOKENS.light;
    return t.trailCasing;
}

// icon-halo-color for clip-arrow continuation arrowheads. The halo's
// job is to lift the arrow off the basemap, so, like the trail casing
// and the highlight outline, it contrasts the BASEMAP via the color
// scheme: dark halo on the light basemap, light on the dark basemap.
// Single-route arrows (drawn in the route's own color) take that
// scheme halo; shared multi-route endpoints keep a fixed light halo
// because their arrow is filled black (sharedArrowColor) and needs a
// light edge on either basemap. Re-applied on scheme toggle by
// applyMapPaintForScheme().
function clipArrowHaloExpr() {
    const t = MAP_PAINT_TOKENS[currentColorScheme()] || MAP_PAINT_TOKENS.light;
    return [
        "case",
        [">=", ["get", "visible_count"], 2],
        "rgba(255,255,255,0.85)",
        t.arrowHalo,
    ];
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

// ============================================================
// Proximity filtering helpers
// ============================================================
// Distance (meters) from the nearest visible trail within which a
// trail_marker or feature POI is allowed to render. Features tagged
// `tourism=attraction` in OSM often sit 10-50 m off the trail (scenic
// viewpoints, old structures, named rocks), 50 m is a permissive
// default that surfaces them while still filtering out incidental
// bbox POIs that aren't actually trail-adjacent. Configurable per-map
// via the `poi_proximity_m` YAML key.
const POI_PROXIMITY_METERS = CONFIG.poiProximityMeters ?? 50;

// Wider proximity threshold for amenity POIs (toilets, drinking
// water). These are utility points a rider will deliberately detour
// for, and they're commonly placed at trailheads or parking lots
// that sit further than POI_PROXIMITY_METERS off the trail polyline
// itself. 500 m ≈ a 5-7 minute walk, the right ballpark for "still
// useful to know about." Not currently a YAML knob; promote to one
// if multiple curators ask.
const POI_AMENITY_PROXIMITY_METERS = 500;

// Threshold (meters) for "on the highlighted route" during the
// spotlight dim. Intentionally tight, when a single route/trail is
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
        if (!featurePassesModeFilter(f)) continue;
        if (f.properties.isStub === true) continue;
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
// no highlight is set or routesData hasn't loaded, callers treat that
// as "too far" and dim the marker.
function distanceToHighlighted(lng, lat) {
    if (!routesData || !highlight) return Infinity;
    let minDist = Infinity;
    for (const f of routesData.features) {
        if (!featurePassesModeFilter(f)) continue;
        if (f.properties.isStub === true) continue;
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
// handling, `_opacity` vs `_opacityWhenCovered`). An inline opacity
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
    apply(hubMarkers);
    apply(featureMarkers);
    apply(toiletMarkers);
    apply(drinkingWaterMarkers);
}

function updateMarkerProximity() {
    // Toggle rows use aria-pressed semantics (not checkbox .checked).
    // Trail markers (guideposts + emergency access points) share the
    // single "Markers" toggle.
    const isOn = (id) => {
        const btn = document.getElementById(id);
        return !!btn && btn.getAttribute("aria-pressed") === "true";
    };

    const filterMarkers = (markers, on, threshold) => {
        if (!on) return;
        for (const marker of markers) {
            const { lng, lat } = marker.getLngLat();
            const dist = distanceToVisibleTrails(lng, lat);
            if (dist <= threshold) {
                marker.addTo(map);
            } else {
                marker.remove();
            }
        }
    };

    filterMarkers(trailMarkerMarkers,    isOn("toggle-markers"),         POI_PROXIMITY_METERS);
    filterMarkers(featureMarkers,        isOn("toggle-features"),        POI_PROXIMITY_METERS);
    filterMarkers(toiletMarkers,         isOn("toggle-toilets"),         POI_AMENITY_PROXIMITY_METERS);
    filterMarkers(drinkingWaterMarkers,  isOn("toggle-drinking-water"),  POI_AMENITY_PROXIMITY_METERS);

    // Markers are obstacles for the decoration placer (gatherObstacles
    // walks the four marker arrays). When any marker is added/removed
    // here, recompute the decorations so arrows/diamonds/labels skip
    // the new POI footprints (or reclaim space when a marker drops).
    invalidateObstaclesCache();
    if (map && map.getSource("trail-decorations")) {
        updateDecorationsSource();
    }

    // Re-evaluate the proximity-gated toggle rows (Markers, Features,
    // Toilets, Drinking water). If the visible-routes set leaves zero
    // of a type within its proximity threshold of any trail, that
    // toggle is a dead control, hide its row. The row comes back the
    // moment a route change brings a near-trail member into scope.
    updatePoiToggleVisibility();
}

// True iff at least one POI of the given type is within `threshold`
// meters of a currently-visible trail. Independent of the toggle's
// aria-pressed state (we ask "would anything show if it were on?",
// not "is anything showing now?"). Used by
// updatePoiToggleVisibility() to decide which proximity-gated
// toggle rows to render.
function hasVisibleProximityPois(poiType, threshold) {
    if (!poisData || !routesData) return false;
    for (const f of poisData.features) {
        if (f.properties.poi_type !== poiType) continue;
        const [lng, lat] = f.geometry.coordinates;
        if (distanceToVisibleTrails(lng, lat) <= threshold) {
            return true;
        }
    }
    return false;
}

// Show/hide each proximity-gated POI toggle (Markers, Features,
// Toilets, Drinking water) based on (a) its YAML show_* gate and
// (b) whether the proximity filter would currently let any of that
// type render at the per-type threshold. Called on initial POI load
// and after every route-visibility change. The toggle row is
// `.hidden` when dead so the Options list collapses cleanly;
// persisted aria-pressed state is untouched, so toggling a
// category back on once a near-trail POI of that type reappears
// just works.
//
// Why Markers are in this list (subtle): trail-marker render is
// proximity-filtered at the layer level (see updateMarkerProximity),
// so a marker tagged in OSM well off the trail line, typical for
// guideposts at parking-area signs or trail-entrance posts placed
// outside the bbox-tight trail polyline, never renders at the
// default 50 m threshold even when the toggle is on. Without this
// entry the toggle row stayed visible regardless, creating a
// chicken-and-egg trap: data exists → row shown → tap toggles on →
// nothing on the map. Now the row tracks whether any marker is
// currently in scope; out-of-scope markers hide the row.
function updatePoiToggleVisibility() {
    const flips = [
        ["toggle-markers",        CONFIG.showMarkers,        POI.TRAIL_MARKER,    POI_PROXIMITY_METERS,         "trail_markers"],
        ["toggle-features",       CONFIG.showFeatures,       POI.FEATURE,         POI_PROXIMITY_METERS,         "features"],
        ["toggle-toilets",        CONFIG.showToilets,        POI.TOILET,          POI_AMENITY_PROXIMITY_METERS, "toilets"],
        ["toggle-drinking-water", CONFIG.showDrinkingWater,  POI.DRINKING_WATER,  POI_AMENITY_PROXIMITY_METERS, "drinking_water"],
    ];
    for (const [id, gate, type, threshold, layerName] of flips) {
        const btn = document.getElementById(id);
        if (!btn) continue;
        // Curator-forced layers: wirePeekToggle already hid this row and
        // skipped the click wiring (the rider gets no off affordance).
        // Keep it hidden, re-revealing it on a proximity pass would
        // resurrect a dead, unclickable control. The layer itself stays
        // visible via the aria-pressed default + proximity filter,
        // independent of the row. See isForcedVisible / wirePeekToggle.
        if (isForcedVisible(layerName)) {
            btn.classList.add("hidden");
            continue;
        }
        const show = gate && hasVisibleProximityPois(type, threshold);
        btn.classList.toggle("hidden", !show);
    }
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

// fetch() with a hard timeout. The browser's own network timeout can
// run to a minute or more, and on "reported but dead" cellular a
// request can hang that whole time, leaving an awaiting data loader
// stalled with no error and no UI feedback (blank map, no toast). An
// AbortController bounds the wait so a hung request rejects promptly
// and the caller's catch can surface its "failed to load" toast. A
// cache hit served by the service worker resolves instantly and never
// reaches the timeout, so this only ever bites a genuine network
// stall. 20 s is generous enough not to abort a slow-but-real download
// of these (small) GeoJSON files; bump it if a map's data grows large.
async function fetchWithTimeout(resource, ms = 20000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    try {
        return await fetch(resource, { signal: controller.signal });
    } finally {
        clearTimeout(timer);
    }
}

async function loadTrails() {
    // trails.geojson is required, if it 404s or fails to parse, the
    // app has nothing to render. Fail loudly with a visible message
    // instead of letting downstream addSource calls die opaquely.
    try {
        const resp = await fetchWithTimeout("trails.geojson");
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
        }
        routesData = await resp.json();
    } catch (e) {
        console.error("loadTrails: trails.geojson failed to load:", e);
        showToast("Map data failed to load. Try reloading the page.");
        // This rejection skips the rest of the style.load pipeline,
        // including the map.once('idle') hide registered there, so hide
        // the bar here, otherwise it lingers under the error toast.
        if (window.__hideMapLoading) window.__hideMapLoading();
        throw e;  // halt init, there's nothing useful to render
    }

    // Optional sibling file: continuation arrowhead points at bbox-edge
    // endpoints of clipped relations. Build-time flag tells us whether
    // the file actually exists in this build (most maps don't have
    // clipped_relations); skip the fetch entirely when it doesn't, so
    // the network log isn't littered with 404s from an unconditional
    // probe-fetch.
    if (CONFIG.hasClipEndpoints) {
        try {
            const clipResp = await fetchWithTimeout("clip_endpoints.geojson");
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

    // Decoration source, pre-deconflicted Point features (trail
    // names, route names, IMBA diamonds, direction arrows). All
    // placement decisions happen in JS at compute time, so the four
    // decor layers can render with `*-allow-overlap: true` and skip
    // MapLibre's per-tile collision pipeline. See computeDecorations()
    // for the placement algorithm.
    //
    // Initial data is empty; the first computeDecorations() pass is
    // deferred to map.once('idle', …) below so the basemap + trail
    // lines paint immediately and the decoration overlay arrives in
    // the next frame. computeDecorations() is 50-200ms on dense maps
    // (4-pass placement + collision-checked label clipping); pulling
    // it out of the critical path notably tightens time-to-first-paint
    // without changing the final visual outcome.
    map.addSource("trail-decorations", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
    });
    // Open the boot gate and run the first pass. Earlier calls (e.g.
    // setupFloatingChrome's applyVisibilityChange inside style.load)
    // were dropped by the gate, this is the single first pass, off
    // the first-paint critical path. See updateDecorationsSource.
    map.once("idle", () => {
        _decorationsReady = true;
        updateDecorationsSource();
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
    // Sort: featured routes (event_mode) last so their layers add on
    // top of background routes; within each group, alphabetical by
    // name. MapLibre draws layers in addition order, so "added later"
    // means "drawn on top." Featured routes also get a wider line via
    // FEATURED_WIDTH_MULTIPLIER below.
    const sortedRoutes = Object.entries(routes)
        .sort(([, a], [, b]) => {
            const aFeat = a.featured ? 1 : 0;
            const bFeat = b.featured ? 1 : 0;
            if (aFeat !== bFeat) return aFeat - bFeat;
            return a.name.localeCompare(b.name);
        });

    // Featured routes (event mode) render at 1.5x the standard line
    // width, makes the spotlighted route(s) read as foreground
    // against the muted background trails. Applies to casing, fill,
    // and the two-color underlay layer.
    const FEATURED_WIDTH_MULTIPLIER = 1.5;

    const byDifficulty = CONFIG.colorBy === "trail";

    // Pass 1: casings
    //
    // Casing halo posture:
    //   - Solid routes always get the outline halo (1-1.5 px beyond
    //     the fill on each side, ~50% opacity, basemap-contrasting
    //     shade via trailCasingColor()).
    //   - Two-color dashed routes (`colors: [A, B]`) get the halo
    //     too. The second color renders as a solid underlay (see
    //     fill2 below) that fills the dash gaps, so a solid casing
    //     behind everything reads as a clean outline around the whole
    //     line, same visual contract as solid routes.
    //   - Single-color dashed routes (`colors: [A]` or no `colors`
    //     at all) suppress the casing. Making it solid would defeat
    //     the dashed appearance because the casing color would show
    //     through the dash gaps. A "dashed casing" alternative would
    //     need width-proportional dash-array adjustment to keep dashes
    //     aligned between casing and fill (MapLibre's line-dasharray
    //     is in line-widths, so a wider casing produces longer dashes
    //     than the fill at the same array), defer until there's a
    //     concrete need.
    for (const [routeId, routeInfo] of sortedRoutes) {
        const dashed = isDashed(routeInfo);
        const dashColors = getDashColors(routeInfo);
        const cap = getDashCap(routeInfo);
        const wmul = routeInfo.featured ? FEATURED_WIDTH_MULTIPLIER : 1;

        const hasUnderlay = !!(dashColors && dashColors.length >= 2);
        const casingVisible = !dashed || hasUnderlay;

        const casingCol = trailCasingColor();

        map.addLayer({
            id: `trail-casing-${routeId}`,
            type: "line",
            source: "trails",
            filter: ["==", ["get", "route_id"], routeId],
            paint: {
                "line-color": casingCol,
                // Visible casing uses the wider "solid-route" stops so
                // it extends ~1-1.5 px beyond the fill on each side
                // (the halo). Invisible casing keeps the narrower stops
                // since it's not drawn anyway, keeps any future
                // debugging consistent with what's actually rendered
                // for single-color dashed.
                "line-width": casingVisible
                    ? ["interpolate", ["linear"], ["zoom"], 10, 3 * wmul, 14, 6 * wmul, 18, 10 * wmul]
                    : ["interpolate", ["linear"], ["zoom"], 10, 2 * wmul, 14, 4 * wmul, 18, 7 * wmul],
                "line-offset": makeOffsetExpr(),
                "line-opacity": casingVisible ? 0.5 : 0,
                // No line-dasharray on the casing, it's always a
                // solid outline (when visible) behind either the solid
                // fill or the two-color-dashed fill+underlay stack.
            },
            layout: {
                "line-cap": cap,
                "line-join": cap === "square" ? "miter" : "round",
            },
        });
    }

    // Pass 2: fills
    for (const [routeId, routeInfo] of sortedRoutes) {
        const color = routeInfo.colour || CONFIG.defaultTrailColor;
        const dashed = isDashed(routeInfo);
        const cap = getDashCap(routeInfo);
        const dashColors = getDashColors(routeInfo);
        const wmul = routeInfo.featured ? FEATURED_WIDTH_MULTIPLIER : 1;

        const fillColor = byDifficulty
            ? difficultyColorExpr()
            : (dashColors ? dashColors[0] : color);

        // When coloring by difficulty with a dashed default, split into
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
                    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2 * wmul, 14, 4 * wmul, 18, 7 * wmul],
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
                "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2 * wmul, 14, 4 * wmul, 18, 7 * wmul],
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

    // Dim-tint, full-viewport black wash, transparent by default.
    // Rendered above the basemap + trail casings/fills but below the
    // clip-arrows, highlights, labels, difficulty, and arrows. When
    // `CONFIG.mapDimOnHighlight` is true AND a route/trail is highlighted,
    // refreshSpotlightDim() sets its opacity to SCRIM_OPACITY so the
    // basemap + non-highlighted trail lines recede behind the wash and
    // the highlighted ribbon reads as a spotlight. It stays steady while
    // a menu opens over it (the menu suppresses its own scrim instead,
    // see .has-spotlight in style.css), so the dim never animates against
    // a menu's scrim.
    map.addLayer({
        id: "dim-tint",
        type: "background",
        paint: {
            "background-color": "#000",
            // Starts transparent; refreshSpotlightDim() sets it to
            // SCRIM_OPACITY whenever a highlight is active. The wash must
            // change INSTANTLY, and duration:0 is REQUIRED to get that:
            // MapLibre's default paint transition is 300ms, so without it
            // the opacity fades in over ~300ms. On the "tap a search
            // result" path (the menu scrim is removed instantly while the
            // camera flies ~300ms) that fade reads as the background
            // un-dimming then re-dimming during the fly. duration:0 makes
            // the wash an atomic swap with the menu scrim instead of an
            // animation against it (two fading black layers also wouldn't
            // composite to a constant → wobble). The wash is steady while
            // up; menus suppress their own scrim over it (see
            // .has-spotlight in style.css). The SAME SCRIM_OPACITY drives
            // the CSS overlay scrims via --scrim-opacity (set in init()),
            // so both share one density.
            "background-opacity": 0,
            "background-opacity-transition": { duration: 0 },
        },
    });

    // Pass 3: continuation arrowheads at clipped-relation bbox endpoints.
    if (map.getSource("clip-endpoints")) {
        for (const [routeId, routeInfo] of sortedRoutes) {
            // Per-endpoint color logic, paired:
            //   visible_count >= 2 (multi-route convergence) →
            //     fill black (composited alpha via sharedArrowColor)
            //     with a fixed light halo so the dark arrow stays
            //     readable on either basemap.
            //   single route →
            //     fill with the route's actual color; halo contrasts the
            //     basemap via the scheme (see clipArrowHaloExpr), matching
            //     the trail casing and highlight outline.
            const iconCol = [
                "case",
                [">=", ["get", "visible_count"], 2],
                sharedArrowColor(),
                effectiveRouteColor(routeInfo),
            ];
            const haloCol = clipArrowHaloExpr();
            map.addLayer({
                id: `clip-arrow-${routeId}`,
                type: "symbol",
                source: "clip-endpoints",
                // Pipe-delimited substring match, leading/trailing `|`
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
                    // arrow, clip-continuation indicators benefit
                    // from extra visual weight since they signal
                    // "trail leaves the map" rather than ongoing
                    // direction.
                    "icon-size": ["interpolate", ["linear"], ["zoom"],
                        12, 1.2, 14, 1.65, 18, 2.4],
                    // Push the arrowhead away from the trail's
                    // clipped end so it doesn't crowd the line where
                    // it meets the bbox edge. Offset is in pre-
                    // rotation icon space (Y is "up" in the asset's
                    // tip-up frame), then rotates with the bearing,
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
    // real filter and set the dynamic color.
    //
    // History: this used to be a four-layer sandwich (outline, blurred
    // glow, stroke, white core). The glow was removed first, its
    // additive alpha created bright spikes at sharp switchback bends.
    // The white core was dropped when the route stroke switched to the
    // route's NATIVE color (was amber), with native color, the white
    // core blurred into light-colored routes (yellow, cream) and its
    // zoom-dependent width meant the inner-stripe effect was visible
    // only at high zoom. The remaining outline + color-stroke pair
    // gets the structural emphasis from sheer thickness (~2× the
    // unhighlighted fill width) plus the always-on scheme-contrasting
    // silhouette (black on the light basemap, white on the dark);
    // the spotlight dim (mapDimOnHighlight) does the rest of the
    // visibility work by receding everything else.
    const NONE_FILTER_ROUTE = ["==", ["get", "route_id"], "___NONE___"];
    const NONE_FILTER_TRAIL = ["==", ["get", "trail_name"], "___NONE___"];

    // Route highlight ribbon (bottom → top), over an optional amber glow:
    //   outline:  thick silhouette around the highlight ribbon. Color
    //             is bidirectional (black for light routes, white for
    //             dark), set by highlightRoute() to mirror the
    //             unhighlighted casing's edge direction so the route's
    //             "edge" reads consistently in both states. Initial
    //             #000 here is a fail-safe; real highlights overwrite
    //             on every selection.
    //   underlay: same width as the stroke, invisible (opacity 0) for
    //             most routes. For a two-color dashed route (`colors:
    //             [A, B]`) highlightRoute() paints it B and raises the
    //             opacity so the dash gaps show B, mirroring the
    //             trail-fill2 underlay in the unhighlighted rendering.
    //   stroke:   opaque, painted with the route's native color by
    //             highlightRoute(). Width ~2× the unhighlighted fill so
    //             the highlight reads as "this route, scaled up."
    //             Dashed routes keep their dash pattern here (set per
    //             selection by highlightRoute()); single-color dash
    //             gaps show the outline silhouette beneath, so the
    //             ribbon stays a continuous selected band with the
    //             dash identity riding on top. The outline itself
    //             can't be dashed: line-dasharray is measured in
    //             line-widths, so the wider outline can't keep its
    //             dashes aligned with the stroke (same reason the
    //             casing suppresses dashes, see the casing comment).
    // line-color-transition: { duration: 0 } on every highlight layer.
    // MapLibre's default line-color transition is 300ms; without
    // overriding it, every setPaintProperty('line-color', ...) on a
    // highlight layer animates from its previous value to the new
    // one over 300ms. When the filter then activates the layer, the
    // user sees the color mid-animation, that's the "flash" from
    // amber (or the previous route's color) to the target. Setting
    // duration: 0 makes the color change instantaneous, so by the
    // time the filter exposes the layer it's already at the target.
    //
    // Optional selection glow (highlight_glow, default on): a soft amber
    // aura BENEATH the ribbon so any route, a black one included, reads
    // unmistakably as selected against the dimmed map. Rendered as an
    // OPAQUE core + line-blur, not a translucent wide stroke: the earlier
    // translucent glow was removed because its alpha doubled where a line
    // self-overlaps at a tight switchback, spiking bright at hairpins. An
    // opaque core can't double; the blur supplies the soft outer falloff.
    // Amber matches the highlight-chip accent (--highlight-amber).
    if (CONFIG.highlightGlow !== false) {
        map.addLayer({
            id: "route-highlight-glow",
            type: "line",
            source: "trails",
            filter: NONE_FILTER_ROUTE,
            paint: {
                "line-color": "#ffb700",
                "line-width": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 17, 18, 28],
                "line-blur": ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 5, 18, 7],
                "line-opacity": 1,
                "line-offset": makeOffsetExpr(),
            },
            layout: { "line-cap": "round", "line-join": "round" },
        });
    }
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
        id: "route-highlight-underlay",
        type: "line",
        source: "trails",
        filter: NONE_FILTER_ROUTE,
        paint: {
            "line-color": "#000",
            "line-color-transition": { duration: 0 },
            // Hidden by default; highlightRoute() raises the opacity
            // only for two-color dashed routes. Transition duration 0
            // for the same flash-avoidance reason as line-color.
            "line-opacity": 0,
            "line-opacity-transition": { duration: 0 },
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 18, 13],
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
            // line-dasharray is a cross-faded property with a default
            // 300ms transition: without this override, switching from a
            // dashed selection to a solid one (or between dash patterns)
            // crossfades the old pattern into the new over 300ms, and
            // since the new route's filter lands immediately, the new
            // route renders with the OLD dashes fading out (e.g. a solid
            // route flashing dashed for a beat). Duration 0 snaps the
            // pattern so the filter only ever exposes the target dash.
            "line-dasharray-transition": { duration: 0 },
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 18, 13],
            "line-opacity": 1,
            "line-offset": makeOffsetExpr(),
        },
        layout: { "line-cap": "round", "line-join": "round" },
    });
    // Trail highlight ribbon (bottom → top), over an optional amber glow:
    //   outline:  thick scheme-contrasting silhouette (black on light
    //             basemap, white on dark, see applyMapPaintForScheme)
    //   stroke:   highlighter yellow (#FFEC00), see highlightTrail().
    //             Trails span multiple routes so they have no native
    //             color; the highlighter yellow is the framework's
    //             "no color of its own" emphasis state.
    // Optional selection glow, see the route glow above for why it's an
    // opaque core + blur rather than a translucent stroke.
    if (CONFIG.highlightGlow !== false) {
        map.addLayer({
            id: "trail-highlight-glow",
            type: "line",
            source: "trails",
            filter: NONE_FILTER_TRAIL,
            paint: {
                "line-color": "#ffb700",
                "line-width": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 17, 18, 28],
                "line-blur": ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 5, 18, 7],
                "line-opacity": 1,
                "line-offset": makeOffsetExpr(),
            },
            layout: { "line-cap": "round", "line-join": "round" },
        });
    }
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

    // Route-name labels, one layer per route so each follows its
    // own offset line. Only rendered when labelMode === "routes";
    // trail-name labels render from the trail-decorations source via
    // decor-trail-name so each trail name appears exactly once per
    // physical way regardless of how many routes share it.
    //
    // Event mode: only the featured route(s) get an addLayer, so
    // background routes can never render a label even if the rider
    // somehow flips labelMode. Saves layer churn and removes a class
    // of bug where a hidden segmented-control click could surface
    // background labels.
    for (let li = 0; li < sortedRoutes.length; li++) {
        const [routeId, routeInfo] = sortedRoutes[li];
        if (CONFIG.eventModeActive && !routeInfo.featured) continue;

        // Stagger labels along the line so shared-segment route names
        // don't stack up. With symbol-placement "line", text-offset x
        // shifts along the line direction (in ems).
        const stagger = (li % 4) * 4;   // 0, 4, 8, 12 ems

        map.addLayer({
            id: `trail-label-${routeId}`,
            type: "symbol",
            source: "trails-labels",
            minzoom: LABEL_CROSSOVER_ZOOM,
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
                // Small buffer on the label itself, it doesn't take
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

    // Decoration layers, IMBA diamonds and direction arrows are
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
    // Apply the current color scheme's paint tokens, sets label
    // text-color / halo and arrow icon-image to the scheme-correct
    // values. Decoration layers ship with light-mode defaults; this
    // ensures dark-mode visitors see the right colors immediately
    // (no flash of light-mode labels).
    applyMapPaintForScheme(currentColorScheme());

    // Sync layer visibility to the current bucket-model state before
    // we return, without this, every route's layers default to
    // "visible" and any route hidden by the bucket model (e.g.,
    // emergency-access while emergencyOn is false) would briefly
    // paint during the await loadPOIs() yield in init() before
    // setupFloatingChrome → applyVisibilityChange runs. See the
    // comment on _applyPerRouteLayerVisibility for the full reasoning.
    _applyPerRouteLayerVisibility();
}

// Build a filter expression for a label layer on the trail-decorations
// source. Always includes the KIND/zoom gate (so the layer only renders
// features of the right kind, at zooms ≥ the feature's min_zoom);
// callers append any highlight-narrowing filters as `extraFilters`.
//
// Centralizing the gate prevents the three label-layer blocks below
// from drifting in their kind/zoom logic, every label feature is
// gated through this one place.
function buildLabelFilter(kind, ...extraFilters) {
    return ["all",
        ["==", ["get", "kind"], kind],
        ["<=", ["get", "min_zoom"], ["zoom"]],
        ...extraFilters,
    ];
}

function updateLabels() {
    // Under a ROUTE-highlight dim, ALL name labels narrow to the
    // highlighted route: route names to the route itself (an event map's
    // "Full Route" label riding a dimmed line while Short Route is
    // spotlit reads as if the wrong route were highlighted), and trail
    // names to the trails along that route (bright names floating on
    // dimmed background lines undercut the spotlight, the route's own
    // segment names are what the rider is tracing).
    //
    // Under a TRAIL highlight, labels are deliberately NOT narrowed: the
    // wash recedes the surrounding network visually, but its names stay
    // readable so a rider can still trace the connecting trails needed
    // to reach the highlighted one (the in-field "how do I get there?"
    // case).
    const routeNarrow = (highlightDimActive() && highlight.kind === "route")
        ? highlight.key : null;

    // Per-route route-name label layers, each scoped to one route via
    // its baseline filter (set at layer creation, not here). Source
    // data (computeLabelData) only carries shared-way features now;
    // solo-way labels live on the decor-route-name layer. Visible only
    // in "routes" mode.
    for (const routeId of Object.keys(CONFIG.routes)) {
        const layerId = `trail-label-${routeId}`;
        if (!map.getLayer(layerId)) continue;

        const visible = labelMode === "routes" && visibleRoutes.has(routeId)
            && (!routeNarrow || routeId === routeNarrow);
        map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    }

    // Solo-way route-name labels (one LineString per way with exactly
    // one visible route, labeled with that route's name). Visible
    // only in "routes" mode.
    //
    // Event mode renders these identically to a normal map, the
    // source only carries featured-route labels there (computeDecorations
    // gates emission via routeLabelAllowed), so the muted background
    // network stays unlabeled without any layer-level special case.
    if (map.getLayer("decor-route-name")) {
        const visible = labelMode === "routes";
        map.setLayoutProperty("decor-route-name",
            "visibility", visible ? "visible" : "none");
        if (visible) {
            map.setFilter("decor-route-name", routeNarrow
                ? buildLabelFilter(KIND.ROUTE_NAME,
                    ["==", ["get", "solo_route_id"], routeNarrow])
                : buildLabelFilter(KIND.ROUTE_NAME));
        }
    }

    // Trail-name labels (one per physical way). Visible only in
    // "trails" mode.
    if (map.getLayer("decor-trail-name")) {
        const visible = labelMode === "trails";
        map.setLayoutProperty("decor-trail-name", "visibility",
            visible ? "visible" : "none");
        if (visible) {
            map.setFilter("decor-trail-name", routeNarrow
                ? buildLabelFilter(KIND.TRAIL_NAME,
                    ["in", routeNarrow, ["get", "shared_routes"]])
                : buildLabelFilter(KIND.TRAIL_NAME));
        }
    }

    // Overview point labels, same mode-based visibility as the line
    // labels above; the layer maxzoom (set at creation) restricts them
    // to overview zoom.
    if (map.getLayer("decor-route-name-pt")) {
        const visible = labelMode === "routes";
        map.setLayoutProperty("decor-route-name-pt",
            "visibility", visible ? "visible" : "none");
        if (visible) {
            map.setFilter("decor-route-name-pt", routeNarrow
                ? buildLabelFilter(KIND.ROUTE_LABEL_PT,
                    ["==", ["get", "solo_route_id"], routeNarrow])
                : buildLabelFilter(KIND.ROUTE_LABEL_PT));
        }
    }
    if (map.getLayer("decor-trail-name-pt")) {
        const visible = labelMode === "trails";
        map.setLayoutProperty("decor-trail-name-pt",
            "visibility", visible ? "visible" : "none");
        if (visible) {
            map.setFilter("decor-trail-name-pt", routeNarrow
                ? buildLabelFilter(KIND.TRAIL_LABEL_PT,
                    ["in", routeNarrow, ["get", "shared_routes"]])
                : buildLabelFilter(KIND.TRAIL_LABEL_PT));
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
// Mode key matching the build-time route_order.enumerate_modes
// scheme. Used to look up the active routeOrder in CONFIG.routeOrders
// and to filter mode-tagged features (stubs + host variants).
function currentModeKey() {
    const season = (seasonMode === "winter") ? "winter" : "summer";
    return emergencyOn ? `${season}_emergency` : season;
}

// Per-mode global rank map. Sorts each corridor's routes by index
// into the active mode's routeOrder (from CONFIG.routeOrders). Falls
// back to natural-sort if the active mode's order isn't available
// (e.g. legacy builds, or modes that resolved to no routes).
//
// Returned Map maps route_id → rank (lower = first in the corridor).
// Routes that don't appear in routeOrder fall to the end, then sort
// by ROUTE_ID_COMPARE among themselves.
function modeAwareGlobalRank() {
    const orders = CONFIG.routeOrders || {};
    const mode = currentModeKey();
    const order = orders[mode];
    if (!Array.isArray(order) || order.length === 0) {
        // Legacy fallback: natural-sort over CONFIG.routes.
        const allRouteIds = Object.keys(CONFIG.routes)
            .slice().sort(ROUTE_ID_COMPARE);
        const rank = new Map();
        allRouteIds.forEach((id, i) => rank.set(id, i));
        return rank;
    }
    const rank = new Map();
    order.forEach((id, i) => rank.set(String(id), i));
    // Append any CONFIG-listed routes missing from the order (e.g.
    // custom routes added post-build) at the end, by natural-sort.
    const missing = Object.keys(CONFIG.routes)
        .filter((id) => !rank.has(id))
        .sort(ROUTE_ID_COMPARE);
    let next = order.length;
    missing.forEach((id) => rank.set(id, next++));
    return rank;
}

// Stable-lane offset for a route sitting at `position` within
// `visibleShared` (already rank-sorted). Adds the corridor's baseline
// (CONFIG.corridorBaselines[mode], computed at build time by
// corridor_baselines.py) so each route holds a stable lane instead of
// re-centering ("breathing") sideways whenever a neighbor joins or
// leaves the corridor. Falls back to the legacy centered offset
// (position - (n-1)/2) when no baseline exists for the corridor, e.g.
// custom routes added after the build, or maps with no modes. The
// corridor key is the rank-sorted ids joined by '|', identical to the
// build-time key, so stub offsets and corridor offsets line up exactly.
function laneOffset(position, visibleShared) {
    const n = visibleShared.length;
    if (n <= 1 || position < 0) return 0;
    const baselines = (CONFIG.corridorBaselines || {})[currentModeKey()];
    if (baselines) {
        const b = baselines[visibleShared.join("|")];
        if (b !== undefined) return position + b;
    }
    return position - (n - 1) / 2;
}

// Mode-tag filter: a feature renders if it has no `mode` property
// (mode-independent, most features) OR its `mode` matches the
// current mode. Stubs and host variants emitted by mode-aware
// apply_subway_style_modes carry the property; everything else
// passes through.
function featurePassesModeFilter(f) {
    const m = f.properties && f.properties.mode;
    return !m || m === currentModeKey();
}

function computeOffsetsAndFilter() {
    if (!routesData) return { type: "FeatureCollection", features: [] };

    const globalRank = modeAwareGlobalRank();
    const features = routesData.features
        .filter(featurePassesModeFilter)
        .map((f) => {
        const props = f.properties;
        const routeId = props.route_id;

        // Subway-style transition micro-features (isStub: true,
        // emitted by parallel_routes.apply_subway_style at build
        // time) come with their offset_index pre-baked, a fractional
        // value that interpolates between adjacent corridors' offsets.
        // Recomputing from shared_routes would clobber that with 0
        // (since their shared_routes is just [route_id]). Pass them
        // through unchanged.
        if (props.isStub === true) {
            return f;
        }

        const shared = props.shared_routes || [routeId];

        const visibleShared = shared
            .filter((id) => visibleRoutes.has(id))
            .sort((a, b) => globalRank.get(a) - globalRank.get(b));
        const position = visibleShared.indexOf(routeId);
        const offsetIndex = laneOffset(position, visibleShared);

        let geometry = f.geometry;
        // Apply Chaikin corner-cutting to ALL non-stub line features.
        // The earlier gate (offsetIndex !== 0) produced a visual
        // inconsistency where solo segments stayed sharp-cornered
        // while shared segments got smoothed, most visible at the
        // boundary between a shared corridor and the route's solo
        // section. Smoothing everything keeps corners consistent.
        if (geometry.type === "LineString" && geometry.coordinates.length >= 3) {
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

    const globalRank = modeAwareGlobalRank();
    const features = routesData.features
        .filter(featurePassesModeFilter)
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
            const offsetIndex = laneOffset(position, visibleShared);

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
        // Drop solo ways, their route-name labels are emitted by the
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
// Bucket model, non-exclusive flags + season mode + emergency toggle
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
    // The Finder's in-scope POI cache keys off visibleRoutes, drop it
    // before rebuildFinderList (below) repopulates against the new set.
    invalidateFinderPoiScope();
    updateTrailDisplay();
    updateMarkerProximity();
    rebuildFinderList();
    rebuildRoutePanel();
    pruneInvisibleHighlights();
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

// Sync every per-route layer's MapLibre visibility with the current
// visibleRoutes set. Called from updateTrailDisplay() on every
// bucket-model change AND from loadTrails() right after the layers
// are added, without that initial sync, MapLibre's default
// visibility ("visible") would let bucket-hidden routes (e.g., an
// emergency-access route while emergencyOn is false) paint briefly
// during the await loadPOIs() yield in init(), before
// setupFloatingChrome → applyVisibilityChange runs. On a fast
// connection that's imperceptible; on 4G it's a visible flicker.
function _applyPerRouteLayerVisibility() {
    for (const routeId of Object.keys(CONFIG.routes)) {
        const vis = visibleRoutes.has(routeId) ? "visible" : "none";
        for (const prefix of ["trail-casing-", "trail-fill-", "trail-fill2-",
                              "trail-fill-unrated-", "trail-label-",
                              "clip-arrow-"]) {
            const layerId = prefix + routeId;
            if (map.getLayer(layerId)) {
                map.setLayoutProperty(layerId, "visibility", vis);
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
    // out, and so the obstacle / way-length set the placer sees
    // matches the new state.
    updateDecorationsSource();

    _applyPerRouteLayerVisibility();

    recomputeClipEndpointVisibility();

    // applyDimState() rebuilds labels / decoration filters /
    // clip-arrow visibility in a single pass, respecting whether the
    // spotlight dim is currently active.
    applyDimState();
}

// ============================================================
// Highlight system
// ============================================================
// Layer groups, per highlight kind the ribbon is up to four stacked
// line layers that share one filter (set / cleared together):
//   glow (optional): soft amber aura beneath everything; constant
//            color, opaque core + blur. Gated by highlight_glow, the
//            id stays in the arrays below and the getLayer() guards in
//            highlightRoute/Trail skip it when the layer wasn't created.
//   outline: thick silhouette. For routes it's luminance-matched to the
//            route's own color (white for a dark route, black for a
//            light one, see highlightOutlineForColor) so the ribbon
//            keeps a readable edge under the wash; for trails it's the
//            scheme-contrasting silhouette (black on light, white on
//            dark).
//   underlay (routes only): stroke-width layer that highlightRoute()
//            paints with a two-color dashed route's second color so
//            dash gaps show it, hidden (opacity 0) otherwise.
//   stroke:  recolored per highlight via setPaintProperty, the route's
//            native color (chip + ribbon agree on identity), or the
//            framework's "highlighter yellow" #FFEC00 for trails (which
//            span multiple routes, so no single native color). Dashed
//            routes carry their dash pattern + cap here too, set per
//            selection by highlightRoute().
//
// History: an earlier four-layer sandwich (outline + blurred glow +
// stroke + white core) lived here. The white core was dropped for good
// (zoom-dependent visibility; blurred into light routes). The glow was
// removed once too, its TRANSLUCENT wide stroke spiked bright where a
// line self-overlaps at switchbacks, and is now back in a spike-safe
// form (opaque core + line-blur; see the layer definition).
const ROUTE_HIGHLIGHT_LAYERS = [
    "route-highlight-glow",
    "route-highlight-outline",
    "route-highlight-underlay",
    "route-highlight-stroke",
];
const TRAIL_HIGHLIGHT_LAYERS = [
    "trail-highlight-glow",
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
// Gated behind `CONFIG.mapDimOnHighlight` (per-map YAML, default ON,
// opt out with `map_dim_on_highlight: false`). When active,
// highlighting a route or trail dims the rest of the map:
//   - The `dim-tint` background layer fades in, washing the basemap +
//     non-highlighted trail casings/fills toward black (strength is
//     SCRIM_OPACITY, from scrim_opacity).
//   - Difficulty icons, one-way arrows, and clip-arrows are narrowed to
//     the highlighted route/trail only (so they don't punch through the
//     tint on other lines). When a ROUTE is highlighted, name labels
//     (route AND trail names) narrow with them (see updateLabels); under
//     a TRAIL highlight, labels are deliberately NOT narrowed, they
//     stay readable so connecting trails can still be followed to reach
//     the highlighted one.
//   - POI markers (DOM overlay, above the WebGL canvas) fade to ~0.25
//     unless adjacent to the highlight, via Marker.setOpacity() in
//     updateMarkerDimState().
// The in-map wash and the Search / Options / About menu backdrops share
// one scrim density (SCRIM_OPACITY / --scrim-opacity) but are never both
// animated at once: while a highlight is active the wash is steady and
// the menus suppress their own backdrop (CSS .has-spotlight) so the
// steady wash shows through; with no highlight the menu's own scrim
// provides the dim. Two fading black layers don't composite to a
// constant, so keeping it to one animated layer avoids the "variable
// dimming" wobble, see refreshSpotlightDim().
// Clearing the highlight, or toggling the config off, restores normal
// visibility in one pass via applyDimState().

function highlightDimActive() {
    // Default ON, opt out with `map_dim_on_highlight: false` in YAML.
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

// True while an About/Welcome modal is on screen. These are the only
// surfaces with a full-viewport backdrop that covers (and must dim) an
// Options panel sitting behind them, see refreshSpotlightDim. Search /
// Options are overlays, NOT modals, and are deliberately excluded.
function anyModalOpen() {
    const shown = (id) => {
        const el = document.getElementById(id);
        return !!el && !el.classList.contains("hidden");
    };
    return shown("about-modal") || shown("welcome-modal");
}

// Drive the in-map spotlight wash + mirror its state onto <body
// class="has-spotlight"> for CSS.
//
// Why not couple this to whether a *menu overlay* is open? Because the
// wash (a WebGL background layer) and an overlay's own scrim (a CSS layer)
// are two independent black layers. An earlier version cross-faded them on
// open/close to avoid stacking, but two fading black layers don't
// composite to a constant: `1−(1−a)(1−b)` dips below the target mid-fade,
// and the WebGL vs CSS easings differ, so the map "breathed" (variable
// dimming). The fix: never animate two scrims at once. The wash is steady
// (and instant) while a highlight is active, and the Search/Options
// overlays suppress THEIR OWN scrim while .has-spotlight is set (CSS), so
// the steady wash shows through, opening a menu over a highlight, or
// switching results, changes nothing about the wash.
//
// A/W MODAL EXCEPTION: the wash yields to an About/Welcome modal. That
// modal's backdrop covers the whole viewport, including the Options
// panel behind it, and dims it. The in-map wash can't dim that panel (a
// DOM box above the WebGL canvas), and keeping the wash on would also
// double-dim the map under the backdrop. So while a modal is open the
// wash is suppressed and the modal backdrop (same --scrim-opacity) is the
// single scrim, dimming map + panel uniformly whether or not a highlight
// is active. (Overlays are excluded from anyModalOpen(), so the wash
// stays on behind a Search sheet, the highlighted route still reads.)
function refreshSpotlightDim() {
    if (!map) return;
    const on = highlightDimActive() && !anyModalOpen();
    // Guard the layer, not the whole function: the body class must stay
    // in sync even when dim-tint is momentarily absent (style rebuild),
    // or a stale .has-spotlight would leave every overlay scrimless.
    if (map.getLayer("dim-tint")) {
        map.setPaintProperty("dim-tint", "background-opacity", on ? SCRIM_OPACITY : 0);
    }
    document.body.classList.toggle("has-spotlight", on);
}

// One-shot sync of everything that responds to dim + highlight state.
// Called from highlightRoute / highlightTrail / clearHighlight, and
// from updateTrailDisplay() so season/emergency toggles don't
// accidentally re-enable non-highlighted labels under an active dim.
function applyDimState() {
    refreshSpotlightDim();
    updateLabels();
    updateDecorationsHighlight();
    updateClipArrowsDim();
    updateMarkerDimState();
}

// sRGB channels (0-255) of any CSS color. Uses a 1×1 canvas so it
// resolves hex, rgb(), and named colors (an OSM `colour` tag can be
// any of these) without a bespoke parser; an unparseable value leaves
// the prior valid fill (#000) → treated as dark. Cheap: called once
// per route highlight / scheme toggle.
let _lumCtx = null;
function parseColorRgb(cssColor) {
    if (!_lumCtx) {
        const c = document.createElement("canvas");
        c.width = c.height = 1;
        _lumCtx = c.getContext("2d", { willReadFrequently: true });
    }
    _lumCtx.fillStyle = "#000000";
    _lumCtx.fillStyle = cssColor;   // invalid input keeps the prior value
    _lumCtx.fillRect(0, 0, 1, 1);
    const [r, g, b] = _lumCtx.getImageData(0, 0, 1, 1).data;
    return [r, g, b];
}

// Highlight-outline color for a route painted in `color`. The ribbon is
// outline (wide) + the route's own-color stroke (narrow); the outline
// frames that stroke so the route reads as selected. Keying it to the
// STROKE's luminance (dark route → white silhouette, light route →
// black) is what keeps a black/dark route visible: under the spotlight
// wash the dimmed background is dark, so the old basemap-contrasting
// black outline vanished into it along with the black stroke. Contrast
// against the stroke instead and the ribbon always has a readable edge,
// on either basemap and at any wash level.
//
// Greys are special-cased. `gray` (#808080) has luminance 0.502, just
// over the binary threshold, so it used to draw a BLACK outline: a grey
// stroke framed in black on the black spotlight wash, nothing popped.
// Low-chroma colors in the middle luminance band (the grey family) get
// the white silhouette instead, so a grey route reads as grey riding a
// bright band. Saturated colors keep the plain luminance split.
//
// isGreyFamilyColor is split out because routeHighlightOutlineColor
// applies the same exception to its scheme-silhouette pick for
// single-color dashed routes.
function isGreyFamilyColor(color) {
    const [r, g, b] = parseColorRgb(color);
    const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    const chroma = (Math.max(r, g, b) - Math.min(r, g, b)) / 255;
    return chroma < 0.15 && lum >= 0.35 && lum <= 0.65;
}

function highlightOutlineForColor(color) {
    if (isGreyFamilyColor(color)) return "#ffffff";
    const [r, g, b] = parseColorRgb(color);
    const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    return lum < 0.5 ? "#ffffff" : "#000000";
}

// Outline silhouette for a highlighted ROUTE. Normally the stroke-
// contrast pick above, with one exception: single-color dashed routes.
// Their dash gaps expose the outline, so it doubles as the dash
// BACKGROUND; stroke-contrast would flip that background light/dark
// per route color (dark-blue dashes on white, yellow dashes on black),
// reading as inconsistent styling between routes. Those routes use the
// constant scheme-contrasting silhouette instead (black on the light
// basemap, white on dark, same token as the trail outline), so every
// single-color dashed highlight shares one background per scheme.
// Two-color dashes fill their gaps with the underlay and solid strokes
// cover the outline's interior entirely, both keep stroke-contrast.
// Grey-family strokes (isGreyFamilyColor) override the scheme pick and
// always take white: the light scheme's black silhouette put grey
// dashes on a black band under the black spotlight wash, murky, the
// same failure the grey special case exists to fix. Grey connectors
// are by far the common single-color dashed case, so in practice
// these routes read consistently anyway.
// Callers: highlightRoute() on selection, applyMapPaintForScheme() on
// a scheme toggle mid-highlight.
function routeHighlightOutlineColor(info) {
    const dashColors = getDashColors(info);
    if (isDashed(info) && !(dashColors && dashColors.length >= 2)) {
        if (isGreyFamilyColor(effectiveRouteColor(info))) return "#ffffff";
        const t = MAP_PAINT_TOKENS[currentColorScheme()] || MAP_PAINT_TOKENS.light;
        return t.highlightOutline;
    }
    return highlightOutlineForColor(effectiveRouteColor(info));
}

function highlightRoute(routeId) {
    const info = CONFIG.routes[routeId];
    if (!info) return;
    // Single-highlight invariant across kinds: drop any POI highlight
    // (rings, force-mounted markers, open popup) before lighting a
    // route. The route/trail filters clear each other inline below.
    clearPoiHighlight();
    highlight = { kind: "route", key: routeId };

    // Highlight the route in its OWN color, not a non-native accent
    // color. Routes have an identity (a chip swatch, an Options-row
    // color, OSM's `colour` tag); the highlight inherits that
    // identity by coloring the stroke with effectiveRouteColor().
    // Structural emphasis comes from the layered architecture: a
    // thick scheme-contrasting outline beneath (black on the light
    // basemap, white on dark), the route-colored stroke at ~2x the
    // unhighlighted fill width, and the spotlight dim receding
    // every other layer. Together they unmistakably signal "this
    // route is selected" without recoloring the route itself.
    const color = effectiveRouteColor(info);
    const routeFilter = ["==", ["get", "route_id"], routeId];
    // Set paint BEFORE flipping the filter. setFilter activates the
    // layer (or switches it to a new route's geometry); whatever
    // line-color is currently set paints for one frame before any
    // subsequent setPaintProperty takes effect. That produced a
    // visible "flash" of either the hardcoded fail-safe color (first
    // highlight) or the previous route's color (when switching
    // between routes) before the new color landed. Setting paint
    // first means the filter activation already finds the right
    // color in place. (The outline isn't set here, it's a constant
    // scheme-contrasting silhouette owned by applyMapPaintForScheme.)
    for (const layerId of ROUTE_TINTED_HIGHLIGHT_LAYERS) {
        if (map.getLayer(layerId)) {
            map.setPaintProperty(layerId, "line-color", color);
        }
    }
    // Outline silhouette: luminance-matched to this route's color so a
    // dark/black route keeps a readable light edge under the wash, or
    // the scheme silhouette for single-color dashed routes whose gaps
    // expose it as the dash background (see routeHighlightOutlineColor).
    // Set before the filter activates the layer, same flash-prevention
    // ordering as the stroke above.
    if (map.getLayer("route-highlight-outline")) {
        map.setPaintProperty("route-highlight-outline", "line-color",
            routeHighlightOutlineColor(info));
    }
    // Dash identity: the stroke mirrors the route's own dash pattern
    // and cap so a dashed relation still reads as dashed while
    // highlighted. getDashPattern returns [1, 0] (solid) for
    // non-dashed routes, so switching from a dashed selection to a
    // solid one resets cleanly. Dash units are line-widths, so at the
    // stroke's ~2× width the dashes render ~2× longer than the
    // unhighlighted fill: "this route, scaled up." Gaps show the
    // outline silhouette (or the two-color underlay below), keeping
    // the ribbon a continuous selected band.
    if (map.getLayer("route-highlight-stroke")) {
        const dashCap = isDashed(info) ? getDashCap(info) : "round";
        map.setPaintProperty("route-highlight-stroke", "line-dasharray",
            getDashPattern(info));
        map.setLayoutProperty("route-highlight-stroke", "line-cap", dashCap);
        map.setLayoutProperty("route-highlight-stroke", "line-join",
            dashCap === "square" ? "miter" : "round");
    }
    // Two-color dash underlay: paint the second color into the dash
    // gaps, mirroring the trail-fill2 underlay in the unhighlighted
    // rendering. Hidden (opacity 0) for everything else.
    if (map.getLayer("route-highlight-underlay")) {
        const dashColors = getDashColors(info);
        const hasUnderlay = !!(dashColors && dashColors.length >= 2);
        map.setPaintProperty("route-highlight-underlay", "line-color",
            hasUnderlay ? dashColors[1] : "#000");
        map.setPaintProperty("route-highlight-underlay", "line-opacity",
            hasUnderlay ? 1 : 0);
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
    // Chip and ribbon now share the same color by construction.
    const indexEntry = routeIndex.find((r) => r.id === routeId);
    showHighlightChip({
        label: info.name,
        color,
        stats: indexEntry ? routeStatsText(indexEntry) : "",
        // Dash fields for the chip swatch, built from CONFIG directly
        // (mirroring buildRouteIndex) rather than indexEntry so the
        // chip renders correctly even if routeIndex isn't built yet.
        route: {
            color,
            dashed: Array.isArray(info.dashed) ? info.dashed : null,
            dashCap: info.dashCap || null,
            dashColors: Array.isArray(info.dashColors) ? info.dashColors : null,
        },
    });

    // Spotlight dim (no-op unless CONFIG.mapDimOnHighlight is on)
    applyDimState();

    // Mark this route's panel key row as the selected one.
    syncRoutePanelActiveRow();
}

function highlightTrail(trailName) {
    // Drop any POI highlight before lighting a trail (single-highlight
    // invariant across kinds); route filters clear inline below.
    clearPoiHighlight();
    highlight = { kind: "trail", key: trailName };

    // Trails span multiple routes, no single native color to
    // inherit. Use highlighter yellow (#FFEC00, Stabilo Boss territory)
    // as the framework's "no color of its own" emphasis state. Reads
    // unmistakably as "selected" without claiming the trail belongs to
    // any one route.
    const highlighter = "#FFEC00";
    const trailFilter = ["==", ["get", "trail_name"], trailName];
    // Paint before filter, same flash-prevention pattern as
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

    // A trail highlight is not a route highlight, clear any panel
    // key row marked from a previous route selection.
    syncRoutePanelActiveRow();
}

// Pending deferred-popup state for the single-POI highlight path.
// highlightPoi arms BOTH a moveend listener and a 1200 ms safety
// timeout to open the popup after the fly-to; whichever fires first
// must disarm the other, and REPLACING the highlight must disarm
// both. Without this, tapping POI A then POI B within ~1.2 s let A's
// still-armed timer (or its moveend listener catching B's fly-to)
// reopen A's popup beside B's highlight.
let _poiPopupTimer = null;
let _poiPopupMoveHandler = null;

function _cancelPendingPoiPopup() {
    if (_poiPopupTimer !== null) {
        clearTimeout(_poiPopupTimer);
        _poiPopupTimer = null;
    }
    if (_poiPopupMoveHandler) {
        map.off("moveend", _poiPopupMoveHandler);
        _poiPopupMoveHandler = null;
    }
}

// highlightPoi, single POI highlight. Hands off to highlightPoiSet
// (which does the pan/zoom + persistent ring + chip), then opens the
// marker's popup once the camera settles so the rider gets the info
// card immediately.
function highlightPoi(p) {
    if (!p || typeof p.lng !== "number" || typeof p.lat !== "number") return;

    const label = p.name || (POI_TYPE_META_LABEL[p.type] || "Place");
    highlightPoiSet([p], label);
    // Record the share descriptor. highlightPoiSet routes through
    // clearRouteTrailHighlight (not clearPoiHighlight), so it never
    // clobbers this; set it after for clarity. See _poiHighlightRef.
    _poiHighlightRef = p.uid || null;

    // Defer popup until the flyTo finishes so the popup positions
    // correctly relative to its new screen position. One-shot with a
    // safety timeout in case moveend doesn't fire; opening via either
    // path disarms the other (see _cancelPendingPoiPopup).
    const marker = findPoiMarker(p);
    if (marker && typeof marker.getPopup === "function") {
        const popup = marker.getPopup();
        if (popup) {
            const openOnce = () => {
                _cancelPendingPoiPopup();
                if (!popup.isOpen()) marker.togglePopup();
            };
            _poiPopupMoveHandler = openOnce;
            map.on("moveend", openOnce);
            _poiPopupTimer = setTimeout(openOnce, 1200);
        }
    }
}

// highlightPoiGroup, for a group of same-type, same-name POIs
// (e.g. all 5 unnamed toilets on the map). Hands off to
// highlightPoiSet which fits bounds + draws a persistent ring on
// each member. No popups (would be visually noisy with multiple
// overlapping cards); the rider can tap an individual marker for
// details, or read the chip ("Toilets (× 3)") for the count.
function highlightPoiGroup(group) {
    if (!group || !group.members || !group.members.length) return;
    // "Toilets (× 4)" rather than "Toilets × 4", the parenthetical
    // count reads as "Toilets, 4 of them" rather than "Toilets
    // multiplied by 4". The earlier "(All)" suffix on category
    // aggregates is gone, the count IS the aggregate signal, no
    // need to also flag the row as "All".
    const label = `${group.name} (× ${group.count})`;
    highlightPoiSet(group.members, label);
    // Record the share descriptor (the group's `group:`/`category:` uid).
    _poiHighlightRef = group.uid || null;
}

// Parse a POI share descriptor (the Finder row uid carried in a share
// link's `/p/<ref>` segment) into its shape. Returns one of:
//   { mode: "single",   uid }: a single POI, matched by uid
//   { mode: "group",    type, name }: all POIs of a (type, name)
//   { mode: "category", type }: all POIs of a type
// or null if the ref is malformed. The `group:` form splits on the FIRST
// colon after the prefix only, so a POI name containing ":" survives.
function _parsePoiRef(ref) {
    if (typeof ref !== "string" || !ref) return null;
    if (ref.startsWith("poi:")) {
        return { mode: "single", uid: ref };
    }
    if (ref.startsWith("group:")) {
        const rest = ref.slice("group:".length);
        const sep = rest.indexOf(":");
        if (sep < 0) return null;
        return { mode: "group", type: rest.slice(0, sep), name: rest.slice(sep + 1) };
    }
    if (ref.startsWith("category:")) {
        const type = ref.slice("category:".length);
        if (!type) return null;
        return { mode: "category", type };
    }
    return null;
}

// Re-create a POI highlight from a share descriptor, resolving it
// against the LIVE poiIndex (so a rebuilt map highlights its current
// members). Best-effort: silently no-ops if nothing matches, a stale
// link to a since-removed POI just restores the view without a ring.
// Mirrors the route/trail stale-link handling in applyPendingShareHighlight.
function highlightPoiByRef(ref) {
    const parsed = _parsePoiRef(ref);
    if (!parsed) return;
    if (parsed.mode === "single") {
        const entry = poiIndex.find((p) => p.uid === parsed.uid);
        if (entry) highlightPoi(entry);
        return;
    }
    let members;
    let name;
    if (parsed.mode === "group") {
        members = poiIndex.filter((p) => p.type === parsed.type && p.name === parsed.name);
        name = parsed.name;
    } else { // category
        members = poiIndex.filter((p) => p.type === parsed.type);
        name = POI_TYPE_FALLBACK_NAME[parsed.type] || parsed.type;
    }
    if (!members.length) return;
    if (members.length === 1) {
        // The set collapsed to one (data changed since the link was made,
        // or the category now has a lone member), highlight it as a
        // single so the rider still gets the popup + a clean uid ref.
        highlightPoi(members[0]);
        return;
    }
    highlightPoiGroup({
        isGroup: true,
        uid: ref,
        type: parsed.type,
        name,
        count: members.length,
        members,
    });
}

// Find the on-map maplibregl.Marker that corresponds to a poiIndex
// entry. Compares geographic coords (6-decimal precision = ~11cm,
// well below any meaningful POI placement). Returns null if no
// matching marker is currently in the marker pool, happens when the
// POI's layer toggle is off (the marker objects exist but aren't
// .addTo()'d to the map). The popup-open path tolerates null.
function findPoiMarker(p) {
    const arrays = {
        "trail_marker":   trailMarkerMarkers,
        "parking":        parkingMarkers,
        "trailhead":      trailheadMarkers,
        "hub":            hubMarkers,
        "feature":        featureMarkers,
        "toilet":         toiletMarkers,
        "drinking_water": drinkingWaterMarkers,
        "event":          eventPoiMarkers,
    };
    const list = arrays[p.type];
    if (!list) return null;
    const lngEq = (a, b) => Math.abs(a - b) < 1e-6;
    for (const m of list) {
        if (typeof m.getLngLat !== "function") continue;
        const ll = m.getLngLat();
        if (lngEq(ll.lng, p.lng) && lngEq(ll.lat, p.lat)) return m;
    }
    return null;
}

// POI persistent-highlight system.
//
// When the rider taps a POI search result (single OR group), the
// matching POIs get a persistent ring drawn around them on the map.
// The ring stays put while the rider pans and zooms, so they can
// navigate toward whichever one is most relevant. Cleared explicitly
// via the highlight chip's X, the Esc key, or by triggering a new
// highlight (which replaces the set).
//
// No animation, no map dim, the rings are static decorations that
// say "here are your matches, pick the one you want." For single-POI
// highlights we ALSO open the marker's popup so the rider gets the
// info card immediately. For group highlights we skip the popup
// (would be visually noisy with N overlapping cards).
//
// Implementation: one MapLibre GeoJSON source + two stacked circle
// layers (dark stroke outside, bright stroke inside, sandwich
// pattern visible on any basemap). setData fires once per
// highlight change; no per-frame updates → no MapLibre render
// re-entrancy issue.

let _highlightedPois = [];                       // module-scope state
// Serializable descriptor of the *current* POI highlight, mirroring the
// Finder row the rider tapped: a single POI's `poi:<type>:<lng>,<lat>`
// uid, a name-group's `group:<type>:<name>` uid, or a category row's
// `category:<type>` uid. buildShareUrl() serializes this so a POI
// highlight round-trips through a share link the way a route/trail does
// via `highlight`. Set by highlightPoi / highlightPoiGroup, nulled by
// clearPoiHighlight. The single-highlight invariant (see commit history)
// keeps this and `highlight` mutually exclusive: at most one is non-null.
let _poiHighlightRef = null;
const POI_HIGHLIGHT_SOURCE = "poi-highlight-source";
const POI_HIGHLIGHT_OUTER  = "poi-highlight-outer";
const POI_HIGHLIGHT_INNER  = "poi-highlight-inner";
const POI_HIGHLIGHT_RADIUS = 18;                 // pixels, fixed at all zooms

// Hybrid search-scope policy:
//   - Proximity filter (automatic, per-type radius) → POI dropped
//     from search index. If the curator's auto-filter says it's
//     not relevant to any visible trail, the rider shouldn't be
//     surprised by it surfacing in search.
//   - Options toggle off (explicit rider choice) → POI STILL in
//     search index. The rider knows the category exists; this
//     gives them a way to find a specific item without re-enabling
//     the entire category. Selecting a toggle-hidden result
//     force-mounts the type's markers; clearing the highlight
//     rolls them back to hidden. (No chip note, riders figure
//     out the "marker disappears on dismiss" behavior by trying.)
//
// Marker-mount detection (used by pruneInvisibleHighlights and
// other paths) reads MapLibre's internal Marker._map reference,
// set by addTo(map) and nulled by remove(). Private field but
// stable, MapLibre's own code reads it.
function _isPoiCurrentlyVisible(p) {
    const m = findPoiMarker(p);
    return !!(m && m._map);
}

// POI types whose markers can be hidden by the proximity filter.
// Same set used by updateMarkerProximity(), kept here for the
// search-scope check.
const _PROXIMITY_TYPES = new Set([
    "trail_marker", "feature", "toilet", "drinking_water",
]);

function _proximityThresholdForType(type) {
    if (type === "trail_marker" || type === "feature") {
        return POI_PROXIMITY_METERS;
    }
    if (type === "toilet" || type === "drinking_water") {
        return POI_AMENITY_PROXIMITY_METERS;
    }
    return Infinity;
}

// True iff the POI is within the type's proximity threshold of any
// currently-visible trail. Toggle state is NOT consulted, toggle-
// hidden POIs stay searchable (force-mounted on selection).
function _isPoiInSearchScope(p) {
    if (!_PROXIMITY_TYPES.has(p.type)) return true;
    return distanceToVisibleTrails(p.lng, p.lat)
        <= _proximityThresholdForType(p.type);
}

// Cached in-scope POI list for the Finder. The scope test walks every
// visible feature's every segment per POI (distanceToVisibleTrails),
// and rebuildFinderList used to recompute it on EVERY keystroke and
// chip tap, millions of point-to-segment calls per input event on a
// large map, tens to hundreds of ms of typing jank on a phone. The
// set only actually changes when the visible-route set does, so
// compute lazily and invalidate on visibility change / POI reload
// (see invalidateFinderPoiScope callers).
let _finderPoiScopeCache = null;

function invalidateFinderPoiScope() {
    _finderPoiScopeCache = null;
}

function finderPoisInScope() {
    if (!_finderPoiScopeCache) {
        _finderPoiScopeCache = poiIndex.filter(_isPoiInSearchScope);
    }
    return _finderPoiScopeCache;
}

function _markerArrayForType(type) {
    switch (type) {
        case "parking":         return parkingMarkers;
        case "trailhead":       return trailheadMarkers;
        case "hub":             return hubMarkers;
        case "toilet":          return toiletMarkers;
        case "drinking_water":  return drinkingWaterMarkers;
        case "trail_marker":    return trailMarkerMarkers;
        case "feature":         return featureMarkers;
        case "event":           return eventPoiMarkers;
    }
    return null;
}

function _lsKeyForType(type) {
    switch (type) {
        case "parking":         return "mtb.poi.parking";
        case "trailhead":       return "mtb.poi.trailheads";
        case "hub":             return "mtb.poi.hubs";
        case "toilet":          return "mtb.poi.toilets";
        case "drinking_water":  return "mtb.poi.drinking_water";
        case "trail_marker":    return "mtb.poi.markers";
        case "feature":         return "mtb.poi.features";
    }
    return null;
}

// POI type → name used in `default_visible` config list. Lets us
// resolve "what's the per-map default for this category?" from a
// runtime POI type. Keep these two maps in lockstep with each other
// AND with the addXxxMarkers() callers in loadPOIs (the boot path
// that decides the initial mount state).
function _defaultVisibleNameForType(type) {
    switch (type) {
        case "parking":         return "parking";
        case "trailhead":       return "trailheads";
        case "hub":             return "hubs";
        case "toilet":          return "toilets";
        case "drinking_water":  return "drinking_water";
        case "trail_marker":    return "trail_markers";
        case "feature":         return "features";
    }
    return null;
}

// Force-show machinery: when a highlight lands on POIs of a type
// whose Options toggle is OFF, mount that type's markers
// temporarily so the rings have content underneath. Toggle state
// in localStorage is NOT touched. On highlight clear (or
// replacement), the markers come back off.
//
// Simpler than yesterday's draft: only handles the toggle-off
// case. Proximity-hidden POIs are excluded from search (and
// therefore from highlights) before they get here, so there's no
// proximity-force-mount path to worry about.
let _forcedPoiTypes = new Set();

function _forcePoiType(type) {
    if (_forcedPoiTypes.has(type)) return;
    const lsKey = _lsKeyForType(type);
    if (!lsKey) return;
    // Toggle ON → markers already mounted (modulo proximity, which
    // the search-scope filter has already enforced before we got
    // here). No force-mount needed.
    //
    // The fallback for LS.get must match what loadPOIs decided at
    // boot, otherwise we mis-classify "rider hasn't toggled yet"
    // states. Specifically: when default_visible omits this type,
    // loadPOIs called addXxxMarkers(false), markers exist in the
    // array but aren't on the map. If we used `true` as the
    // fallback here, we'd think "toggle is on" and skip force-mount,
    // leaving the search highlight ringing empty space. Using
    // isDefaultVisible(name) keeps both code paths in agreement.
    const dvName = _defaultVisibleNameForType(type);
    const fallbackOn = dvName ? isDefaultVisible(dvName) : true;
    if (LS.get(lsKey, fallbackOn)) return;
    const arr = _markerArrayForType(type);
    if (!arr) return;
    // For proximity-filtered types, mount only the proximity-IN
    // markers, same set updateMarkerProximity() would mount if the
    // toggle were on. Force-mounting EVERY marker including
    // proximity-OUT ones would leave "ghost" markers without rings,
    // visually inconsistent with what the rider saw in search.
    if (_PROXIMITY_TYPES.has(type)) {
        const threshold = _proximityThresholdForType(type);
        for (const m of arr) {
            const { lng, lat } = m.getLngLat();
            if (distanceToVisibleTrails(lng, lat) <= threshold) {
                m.addTo(map);
            }
        }
    } else {
        for (const m of arr) m.addTo(map);
    }
    _forcedPoiTypes.add(type);
    invalidateObstaclesCache();
    if (map && map.getSource("trail-decorations")) {
        updateDecorationsSource();
    }
}

function _unforcePoiType(type) {
    if (!_forcedPoiTypes.has(type)) return;
    _forcedPoiTypes.delete(type);
    const arr = _markerArrayForType(type);
    if (!arr) return;
    // We only force when the toggle is off, so unforce always means
    // unmount everything. (If the rider flipped the toggle ON during
    // the highlight, the toggle handler removed us from the set
    // before we got here, so this branch wouldn't run.)
    for (const m of arr) m.remove();
    invalidateObstaclesCache();
    if (map && map.getSource("trail-decorations")) {
        updateDecorationsSource();
    }
}

function _reconcileForcedTypes(newTypes) {
    // Snapshot, _unforcePoiType mutates the set.
    for (const t of Array.from(_forcedPoiTypes)) {
        if (!newTypes.has(t)) _unforcePoiType(t);
    }
    for (const t of newTypes) _forcePoiType(t);
}

// Called by every POI toggle handler AFTER its own marker logic
// has run. Three jobs:
//   (a) drop the type from _forcedPoiTypes (rider took manual
//       control, so we relinquish ownership);
//   (b) if a highlight is currently active, re-reconcile against
//       its types, covers "rider toggled off the type that's
//       highlighted" by re-mounting via force-show, and "rider
//       toggled on a force-mounted type" by no-op'ing (markers are
//       now mounted by the toggle handler itself).
function _onPoiToggleChange(type) {
    _forcedPoiTypes.delete(type);
    if (_highlightedPois.length > 0) {
        const types = new Set(_highlightedPois.map((p) => p.type));
        _reconcileForcedTypes(types);
    }
}

// Drop POIs from the active highlight set if their markers have
// been hidden since the highlight was set. Called whenever something
// changes marker mount state (POI toggle flipped, route visibility
// changed, etc.). If every highlighted POI is now hidden, clear the
// highlight entirely; if some remain visible, re-render with just
// those (chip label intentionally not updated, the rider's mental
// model is "I highlighted Toilets × 4", and 3-of-4 still being on
// the map is a degraded but coherent state).
function pruneInvisibleHighlights() {
    if (_highlightedPois.length === 0) return;
    const stillVisible = _highlightedPois.filter(_isPoiCurrentlyVisible);
    if (stillVisible.length === _highlightedPois.length) return;
    if (stillVisible.length === 0) {
        clearPoiHighlight();  // also hides the chip
    } else {
        _highlightedPois = stillVisible;
        setPoiHighlightData(_highlightedPois);
    }
}

function ensurePoiHighlightLayers() {
    if (!map.getSource(POI_HIGHLIGHT_SOURCE)) {
        map.addSource(POI_HIGHLIGHT_SOURCE, {
            type: "geojson",
            data: { type: "FeatureCollection", features: [] },
        });
    }
    // Outer dark stroke first so the inner bright stroke sits on
    // top. Translucent dark for any-light-background contrast.
    if (!map.getLayer(POI_HIGHLIGHT_OUTER)) {
        map.addLayer({
            id: POI_HIGHLIGHT_OUTER,
            type: "circle",
            source: POI_HIGHLIGHT_SOURCE,
            paint: {
                "circle-radius": POI_HIGHLIGHT_RADIUS,
                "circle-color": "transparent",
                "circle-stroke-color": "rgba(0, 0, 0, 0.7)",
                "circle-stroke-width": 5,
            },
        });
    }
    if (!map.getLayer(POI_HIGHLIGHT_INNER)) {
        map.addLayer({
            id: POI_HIGHLIGHT_INNER,
            type: "circle",
            source: POI_HIGHLIGHT_SOURCE,
            paint: {
                "circle-radius": POI_HIGHLIGHT_RADIUS,
                "circle-color": "transparent",
                "circle-stroke-color": "#FFEC00",  // highlighter yellow
                "circle-stroke-width": 2.5,
            },
        });
    }
}

function setPoiHighlightData(pois) {
    ensurePoiHighlightLayers();
    const src = map.getSource(POI_HIGHLIGHT_SOURCE);
    if (!src) return;
    src.setData({
        type: "FeatureCollection",
        features: pois.map((p) => ({
            type: "Feature",
            properties: {},
            geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        })),
    });
}

// Set the persistent POI highlight to a list of POIs. Pans/zooms to
// fit them all, draws the rings, shows the highlight chip with the
// label and count.
function highlightPoiSet(pois, label) {
    if (!pois || !pois.length) return;

    // Disarm the previous highlight's deferred popup (moveend +
    // safety timer) BEFORE this set replaces it, otherwise the old
    // timer fires mid/post-transition and reopens the old popup next
    // to the new highlight.
    _cancelPendingPoiPopup();
    // Close any popup left open on the *previous* highlighted POIs
    // before the set is overwritten, otherwise a stale info card from
    // the prior search lingers beside the new one (the chip only ever
    // shows the latest). Must run before _highlightedPois is reassigned.
    closeHighlightedPoiPopups();
    // Drop any route/trail highlight so the POI rings don't sit on top
    // of a stale ribbon, then resync the spotlight dim / dimmed labels /
    // narrowed decorations that key off `highlight` (POIs never dim).
    clearRouteTrailHighlight();
    applyDimState();

    _highlightedPois = pois.slice();
    setPoiHighlightData(_highlightedPois);

    // Force-mount any toggle-hidden types in the highlight so the
    // rings have markers underneath. Proximity-hidden POIs aren't
    // reachable here (search-scope filter excludes them), so this
    // only fires when the rider deliberately turned a category off.
    const newTypes = new Set(_highlightedPois.map((p) => p.type));
    _reconcileForcedTypes(newTypes);

    // Fit map. Single → flyTo with zoom-up. Multiple → fitBounds.
    if (_highlightedPois.length === 1) {
        const p = _highlightedPois[0];
        map.flyTo({
            center: [p.lng, p.lat],
            zoom: Math.max(map.getZoom(), 15),
            duration: 700,
        });
    } else {
        let minLng = Infinity, maxLng = -Infinity;
        let minLat = Infinity, maxLat = -Infinity;
        for (const p of _highlightedPois) {
            if (p.lng < minLng) minLng = p.lng;
            if (p.lng > maxLng) maxLng = p.lng;
            if (p.lat < minLat) minLat = p.lat;
            if (p.lat > maxLat) maxLat = p.lat;
        }
        // Degenerate (all same coords), fall through to single-pan.
        if (minLng === maxLng && minLat === maxLat) {
            map.flyTo({
                center: [minLng, minLat],
                zoom: Math.max(map.getZoom(), 15),
                duration: 700,
            });
        } else {
            map.fitBounds(
                [[minLng, minLat], [maxLng, maxLat]],
                { padding: 80, maxZoom: 16, duration: 700 },
            );
        }
    }

    // Highlight chip, re-uses the existing chip element. Yellow
    // swatch matches the inner ring color so the visual link
    // between chip and on-map highlights is obvious.
    showHighlightChip({
        label,
        color: "#FFEC00",
        stats: "",
        note: "",
    });
}

// Close any popup left open on the currently-highlighted POIs. Only
// single-POI highlights open a popup (highlightPoi); groups don't, so
// for those this loop finds nothing open. Reuses findPoiMarker rather
// than tracking popup state separately. Call BEFORE _highlightedPois is
// cleared or reassigned, while the markers are still resolvable.
function closeHighlightedPoiPopups() {
    for (const p of _highlightedPois) {
        const m = findPoiMarker(p);
        const popup = m && typeof m.getPopup === "function" && m.getPopup();
        if (popup && popup.isOpen()) popup.remove();
    }
}

function clearPoiHighlight() {
    // Drop the share descriptor unconditionally, clearing the POI
    // highlight always invalidates it, and the early-return below only
    // skips marker teardown when nothing's mounted (ref already null then).
    _poiHighlightRef = null;
    if (_highlightedPois.length === 0 && _forcedPoiTypes.size === 0) return;
    // A stale info card from the prior search must not outlive its
    // highlight, disarm the deferred popup (or it would re-open one
    // up to 1.2 s AFTER the clear) and close any open card before we
    // drop the set.
    _cancelPendingPoiPopup();
    closeHighlightedPoiPopups();
    _highlightedPois = [];
    const src = map.getSource(POI_HIGHLIGHT_SOURCE);
    if (src) {
        src.setData({ type: "FeatureCollection", features: [] });
    }
    // Roll back any types we'd force-mounted. Snapshot, _unforcePoiType
    // mutates the set as we iterate.
    for (const t of Array.from(_forcedPoiTypes)) {
        _unforcePoiType(t);
    }
    hideHighlightChip();
}

// Clear only the route/trail line highlight (its layer filters + the
// `highlight` state var). Split out from clearHighlight() so the POI
// path can drop a stale route/trail highlight without tearing down and
// re-mounting POI markers, highlightPoiSet keeps its own forced-type
// reconcile for flicker-free POI→POI switches.
function clearRouteTrailHighlight() {
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
    // Panel active-row mark follows the highlight lifecycle. Synced
    // here (not in clearHighlight) so POI highlights, which route
    // through this teardown, not clearHighlight, also unmark the row.
    syncRoutePanelActiveRow();
}

function clearHighlight() {
    clearRouteTrailHighlight();
    // POI rings + chip, clearPoiHighlight is a no-op if nothing's
    // currently highlighted, and it tears down the chip itself, so
    // calling it here unifies the chip's clear path for both
    // route/trail highlights and POI highlights.
    clearPoiHighlight();
    hideHighlightChip();

    // Dim follows highlight lifecycle, tear it down here.
    applyDimState();
}

function fitToRouteOrTrail({ routeId, trailName }) {
    if (!routesData) return;
    let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
    let hasCoords = false;

    for (const f of routesData.features) {
        if (!featurePassesModeFilter(f)) continue;
        if (f.properties.isStub === true) continue;
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

function showHighlightChip({ label, color, stats, note, route }) {
    const chip = document.getElementById("highlight-chip");
    if (!chip) return;
    const swatch = chip.querySelector(".highlight-chip-swatch");
    const labelEl = chip.querySelector(".highlight-chip-label");
    const statsEl = chip.querySelector(".highlight-chip-stats");
    const noteEl = chip.querySelector(".highlight-chip-note");
    // Swatch: rebuilt on every call. `route` (optional) carries the
    // dash fields (color / dashed / dashCap / dashColors); a dashed
    // route swaps the flat dot for the same mini-SVG ribbon the key
    // and finder rows use (routeSwatchEl), so the chip can't
    // misrepresent a dashed line as solid. Everything else (solid
    // routes, trails, POIs) gets a plain dot in `color`. Rebuilding
    // rather than mutating means a trail/POI highlight following a
    // dashed route gets its dot back without SVG-vs-span special
    // cases.
    if (swatch) {
        let next;
        if (route && isDashed(route)) {
            next = routeSwatchEl(route, "highlight-chip-swatch");
        } else {
            next = document.createElement("span");
            next.className = "highlight-chip-swatch";
            next.setAttribute("aria-hidden", "true");
            next.style.background = color;
        }
        swatch.replaceWith(next);
    }
    if (labelEl) labelEl.textContent = label;
    // stats is the pre-formatted "8.2 mi · 410 ft ↑" text from
    // routeStatsText(), empty string or missing means hide the span
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
    // note is an optional second-line message under the label.
    // Currently unused; kept in the API as a hook for future
    // highlight types that might want a second-line annotation.
    if (noteEl) {
        if (note) {
            noteEl.textContent = note;
            noteEl.classList.remove("hidden");
        } else {
            noteEl.textContent = "";
            noteEl.classList.add("hidden");
        }
    }
    chip.classList.remove("hidden");
}

function hideHighlightChip() {
    const chip = document.getElementById("highlight-chip");
    if (chip) chip.classList.add("hidden");
}

// ============================================================
// Routes panel, key card + search entry, bottom-right
// ============================================================
// The map's ONE routes surface, progressive disclosure over the
// rider's intent ladder: glance ("which color is which?", the key
// rows), browse ("what are my options?", same rows, tap to
// highlight), search ("take me to X", the Search row pinned at the
// card's bottom opens the search overlay, which is this panel's
// expanded state, not a separate destination; it absorbed the old
// Search FAB). A key row pairs a route's color with its name
// (+ stats); previously that mapping was only discoverable by
// tapping a route on the map.
//
// The panel is always present, it's the map's key. Only two forms
// exist, resolved by rebuildRoutePanel + initRoutePanel:
//   key card: header + key rows + Search button. The default.
//                  A zero-row map (only reachable via an empty season
//                  bucket) still renders the card: header + Search
//                  button, no rows.
//   .is-collapsed: a compact round list-icon button, FAB-styled to
//                  match the top-right stack (docked alternative to
//                  the card; rider-toggled, persisted per-map as
//                  LS "mtb.routePanelExpanded" = true | false).
// The `hidden` class in the markup only suppresses an empty card
// during the boot data load; rebuildRoutePanel clears it once rows
// (or an honest empty card) are ready.
//
// Boot docked state: the rider's stored boolean wins; otherwise a
// viewport-aware default (routePanelDefaultCollapsed), expanded when
// the card would fit within PANEL_MAX_VIEWPORT_FRACTION of the
// viewport, chip when it would swamp the screen (RAMBA's 11 rows on a
// phone). Computed once at boot; resize/rotate never yanks the card
// away, and the rider's explicit toggle always wins afterward. The
// find state is never persisted, no map should boot into an open
// search.
const PANEL_MAX_VIEWPORT_FRACTION = 1 / 3;

// The rows the key would show right now: routes visible under the
// rider's current season/emergency toggles (visibleRoutes, same
// gate the map itself renders by), minus non-featured routes on
// event maps (matching the label restriction in
// labelsVisibleForRoute). routeIndex is already sorted by name.
function panelListableRoutes() {
    return routeIndex.filter((r) => {
        if (!visibleRoutes.has(r.id)) return false;
        if (CONFIG.eventModeActive && !r.featured) return false;
        return true;
    });
}

// (Re)build the key rows. Called from applyVisibilityChange() so
// season/emergency toggles update the key in the same breath as the
// map, a key row for a hidden route (or a missing row for a shown
// one) would be worse than no key. Safe to call before initRoutePanel
// has wired the expand/collapse buttons: it only touches rows.
function rebuildRoutePanel() {
    const wrap = document.getElementById("route-panel");
    const list = document.getElementById("route-panel-list");
    if (!wrap || !list) return;

    // The panel is the map's key, always shown. The `hidden` class in
    // the markup only suppresses an empty card during the boot data
    // load; clear it now that we have real rows (or an honest empty
    // card whose Search button still works). Every map is built from a
    // geometry source (the validator requires one), so zero listable
    // rows only happens transiently via an empty season bucket, the
    // card then renders header + Search button with no rows, and the
    // next season flip repopulates it.
    wrap.classList.remove("hidden");

    const rows = panelListableRoutes();
    list.textContent = "";
    for (const r of rows) {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "route-panel-row";
        btn.dataset.routeId = r.id;

        // Dash-aware swatch (shared with finder rows) so a dotted or
        // two-color route reads as dashed in the key, not as a solid
        // bar.
        btn.appendChild(routeSwatchEl(r, "route-panel-swatch"));

        const name = document.createElement("span");
        name.className = "route-panel-row-name";
        name.textContent = r.name;
        btn.appendChild(name);

        // Same gating as finder rows: routeStatsText returns "" when
        // neither distance nor elevation is enabled/available, and we
        // omit the span entirely rather than render an empty one.
        const stats = routeStatsText(r);
        if (stats) {
            const statsEl = document.createElement("span");
            statsEl.className = "route-panel-row-stats";
            statsEl.textContent = stats;
            btn.appendChild(statsEl);
        }

        // Tap toggles: highlight this route, or clear if it's already
        // the highlighted one, so the key row behaves like the
        // control it looks like, rather than requiring the rider to
        // find the chip's × to undo what the key did.
        btn.addEventListener("click", () => {
            if (highlight && highlight.kind === "route"
                && String(highlight.key) === String(r.id)) {
                clearHighlight();
            } else {
                highlightRoute(r.id);
            }
        });

        li.appendChild(btn);
        list.appendChild(li);
    }
    syncRoutePanelActiveRow();
}

// Mark the currently-highlighted route's key row (accent stripe via
// .is-active + aria-current) and clear every other row's mark. Reads
// the global highlight state rather than taking a parameter so every
// call site, highlightRoute, highlightTrail, clearRouteTrailHighlight,
// rebuildRoutePanel, stays a bare one-liner that can't pass stale
// data. String() both sides: route ids arrive as strings from dataset
// but may be set as numbers by map-tap handlers.
function syncRoutePanelActiveRow() {
    const list = document.getElementById("route-panel-list");
    if (!list) return;
    const activeId = (highlight && highlight.kind === "route")
        ? String(highlight.key) : null;
    for (const btn of list.querySelectorAll(".route-panel-row")) {
        const on = activeId !== null && btn.dataset.routeId === activeId;
        btn.classList.toggle("is-active", on);
        if (on) btn.setAttribute("aria-current", "true");
        else btn.removeAttribute("aria-current");
    }
}

// Wire expand/collapse + pick the boot state. Runs once from init()
// after setupFloatingChrome() so visibleRoutes is populated (the
// boot-time applyVisibilityChange has already run rebuildRoutePanel
// by then; this just settles the collapsed/expanded presentation).
function initRoutePanel() {
    const wrap = document.getElementById("route-panel");
    const chip = document.getElementById("route-panel-chip");
    const collapseBtn = document.getElementById("route-panel-collapse");
    if (!wrap || !chip || !collapseBtn) return;

    const applyCollapsed = (collapsed) => {
        wrap.classList.toggle("is-collapsed", collapsed);
        // aria-expanded lives on the chip (the control a screen-reader
        // user lands on when the card is collapsed); the collapse
        // button inside the card is labeled by its aria-label.
        chip.setAttribute("aria-expanded", String(!collapsed));
        // Tell the first-visit FAB-label cue (setupFabLabels) the chip
        // just appeared: if the cue is still live it late-mounts the
        // "Route key" label, so a rider who collapses the panel during
        // the cue isn't left with the one unnamed control. The boot-time
        // call fires before setupFabLabels attaches its listener,
        // harmless (boot-collapsed labeling is handled there directly).
        if (collapsed) {
            wrap.dispatchEvent(new CustomEvent("route-panel-collapsed"));
        }
    };

    // Boot state: the rider's stored boolean wins; otherwise the
    // viewport-aware default (routePanelDefaultCollapsed). Measured
    // here, BEFORE the first applyCollapsed, so the card is rendered
    // expanded and its rows are measurable. Only a boolean is ever
    // stored (true = key card, false = chip); the find state is never
    // persisted.
    const stored = LS.get("mtb.routePanelExpanded", null);
    const collapsed = stored === true ? false
        : stored === false ? true
        : routePanelDefaultCollapsed();
    applyCollapsed(collapsed);

    chip.addEventListener("click", () => {
        applyCollapsed(false);
        LS.set("mtb.routePanelExpanded", true);
    });
    collapseBtn.addEventListener("click", () => {
        applyCollapsed(true);
        LS.set("mtb.routePanelExpanded", false);
    });
}

// Viewport-aware boot default when the rider has no stored preference.
// Expanded costs a card; too tall a card swamps a small screen (RAMBA's
// 11 rows on a phone). Measure the real DOM, robust to font, color
// scheme, and stat availability, and expand only when the card would
// fit within PANEL_MAX_VIEWPORT_FRACTION of the viewport. The list's
// 40vh scroll cap still bounds a rider who later expands a big list;
// this only decides the boot default. Returns true = start collapsed.
function routePanelDefaultCollapsed() {
    const card = document.getElementById("route-panel-card");
    const list = document.getElementById("route-panel-list");
    if (!card || !list) return false;

    const rowCount = list.children.length;
    // A row's real rendered height (~26px; varies with font/scheme/
    // stats). Fall back to a typical value when there are no rows, a
    // zero-row card is tiny and always fits.
    const firstRow = list.firstElementChild;
    const rowHeight = firstRow ? firstRow.offsetHeight : 26;
    // Card chrome = header + Search button + padding (~70px), derived
    // from the rendered card so font/padding changes can't drift it.
    // The list may be scroll-capped at 40vh, but subtracting its
    // measured height leaves the true chrome either way; the estimate
    // below then re-adds the FULL (uncapped) row stack.
    const chrome = card.offsetHeight - list.offsetHeight;
    const estimated = chrome + rowCount * rowHeight;
    return estimated > PANEL_MAX_VIEWPORT_FRACTION * window.innerHeight;
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
            // Event-mode flag, the routes panel keys featured routes
            // only on event maps (the muted background network isn't a
            // route a rider chooses between).
            featured: !!info.featured,
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
            // Dash fields, copied verbatim so the key/finder swatches
            // can reuse the on-map dash helpers (isDashed / getDashPattern
            // / getDashCap / getDashColors) and render dashed routes as
            // dashed ribbons rather than flat color bars. dashed is the
            // pattern array (or absent); dashColors[0] is the dash color
            // (already folded into `color` via effectiveRouteColor).
            dashed: Array.isArray(info.dashed) ? info.dashed : null,
            dashCap: info.dashCap || null,
            dashColors: Array.isArray(info.dashColors) ? info.dashColors : null,
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

// Build the POI search index from poisData (loaded by loadPOIs at
// boot). One entry per POI feature; each entry carries the
// information the search overlay needs to render a row + the
// coordinates needed by highlightPoi(). Unnamed POIs get a
// synthesized fallback name (e.g. "Marker EPIC-3") so they're still
// findable.
function buildPoiIndex() {
    poiIndex = [];
    if (!poisData || !poisData.features) return;
    for (const f of poisData.features) {
        const props = f.properties || {};
        const coords = f.geometry && f.geometry.coordinates;
        if (!coords || coords.length < 2) continue;
        const type = props.poi_type;
        if (!type) continue;

        // Display name. POIs vary in what they have:
        //   - parking / trailhead: name from config
        //   - feature: name from OSM (sometimes empty)
        //   - trail_marker: ref or name
        //   - toilet / drinking_water: name from OSM (often empty)
        // For empty names we synthesize something searchable. Track
        // whether the name was synthesized so groupPoisForFinder can
        // suppress the "unnamed cluster" row when a category-group
        // covers it (otherwise the rider sees two same-labeled
        // "Toilets (× N)" rows with no way to tell them apart).
        let name = props.name || "";
        let synthesized = false;
        if (!name && type === "trail_marker") {
            name = props.ref ? `Marker ${props.ref}` : "Trail Marker";
            if (!props.ref) synthesized = true;
        }
        if (!name) {
            name = POI_TYPE_FALLBACK_NAME[type] || type;
            synthesized = true;
        }

        poiIndex.push({
            uid: `poi:${type}:${coords[0].toFixed(6)},${coords[1].toFixed(6)}`,
            type,
            name,
            synthesized,
            lng: coords[0],
            lat: coords[1],
            ref: props.ref || "",
        });
    }
    poiIndex.sort((a, b) => a.name.localeCompare(b.name));
    // poiIndex was rebuilt from scratch, the Finder's cached in-scope
    // subset points at the old entries.
    invalidateFinderPoiScope();
}

// Display label for each POI type when the OSM feature has no name.
// Also used as the category-aggregate row name in the search overlay
// (when 2+ POIs of one type collapse into a single grouped row).
// Plural reads better there since the aggregate represents a set.
const POI_TYPE_FALLBACK_NAME = Object.freeze({
    "trail_marker":   "Trail Marker",
    "parking":        "Parking",
    "trailhead":      "Trailhead",
    "hub":            "Trail Hub",
    "feature":        "Feature",
    "toilet":         "Toilets",
    "drinking_water": "Drinking Water",
    "event":          "Event Markers",
});

const POI_TYPE_META_LABEL = Object.freeze({
    "trail_marker":   "trail marker",
    "parking":        "parking",
    "trailhead":      "trailhead",
    "hub":            "trail hub",
    "feature":        "feature",
    "toilet":         "toilets",
    "drinking_water": "drinking water",
    "event":          "event marker",
});

// ============================================================
// POI loading
// ============================================================
async function loadPOIs() {
    // pois.geojson is optional in spirit, if it fails, the map still
    // works without POI markers. Fall back to an empty collection and
    // toast a warning; downstream count-based gating (`hasTrailMarkers`,
    // `hasParking`, etc.) auto-hides the relevant toggle rows.
    try {
        const resp = await fetchWithTimeout("pois.geojson");
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
        }
        poisData = await resp.json();
    } catch (e) {
        console.warn("loadPOIs: pois.geojson failed to load:", e);
        showToast("POI data failed to load. Markers won't show.");
        poisData = { type: "FeatureCollection", features: [] };
    }

    // Count features by type in a single pass. Trail markers merge
    // guideposts + emergency access points into one category.
    const poiCounts = {
        [POI.TRAIL_MARKER]: 0,
        [POI.PARKING]: 0,
        [POI.TRAILHEAD]: 0,
        [POI.HUB]: 0,
        [POI.FEATURE]: 0,
        [POI.TOILET]: 0,
        [POI.DRINKING_WATER]: 0,
    };
    for (const f of poisData.features) {
        const t = f.properties.poi_type;
        if (t in poiCounts) poiCounts[t]++;
    }
    const tmCount = poiCounts[POI.TRAIL_MARKER];
    const pkCount = poiCounts[POI.PARKING];
    const thCount = poiCounts[POI.TRAILHEAD];
    const hbCount = poiCounts[POI.HUB];
    const ftCount = poiCounts[POI.FEATURE];
    const wcCount = poiCounts[POI.TOILET];
    const dwCount = poiCounts[POI.DRINKING_WATER];

    // Read persisted toggle state. Per-layer default-on/off is
    // driven by the per-map default_visible YAML list (see
    // isDefaultVisible), empty list means everything starts off
    // until the rider opts in via Options.
    const mkDefault = LS.get("mtb.poi.markers", isDefaultVisible("trail_markers"));
    const pkDefault = LS.get("mtb.poi.parking", isDefaultVisible("parking"));
    const thDefault = LS.get("mtb.poi.trailheads", isDefaultVisible("trailheads"));
    const hbDefault = LS.get("mtb.poi.hubs", isDefaultVisible("hubs"));
    const ftDefault = LS.get("mtb.poi.features", isDefaultVisible("features"));
    const wcDefault = LS.get("mtb.poi.toilets", isDefaultVisible("toilets"));
    const dwDefault = LS.get("mtb.poi.drinking_water", isDefaultVisible("drinking_water"));

    // Hide a toggle row in the Options overlay when its layer has no
    // data, keeps the rider from seeing a dead control.
    const hideToggleRow = (id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add("hidden");
    };

    // Trail markers, merged guideposts + emergency-access layer. The
    // toggle is shown whenever the layer has any data.
    if (CONFIG.showMarkers && tmCount > 0) {
        addTrailMarkers(mkDefault);
    } else {
        hideToggleRow("toggle-markers");
    }

    if (CONFIG.showParking && pkCount > 0) {
        addParkingMarkers(pkDefault);
    } else {
        hideToggleRow("toggle-parking");
    }

    if (CONFIG.showTrailheads && thCount > 0) {
        addTrailheadMarkers(thDefault);
    } else {
        hideToggleRow("toggle-trailheads");
    }

    if (CONFIG.showHubs && hbCount > 0) {
        addHubMarkers(hbDefault);
    } else {
        hideToggleRow("toggle-hubs");
    }

    // Toilets + drinking water, proximity-gated like Features. Set
    // aria-pressed from persisted state (used by updateMarkerProximity
    // when it filters), but the toggle ROW visibility is decided by
    // updatePoiToggleVisibility() on the first applyVisibilityChange()
    // pass after init, based on whether anything is in proximity range
    // (POI_AMENITY_PROXIMITY_METERS = 500 m, wider than features).
    if (CONFIG.showToilets && wcCount > 0) {
        addToiletMarkers(wcDefault);
        const wcBtn = document.getElementById("toggle-toilets");
        if (wcBtn) wcBtn.setAttribute("aria-pressed", wcDefault ? "true" : "false");
    }
    if (CONFIG.showDrinkingWater && dwCount > 0) {
        addDrinkingWaterMarkers(dwDefault);
        const dwBtn = document.getElementById("toggle-drinking-water");
        if (dwBtn) dwBtn.setAttribute("aria-pressed", dwDefault ? "true" : "false");
    }

    // Event POIs (event_mode.pois), always rendered, no rider toggle,
    // no proximity gate. Race-day fixtures (start / finish, aid
    // stations, support areas) are essential to the event map's
    // purpose; hiding them under any condition would defeat that.
    if (CONFIG.hasEventPois) {
        addEventPoiMarkers(true);
    }

    // Features + toilets + water are all gated by data-presence AND
    // proximity: a build can emit POIs that all sit beyond their
    // proximity threshold from the trail (Shelden's "Shelden Estate
    // Wall" / "Old Tennis Court" features are ~12 m and ~33 m off,
    // respectively; toilets are often at parking lots beyond the
    // trail polyline). Always create the markers so they can pop in
    // if a route change brings them into scope; updatePoiToggleVisibility
    // is the source of truth for whether each toggle row is shown.
    if (CONFIG.showFeatures && ftCount > 0) {
        addFeatureMarkers(ftDefault);
    }
    updatePoiToggleVisibility();
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
                            popupHtmlFn, popupMaxWidth, popupClass,
                            addToMap, targetArray }) {
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
            // focusAfterOpen: false, MapLibre's default is to move
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
                // Per-type accent strip on the popup's left edge,
                // color comes from the per-type CSS variable
                // (parking_color, trailhead_color). The class is
                // added to the popup wrapper (.maplibregl-popup);
                // see the popup-parking / popup-trailhead rules in
                // style.css. Must be passed at construction
                // (NOT via addClassName afterwards) because
                // MapLibre creates the container lazily on first
                // open, addClassName has no container to act on
                // yet at construction time.
                className: popupClass || "",
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

// Single helper covering the merged trail-marker POI category, OSM
// guideposts and emergency-access points now render with the same
// style. Shown/hidden together via the "Markers" toggle.
// Note on className strings below: each map marker carries TWO
// classes, the .poi-marker base (shared geometry, drop-shadow,
// font weight, etc.) plus a per-type modifier (.parking-marker,
// .toilet-marker, etc.) that sets the color triple and any per-
// type size overrides. All driven by --poi-marker-* CSS tokens at
// :root so the Options swatches stay in lockstep with the on-map
// markers, see "On-map POI markers" block in style.css.

function addTrailMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.TRAIL_MARKER,
        className: "poi-marker trail-marker",
        // Fall back to "#" when OSM carries neither ref nor name,
        // matches the Options-row swatch, preserves the marker's
        // physical footprint (empty string would collapse it via
        // min-width), and signals "guidepost / trail marker" to the
        // rider.
        labelFn: (p) => p.ref || p.name || "#",
        addToMap,
        targetArray: trailMarkerMarkers,
    });
}

function addParkingMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.PARKING,
        className: "poi-marker parking-marker",
        labelFn: () => "P",
        popupHtmlFn: (p, coords) => {
            let h = `<div class="popup-title">${escapeHtml(p.name || "Parking")}</div>`;
            h += directionsLink(coords, p.directions_url);
            return h;
        },
        popupMaxWidth: "220px",
        popupClass: "popup-parking",
        addToMap,
        targetArray: parkingMarkers,
    });
}

function addTrailheadMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.TRAILHEAD,
        className: "poi-marker trailhead-marker",
        labelFn: () => "TH",
        popupHtmlFn: (p, coords) => {
            let h = `<div class="popup-title">${escapeHtml(p.name || "Trailhead")}</div>`;
            h += directionsLink(coords, p.directions_url);
            return h;
        },
        popupMaxWidth: "220px",
        popupClass: "popup-trailhead",
        addToMap,
        targetArray: trailheadMarkers,
    });
}

// Trail hubs, named on-trail intersections. Inline-SVG hexagonal "H"
// chip with the curator-supplied name as a permanent inline label
// below the chip (same pattern as features). No popup: the name IS
// the entire signal, there's nothing useful to gate behind a tap
// (no directions link because riders can't drive to a hub; no
// facility metadata). The hex silhouette + inline name keeps hubs
// visually distinct from the square TH (Trailhead) and P (Parking)
// chips at a glance.
//
// SVG (not CSS clip-path) for two reasons: a regular flat-top hex's
// 2:√3 width:height ratio doesn't fit a square clip-path without
// stretching, and SVG `stroke` gives a crisp 2 px border that
// follows the polygon edges (CSS `border` doesn't follow clip-path
// silhouettes). See the .hub-marker-* CSS rules in style.css.
const HUB_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true">'
    // Regular flat-top hex polygon, side length 11, inscribed at
    // 22 × 19.05 inside the 24 × 24 viewBox, centered vertically
    // (top edge at y ≈ 2.5, bottom edge at y ≈ 21.5; hex center at
    // y=12). The 1 px slack on each side leaves room for the 2 px
    // stroke.
    + '<polygon class="hub-marker-shape" points="6.5,2.5 17.5,2.5 23,12 17.5,21.5 6.5,21.5 1,12"/>'
    // "H" centered horizontally via text-anchor=middle. SVG <text>'s
    // `y` is the BASELINE, not the visual center, so to place the
    // optical center of a capital letter at the hex center (y=12),
    // baseline = 12 + cap_height/2. For a 12 px sans-serif (system-
    // font stack via font-family:inherit), cap-height ≈ 0.7×12 ≈
    // 8.4 px, so baseline at y = 12 + 4.2 = 16.2. SVG
    // dominant-baseline=central is unreliable across browsers and
    // varies per font; explicit y is the portable form.
    + '<text class="hub-marker-letter" x="12" y="16.2" text-anchor="middle">H</text>'
    + '</svg>';

function addHubMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.HUB,
        className: "hub-marker",
        markerStyle: null,
        contentFn: (el, props) => {
            const chip = document.createElement("span");
            chip.className = "hub-marker-icon";
            chip.innerHTML = HUB_SVG;
            el.appendChild(chip);
            if (props.name) {
                const label = document.createElement("div");
                label.className = "hub-marker-label";
                label.textContent = props.name;
                el.appendChild(label);
            }
        },
        addToMap,
        targetArray: hubMarkers,
    });
}

// Toilet markers, OSM amenity=toilets. Proximity-filtered at the
// wider POI_AMENITY_PROXIMITY_METERS (500 m) threshold so riders
// see toilets that are usefully close to the trail without the
// noise of every distant building polygon in the bbox. Square
// swatch with a stylized figure glyph. No popup: the marker IS
// the entire signal a rider needs ("there's a toilet here"); name
// + access/fee metadata are noise mid-ride and the popup-card adds
// tap friction. Search-overlay selection still pans + ring-pulses;
// createPoiMarkers and highlightPoi both gate popup attachment
// behind a popupHtmlFn check so omitting it cleanly skips the
// popup path.
function addToiletMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.TOILET,
        className: "poi-marker toilet-marker",
        contentFn: (el) => {
            // mdi:human-male-female (Apache 2.0, Pictogrammers).
            // SVG width/height come from .poi-marker svg in CSS, so
            // omit width/height attributes, the CSS rule provides
            // a single source of truth (--poi-marker-svg-size).
            el.innerHTML = '<svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M7.5,2A2,2 0 0,1 9.5,4A2,2 0 0,1 7.5,6A2,2 0 0,1 5.5,4A2,2 0 0,1 7.5,2M6,7H9A2,2 0 0,1 11,9V14.5H9.5V22H5.5V14.5H4V9A2,2 0 0,1 6,7M16.5,2A2,2 0 0,1 18.5,4A2,2 0 0,1 16.5,6A2,2 0 0,1 14.5,4A2,2 0 0,1 16.5,2M15,22V16H12L14.59,8.41C14.84,7.59 15.6,7 16.5,7C17.4,7 18.16,7.59 18.41,8.41L21,16H18V22H15Z"/></svg>';
        },
        addToMap,
        targetArray: toiletMarkers,
    });
}

// Drinking-water markers, OSM amenity=drinking_water. Same
// proximity-filtered (500 m), no-popup pattern as toilets, the
// marker IS the signal ("there's water here"). Droplet glyph,
// blue swatch.
function addDrinkingWaterMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.DRINKING_WATER,
        className: "poi-marker drinking-water-marker",
        contentFn: (el) => {
            // mdi:water (Apache 2.0, Pictogrammers). SVG width/height
            // from .poi-marker svg in CSS, see toilet note above.
            el.innerHTML = '<svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M12,20A6,6 0 0,1 6,14C6,10 12,3.25 12,3.25C12,3.25 18,10 18,14A6,6 0 0,1 12,20Z"/></svg>';
        },
        addToMap,
        targetArray: drinkingWaterMarkers,
    });
}

// Event POIs (event_mode.pois), always-rendered race-day fixtures:
// start / finish, aid stations, support vehicles, etc. The popup
// shows the curator-supplied name + optional description so a tap
// surfaces the context the rider needs ("Aid Station 1: water,
// bananas, mechanic on site"). Distinct flag glyph + saturated red
// (configurable via event_mode.poi_color → --event-poi-color)
// signals "race fixture, not OSM POI" at a glance.
function addEventPoiMarkers(addToMap) {
    createPoiMarkers({
        poiType: POI.EVENT,
        className: "poi-marker event-poi-marker",
        contentFn: (el, props) => {
            // mdi:flag (Apache 2.0, Pictogrammers). White on the
            // event-poi colored chip; SVG sizing comes from
            // .poi-marker svg in CSS like every other POI.
            // The name label hangs below the chip via position:
            // absolute (see .event-poi-marker-label in style.css)
            // so it doesn't affect the chip's bounding box (the
            // marker still anchors at the coordinates correctly).
            el.innerHTML =
                '<svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true">'
                + '<path d="M14.4,6L14,4H5V21H7V14H12.6L13,16H20V6H14.4Z"/>'
                + '</svg>'
                + `<span class="event-poi-marker-label">${escapeHtml(props.name)}</span>`;
        },
        popupHtmlFn: (p) => {
            // Description appears below the name when present;
            // suppressed cleanly otherwise. Both fields escaped
            // even though they're curator-supplied (defense in
            // depth, copy-paste from external sources can carry
            // markup unintentionally).
            let h = `<div class="popup-title">${escapeHtml(p.name || "Event Marker")}</div>`;
            if (p.description) {
                h += `<div class="popup-description">${escapeHtml(p.description)}</div>`;
            }
            return h;
        },
        popupMaxWidth: "240px",
        popupClass: "popup-event-poi",
        addToMap,
        targetArray: eventPoiMarkers,
    });
}

// Feature marker fill, YAML-overridable via `feature_color`.
// Used by the on-map marker's inner dot (.feature-marker-icon).
// The Options-row swatch and any other references read the same
// hex via the --feature-color CSS custom property set at boot.
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
// FAB labels, first-visit-per-map discoverability cue
// ============================================================
//
// Mounts a small pill label to the left of each FAB ("Locate",
// "Reset view", "Options", "Search") on first visit, dismisses on
// any FAB tap OR a 15 s auto-timeout, then sets an LS flag so
// returning riders never see the labels again.
//
// Coordination with the welcome modal: if welcome is currently up,
// wait for it to dismiss before revealing the labels. Otherwise the
// 15 s timer ticks down behind the modal backdrop and the labels
// auto-dismiss before the rider can read them.
//
// Map pan / zoom does NOT dismiss, only FAB taps (or the timeout)
// signal "I know what these do". Map interaction is too easy to
// trigger accidentally on touch (a finger graze during page-load
// reading would dismiss prematurely).
function setupFabLabels() {
    const FLAG_KEY = "mtb.fabsLabeled";
    if (LS.get(FLAG_KEY)) return;

    // Mirror the FAB-stack composition (top-right: Locate, Reset,
    // Options, GPX).
    const FABS = [
        { id: "toggle-locate",     label: "Locate" },
        { id: "toggle-reset-view", label: "Reset view" },
        { id: "toggle-options",    label: "Options" },
        // Event maps only, the GPX FAB is stripped from index.html at
        // build time otherwise, and the missing-button guard below
        // skips the entry. Label pluralized by the actual file count
        // (matches the sheet title + aria-label; see _gpxWording).
        { id: "toggle-gpx",        label: _gpxWording().label },
    ];

    const mounted = [];
    for (const f of FABS) {
        const btn = document.getElementById(f.id);
        if (!btn) continue;
        const label = document.createElement("span");
        label.className = "fab-label";
        label.textContent = f.label;
        // The FAB itself carries the canonical aria-label (e.g.
        // aria-label="Locate me"). The visible span is decoration,
        // aria-hidden so screen readers don't double-read.
        label.setAttribute("aria-hidden", "true");
        btn.appendChild(label);
        mounted.push({ btn, label });
    }

    // Routes-panel discovery label, only when the panel is COLLAPSED.
    // The expanded card is self-evident (header says "Routes", the rows
    // show color + name), so a label would be noise; the collapsed
    // chip is the form that needs naming. Mounted INSIDE the chip
    // button, same pattern as the FABs above, so a tap on the label
    // bubbles into the chip's click handler and expands the panel (a
    // label parked on the panel wrapper looked identical but swallowed
    // the tap: the expand handler lives on the chip, not the wrapper).
    // The dismiss listener still anchors on the panel container, which
    // chip clicks bubble through. initRoutePanel has already settled
    // .is-collapsed by the time we run. "Route key" names the function
    // (the card header's "Routes" names the content); it deliberately
    // under-claims, POI/difficulty symbology lives in the Options
    // swatches, not here. Shares the .fab-label styling + dismissal so
    // all discovery cues read as one first-visit moment.
    //
    // A panel that boots EXPANDED can still collapse while the cue is
    // live, the route-panel-collapsed listener below names the chip
    // the moment it appears.
    const panel = document.getElementById("route-panel");
    const panelChip = document.getElementById("route-panel-chip");
    const panelUsable = panel && panelChip
        && !panel.classList.contains("hidden");
    const mountPanelLabel = () => {
        const label = document.createElement("span");
        label.className = "fab-label";
        label.textContent = "Route key";
        label.setAttribute("aria-hidden", "true");
        panelChip.appendChild(label);
        mounted.push({ btn: panel, label });
    };
    if (panelUsable && panel.classList.contains("is-collapsed")) {
        mountPanelLabel();
    }

    if (mounted.length === 0) return;

    let dismissed = false;
    let timeoutId = null;

    function dismiss() {
        if (dismissed) return;
        dismissed = true;
        if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
        }
        document.body.classList.remove("fabs-labeled");
        LS.set(FLAG_KEY, true);
        // Remove the label spans after the slide-out animation
        // completes so they're not in the DOM forever (small but
        // hygienic, keeps query selectors lean and avoids dangling
        // .fab-label elements showing up if CSS is restyled later).
        // Match the CSS transition duration; reduced-motion users
        // get an instant detach.
        const reduce = window.matchMedia(
            "(prefers-reduced-motion: reduce)").matches;
        const ms = reduce ? 0 : 240;
        setTimeout(() => {
            for (const { label } of mounted) label.remove();
        }, ms);
    }

    // Per-FAB click dismisses the labels. { once: true } so the
    // listener self-detaches after firing, keeps the FAB's main
    // handler unaffected on subsequent clicks and avoids leaking a
    // listener that always early-returns.
    for (const { btn } of mounted) {
        btn.addEventListener("click", dismiss, { once: true });
    }

    // Late collapse: a panel that booted expanded has no label above,
    // so if the rider collapses it while the cue is still live, mount
    // "Route key" on the chip they just created (otherwise it would be
    // the one unnamed control while every FAB is labeled). Dispatched
    // by initRoutePanel's applyCollapsed. The dismiss listener attaches
    // a task later: the collapsing click is still bubbling toward the
    // panel when this handler runs, and a same-tick listener would
    // catch that click and dismiss the whole cue, label included,
    // the instant it mounted.
    if (panelUsable) {
        panel.addEventListener("route-panel-collapsed", () => {
            if (dismissed) return;
            if (mounted.some((m) => m.btn === panel)) return;
            mountPanelLabel();
            setTimeout(() => {
                if (!dismissed) {
                    panel.addEventListener("click", dismiss, { once: true });
                }
            }, 0);
        });
    }

    function reveal() {
        // Two RAFs: the first lets the browser apply the initial
        // CSS state (opacity 0, translated 8 px right) after the
        // span is in the DOM; the second flips to the visible state
        // so the transition actually fires. Without the
        // double-rAF, the browser collapses both states into a
        // single paint and the slide-in is skipped.
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                document.body.classList.add("fabs-labeled");
                timeoutId = setTimeout(dismiss, 15000);
            });
        });
    }

    const welcomeModal = document.getElementById("welcome-modal");
    const welcomeUp = welcomeModal
        && !welcomeModal.classList.contains("hidden");
    if (welcomeUp) {
        const observer = new MutationObserver(() => {
            if (welcomeModal.classList.contains("hidden")) {
                observer.disconnect();
                // Brief gap so the welcome's slide-out doesn't
                // visually fight the labels' slide-in.
                setTimeout(reveal, 200);
            }
        });
        observer.observe(welcomeModal, {
            attributes: true,
            attributeFilter: ["class"],
        });
    } else {
        reveal();
    }
}


// ============================================================
// Floating chrome, brand element top-left, FAB stacks on the right
// edge (Locate + Options top-right, Search bottom-right), plus two
// overlays (Search half-sheet + Options full-screen).
// ============================================================
function setupFloatingChrome() {
    // ----- Brand element (top-left) ---------------------------------
    // The brand-img and brand-title are both rendered by the build at
    // template-substitution time, the build replaces __BRAND_TITLE__
    // with the configured map title and strips the <img> tag if
    // neither logo: nor icon: is configured. CSS handles the visible
    // fallback (img hides span via :has when img is present).
    //
    // Tag the img with .invert-dark so the CSS rule
    // [data-color-scheme="dark"] #brand-img.invert-dark { filter: invert(1) ... }
    // applies in dark mode. Default is on (matches historical
    // behavior); curators with colored logos that look bad inverted
    // set invert_logo_dark: false in YAML to opt out.
    if (CONFIG.invertLogoDark !== false) {
        const brandImg = document.getElementById("brand-img");
        if (brandImg) brandImg.classList.add("invert-dark");
    }

    // ----- Reset View FAB (top-right stack, between Locate + Options)
    //
    // Always restores the framework's canonical view (fitBounds
    // CONFIG.bbox + 50 px padding), regardless of how the rider
    // arrived. See _initialViewTarget declaration for the rationale
    // on why this isn't context-aware. Highlight state is
    // intentionally NOT touched, the rider clears highlights via
    // the chip's X. flyTo for an animated restore (300 ms feels like
    // "reset" without losing context; longer would feel sluggish for
    // what's effectively an undo).
    const resetBtn = document.getElementById("toggle-reset-view");
    if (resetBtn && map) {
        resetBtn.addEventListener("click", () => {
            if (!_initialViewTarget) return;
            // _initialViewTarget.kind is always "bounds" today (the
            // framework canonical view). The kind discriminator is
            // kept on the snapshot so a future change can re-introduce
            // context-aware reset (e.g. per-feature) without
            // restructuring the click handler.
            map.fitBounds(
                [
                    [_initialViewTarget.bbox[0], _initialViewTarget.bbox[1]],
                    [_initialViewTarget.bbox[2], _initialViewTarget.bbox[3]],
                ],
                {
                    padding: _initialViewTarget.padding,
                    bearing: 0,
                    pitch: 0,
                    duration: 300,
                },
            );
        });
    }

    // ----- Search overlay (half-sheet) + Options overlay (full-screen)
    //
    // Two distinct surfaces, each with its own open/close lifecycle.
    // The Options overlay covers the entire viewport; the Search
    // overlay covers the bottom ~55% so the map stays visible above.
    // Both are dismissed via Escape or their own close button; the
    // Search overlay also dismisses on tap-outside (the visible map
    // area above the sheet). Because the overlays are visually
    // distinct from each other, only one can be open at a time,
    // opening one closes the other. The search overlay's launcher is
    // the routes panel's Search row (it absorbed the old Search FAB);
    // dismissing it returns to whichever docked panel form was up.
    const searchOverlay = document.getElementById("search-overlay");
    const optionsOverlay = document.getElementById("options-overlay");
    const searchBtn = document.getElementById("route-panel-search");
    const optionsBtn = document.getElementById("toggle-options");
    // GPX download sheet, markup only exists on maps with
    // event_mode.gpx configured (stripped at build time otherwise),
    // so these are null on most maps and every gpx code path no-ops.
    const gpxOverlay = document.getElementById("gpx-overlay");
    const gpxBtn = document.getElementById("toggle-gpx");

    // Replace the index.html's hardcoded "routes, trails, and places"
    // strings on the search overlay and its input with labels derived
    // from what this map actually surfaces. Mirrors the gating in
    // renderResults() so a map with no trails or POIs doesn't promise
    // those results in its placeholder/aria-labels. The
    // panel's Search button keeps its static "Search" text, it's an
    // honest button, not a preview of the input, but its aria-label
    // carries the full target list for screen readers.
    {
        const targets = _searchTargets();
        if (targets.length) {
            const finderInput = document.getElementById("finder-input");
            const placeholder = `Search ${targets.join(", ")}…`;
            const ariaLabel = `Search ${_joinHumanList(targets)}`;
            if (finderInput) {
                finderInput.placeholder = placeholder;
                finderInput.setAttribute("aria-label", ariaLabel);
            }
            if (searchBtn) searchBtn.setAttribute("aria-label", ariaLabel);
            if (searchOverlay) searchOverlay.setAttribute("aria-label", ariaLabel);
        }
    }

    function setOverlayOpen(overlay, btn, open) {
        if (!overlay) return;
        if (open) {
            overlay.hidden = false;
            // Force a reflow before adding .is-open so the slide-up
            // transition fires (otherwise the element jumps from
            // hidden directly to translateY(0)).
            // eslint-disable-next-line no-unused-expressions
            overlay.offsetHeight;
            overlay.classList.add("is-open");
            if (btn) btn.setAttribute("aria-pressed", "true");
        } else {
            overlay.classList.remove("is-open");
            if (btn) btn.setAttribute("aria-pressed", "false");
            // Wait for slide-out transition before re-hiding the
            // element so the animation plays. Match the CSS
            // transition duration (0.22s) plus a small margin.
            const reduce = window.matchMedia(
                "(prefers-reduced-motion: reduce)").matches;
            const ms = reduce ? 0 : 240;
            setTimeout(() => {
                if (!overlay.classList.contains("is-open")) {
                    overlay.hidden = true;
                }
            }, ms);
        }
    }

    function openSearchOverlay() {
        // Single-overlay invariant: close Options / GPX if open.
        if (optionsOverlay && optionsOverlay.classList.contains("is-open")) {
            setOverlayOpen(optionsOverlay, optionsBtn, false);
        }
        if (gpxOverlay && gpxOverlay.classList.contains("is-open")) {
            setOverlayOpen(gpxOverlay, gpxBtn, false);
        }
        setOverlayOpen(searchOverlay, searchBtn, true);
        // Auto-focus the input ONLY on devices whose primary input is
        // a real pointer (desktop / laptop with mouse or trackpad).
        // On touch-primary devices (phones, tablets, PWAs running
        // in standalone mode) auto-focus pops the OS keyboard
        // immediately, which covers half the screen and hides the
        // result list, riders can't see what's searchable until
        // they dismiss the keyboard. Skipping focus here lets the
        // rider see the empty-state suggestions and scroll the list
        // first; tapping the input themselves brings up the keyboard
        // when they're ready to type. Desktop riders still get the
        // start-typing-immediately convenience because no keyboard
        // appears on focus there.
        const isTouchPrimary = window.matchMedia(
            "(pointer: coarse)").matches;
        if (!isTouchPrimary) {
            const finderInput = document.getElementById("finder-input");
            if (finderInput) setTimeout(() => finderInput.focus(), 50);
        }
    }
    function closeSearchOverlay() {
        setOverlayOpen(searchOverlay, searchBtn, false);
        // Drop focus from the input so iOS can dismiss the keyboard.
        const finderInput = document.getElementById("finder-input");
        if (finderInput) finderInput.blur();
    }
    function toggleSearchOverlay() {
        if (!searchOverlay) return;
        if (searchOverlay.classList.contains("is-open")) closeSearchOverlay();
        else openSearchOverlay();
    }

    function openOptionsOverlay() {
        if (searchOverlay && searchOverlay.classList.contains("is-open")) {
            setOverlayOpen(searchOverlay, searchBtn, false);
        }
        if (gpxOverlay && gpxOverlay.classList.contains("is-open")) {
            setOverlayOpen(gpxOverlay, gpxBtn, false);
        }
        setOverlayOpen(optionsOverlay, optionsBtn, true);
    }
    function closeOptionsOverlay() {
        setOverlayOpen(optionsOverlay, optionsBtn, false);
    }
    function toggleOptionsOverlay() {
        if (!optionsOverlay) return;
        if (optionsOverlay.classList.contains("is-open")) closeOptionsOverlay();
        else openOptionsOverlay();
    }

    // Launcher click handlers, the panel's Search row and the
    // Options FAB. (The Search row can only ever OPEN in practice:
    // while the overlay is up the whole panel is faded out and
    // pointer-inert. toggle keeps the pairing symmetric and costs
    // nothing.)
    if (searchBtn) {
        searchBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            toggleSearchOverlay();
        });
    }
    if (optionsBtn) {
        optionsBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            toggleOptionsOverlay();
        });
    }

    // Search overlay's Cancel button + tap-on-backdrop dismissal.
    // The overlay element IS the backdrop (full viewport, dimmed
    // when open). Clicks land on either the overlay element itself
    // (backdrop area) or on a descendant (the sheet panel). We close
    // on the former, ignore the latter. The routes panel can't
    // interfere: while the overlay is open it's faded out and
    // pointer-inert (see the hide-under-overlay rule in style.css).
    const searchCancel = document.getElementById("search-cancel");
    if (searchCancel) {
        searchCancel.addEventListener("click", (e) => {
            e.stopPropagation();
            closeSearchOverlay();
        });
    }
    if (searchOverlay) {
        searchOverlay.addEventListener("click", (e) => {
            if (e.target === searchOverlay) closeSearchOverlay();
        });
    }

    // Search filter chips (All / Routes / Trails / Places). Single-
    // selection, clicking a chip flips it to selected and rebuilds
    // the result list. Default is "all" set at module scope. The
    // "Trails" chip is hidden when the map has no trails configured;
    // the "Places" chip is hidden when no POIs exist. The "Routes"
    // chip always shows (every map has routes). "All" stays visible
    // whenever ≥1 of the others does.
    const searchFiltersEl = document.getElementById("search-filters");
    if (searchFiltersEl) {
        const showTrails = CONFIG.showTrails !== false;
        const hasPois = poiIndex && poiIndex.length > 0;
        const chipFilters = searchFiltersEl
            .querySelectorAll(".search-filter-chip");
        for (const chip of chipFilters) {
            const f = chip.dataset.filter;
            // Hide chips whose category has no data
            if ((f === "trail" && !showTrails) ||
                (f === "poi" && !hasPois)) {
                chip.classList.add("hidden");
                continue;
            }
            chip.addEventListener("click", (e) => {
                e.stopPropagation();
                currentSearchFilter = f;
                for (const c of chipFilters) {
                    c.setAttribute("aria-selected",
                        c.dataset.filter === f ? "true" : "false");
                }
                rebuildFinderList();
            });
        }
    }

    // Options overlay's Close button + tap-on-backdrop dismissal.
    // Same pattern as search.
    const optionsClose = document.getElementById("options-close");
    if (optionsClose) {
        optionsClose.addEventListener("click", (e) => {
            e.stopPropagation();
            closeOptionsOverlay();
        });
    }
    if (optionsOverlay) {
        optionsOverlay.addEventListener("click", (e) => {
            if (e.target === optionsOverlay) closeOptionsOverlay();
        });
    }

    // ----- GPX download sheet (event maps with event_mode.gpx only)
    //
    // The FAB ALWAYS opens the sheet, even with a single configured
    // file, so a curiosity tap shows a dismissible sheet naming the
    // route instead of instantly dropping a file into the rider's
    // Downloads folder. Tapping a row downloads that row's file via a
    // temporary same-origin <a download>; the saved filename is the
    // URL basename, preserved verbatim from the curator's file so it
    // matches copies distributed by the event's official source. The
    // sheet stays open across row taps so a rider can grab several
    // routes in one visit.
    //
    // Wording is count-aware and shared across every surface (FAB
    // discovery label, FAB aria-label, sheet title, welcome-modal
    // row) via _gpxWording() so "GPX file" vs "GPX files" never
    // disagrees between the tooltip and the sheet it opens.
    {
        const w = _gpxWording();
        const gpxTitle = document.getElementById("gpx-overlay-title");
        if (gpxTitle) gpxTitle.textContent = w.action;
        if (gpxBtn) gpxBtn.setAttribute("aria-label", w.action);
    }
    function closeGpxOverlay() {
        setOverlayOpen(gpxOverlay, gpxBtn, false);
    }
    function openGpxOverlay() {
        // Single-overlay invariant, same as Search / Options.
        if (searchOverlay && searchOverlay.classList.contains("is-open")) {
            setOverlayOpen(searchOverlay, searchBtn, false);
        }
        if (optionsOverlay && optionsOverlay.classList.contains("is-open")) {
            setOverlayOpen(optionsOverlay, optionsBtn, false);
        }
        setOverlayOpen(gpxOverlay, gpxBtn, true);
    }
    const gpxList = document.getElementById("gpx-list");
    if (gpxOverlay && gpxBtn && gpxList) {
        for (const entry of CONFIG.gpxDownloads || []) {
            const row = document.createElement("button");
            row.type = "button";
            row.className = "finder-row gpx-row";
            const name = document.createElement("span");
            name.className = "finder-row-name";
            name.textContent = entry.name;
            row.appendChild(name);
            const glyph = document.createElement("span");
            glyph.className = "gpx-row-glyph";
            glyph.setAttribute("aria-hidden", "true");
            // mdi:download (Apache 2.0, Pictogrammers), trailing cue
            // that tapping the row saves a file.
            glyph.innerHTML =
                '<svg viewBox="0 0 24 24" width="18" height="18" ' +
                'fill="currentColor" aria-hidden="true">' +
                '<path d="M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z"/></svg>';
            row.appendChild(glyph);
            row.addEventListener("click", (e) => {
                e.stopPropagation();
                const a = document.createElement("a");
                a.href = entry.url;
                // Empty download attr = save under the URL's basename.
                a.download = "";
                document.body.appendChild(a);
                a.click();
                a.remove();
            });
            gpxList.appendChild(row);
        }
        gpxBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (gpxOverlay.classList.contains("is-open")) closeGpxOverlay();
            else openGpxOverlay();
        });
        const gpxClose = document.getElementById("gpx-close");
        if (gpxClose) {
            gpxClose.addEventListener("click", (e) => {
                e.stopPropagation();
                closeGpxOverlay();
            });
        }
        gpxOverlay.addEventListener("click", (e) => {
            if (e.target === gpxOverlay) closeGpxOverlay();
        });
    }

    // When the About modal is up, the overlay Escape handlers must
    // stand down, About sits on top of everything and owns the
    // foreground. Without this guard a stray Escape would close the
    // overlay behind the modal silently.
    function isAboutOpen() {
        const aboutModal = document.getElementById("about-modal");
        return aboutModal && !aboutModal.classList.contains("hidden");
    }

    // Escape = dismiss topmost state, one press at a time. Priority
    // order: About modal > Options overlay > Search overlay >
    // highlight. Consolidated handler so we don't have multiple
    // listeners racing on the same keystroke.
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        if (isAboutOpen()) {
            closeAboutModal();
            return;
        }
        if (optionsOverlay && optionsOverlay.classList.contains("is-open")) {
            closeOptionsOverlay();
            return;
        }
        if (gpxOverlay && gpxOverlay.classList.contains("is-open")) {
            closeGpxOverlay();
            return;
        }
        if (searchOverlay && searchOverlay.classList.contains("is-open")) {
            closeSearchOverlay();
            return;
        }
        // Both the route/trail highlight and the POI highlight set
        // count as "something to dismiss." clearHighlight() handles
        // both, so a single Esc press reliably clears whatever's lit.
        if (highlight || _highlightedPois.length > 0) clearHighlight();
    });

    // Expose closeSearchOverlay so the finder row clicks (defined
    // outside this function's scope) can dismiss search after a
    // selection commits. Replaces the old window.__closeSearchOverlay.
    window.__closeSearchOverlay = closeSearchOverlay;

    // ----- Season toggle ---------------------------------------------
    //
    // Segmented Summer / Winter row in the Options overlay. Hidden
    // entirely when the current map has no winter routes, a control
    // that never does anything is worse than no control.
    //
    // Icons are inline SVG rather than emoji (☀ / ❄). Platform emoji
    // rendering varies wildly, iOS draws a full-color glyph, Android
    // a different one, Linux a monochrome system font, etc., which
    // made the summer/winter swatch look subtly wrong on most devices.
    // A hand-drawn SVG renders identically everywhere and picks up
    // `color: white` from the swatch via `currentColor`.
    // mdi:weather-sunny (Apache 2.0, Pictogrammers)
    const SUN_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M12,7A5,5 0 0,1 17,12A5,5 0 0,1 12,17A5,5 0 0,1 7,12A5,5 0 0,1 12,7M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9M12,2L14.39,5.42C13.65,5.15 12.84,5 12,5C11.16,5 10.35,5.15 9.61,5.42L12,2M3.34,7L7.5,6.65C6.9,7.16 6.36,7.78 5.94,8.5C5.5,9.24 5.25,10 5.11,10.79L3.34,7M3.36,17L5.12,13.23C5.26,14 5.53,14.78 5.95,15.5C6.37,16.24 6.91,16.86 7.5,17.37L3.36,17M20.65,7L18.88,10.79C18.74,10 18.47,9.23 18.05,8.5C17.63,7.78 17.1,7.15 16.5,6.64L20.65,7M20.64,17L16.5,17.36C17.09,16.85 17.62,16.22 18.04,15.5C18.46,14.77 18.73,14 18.87,13.21L20.64,17M12,22L9.59,18.56C10.33,18.83 11.14,19 12,19C12.82,19 13.63,18.83 14.37,18.56L12,22Z"/></svg>';
    // Six-ray snowflake: three lines through the center. Tiny "V"
    // barbs at each tip give it the characteristic flake silhouette
    // rather than reading as a plain asterisk.
    // mdi:snowflake (Apache 2.0, Pictogrammers)
    const SNOW_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M20.79,13.95L18.46,14.57L16.46,13.44V10.56L18.46,9.43L20.79,10.05L21.31,8.12L19.54,7.65L20,5.88L18.07,5.36L17.45,7.69L15.45,8.82L13,7.38V5.12L14.71,3.41L13.29,2L12,3.29L10.71,2L9.29,3.41L11,5.12V7.38L8.5,8.82L6.5,7.69L5.92,5.36L4,5.88L4.47,7.65L2.7,8.12L3.22,10.05L5.55,9.43L7.55,10.56V13.45L5.55,14.58L3.22,13.96L2.7,15.89L4.47,16.36L4,18.12L5.93,18.64L6.55,16.31L8.55,15.18L11,16.62V18.88L9.29,20.59L10.71,22L12,20.71L13.29,22L14.7,20.59L13,18.88V16.62L15.5,15.17L17.5,16.3L18.12,18.63L20,18.12L19.53,16.35L21.3,15.88L20.79,13.95M9.5,10.56L12,9.11L14.5,10.56V13.44L12,14.89L9.5,13.44V10.56Z"/></svg>';
    const seasonField = document.getElementById("season-field");
    const seasonSwatch = seasonField && seasonField.querySelector(".season-swatch");
    const seasonButtons = seasonField
        ? Array.from(seasonField.querySelectorAll(".opt-segmented-btn"))
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
                // Both colors live in CSS, summer is the default
                // .season-swatch background (forest green), winter
                // is layered on via .is-winter (cold glacier teal).
                // Toggling a class instead of mutating inline style
                // keeps the colors discoverable in style.css.
                seasonSwatch.classList.toggle("is-winter", !isSummer);
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
    // Same segmented On/Off format as the POI toggles. Shown only
    // when the current map has at least one route with
    // `emergency: true`. Reveal the row first, then wire, wirePeekToggle
    // bails on hidden rows.
    const emBtn = document.getElementById("toggle-emergency-routes");
    const hasEmergencyRoutes = anyRouteHas("emergency");
    if (emBtn && hasEmergencyRoutes) {
        emBtn.classList.remove("hidden");
        wirePeekToggle("toggle-emergency-routes", "mtb.emergencyOn",
                isDefaultVisible("emergency"), (on) => {
            emergencyOn = on;
            applyVisibilityChange();
        }, "emergency");
    } else {
        if (emBtn) emBtn.classList.add("hidden");
        // Force off, no route data to toggle.
        emergencyOn = false;
    }

    // ----- POI swatches ---------------------------------------------
    // All swatch colors flow from YAML via CSS custom properties on
    // :root (see setPoiColorVars near the top of this file). The
    // matching CSS rules in style.css consume --marker-color /
    // --marker-text-color / --marker-border-color (and the parking /
    // trailhead equivalents). No JS-driven inline-style overrides
    // here, that was a legacy path that beat CSS-var specificity
    // and caused the Features chip "purple fill" bug earlier.
    //
    // The feature-swatch wrapper is intentionally transparent, the
    // on-map marker appearance (colored dot + ring + drop shadow)
    // is rendered by .feature-swatch::before whose fill lives in CSS.

    // ----- POI toggle rows (aria-pressed buttons) -------------------
    //
    // Each row carries on/off state via aria-pressed. The wirePeekToggle
    // helper reads persisted state, sets the initial pressed value, and
    // wires the click handler. Rows already hidden (no data) are skipped.
    // The visible UI is a segmented On/Off pair (matches the Season
    // row's two side-by-side segments). aria-pressed lives on the row
    // div for the existing CSS off-state-slash treatment to keep
    // working; aria-checked drives the visible "fill the active
    // segment" appearance.
    function wirePeekToggle(id, lsKey, defaultOn, onChange, layerName) {
        const row = document.getElementById(id);
        if (!row) return;
        // forced_visible: if this layer is in CONFIG.forcedVisible,
        // mark the row on (aria-pressed=true), hide it entirely,
        // force-fire onChange(true) once so the layer renders visible,
        // and skip the click wiring. The rider has no off affordance
        // and any persisted LS state is ignored (write-through
        // suppressed too, we don't touch LS so a future config change
        // that drops the force still restores the rider's last
        // preference). layerName is optional so existing call sites
        // that haven't opted in yet keep working unchanged.
        //
        // This MUST run before the hidden-row guard below. By the time
        // setupFloatingChrome() wires the toggles, loadPOIs() →
        // updatePoiToggleVisibility() has already hidden every forced
        // proximity row, and toilets/drinking-water also start hidden
        // in the template, so the guard would otherwise short-circuit
        // the force-on and the layer would silently never render.
        // aria-pressed must be set first because the proximity-gated
        // POI layers render via updateMarkerProximity() → isOn(), which
        // reads exactly this attribute (not isForcedVisible).
        if (layerName && isForcedVisible(layerName)) {
            row.setAttribute("aria-pressed", "true");
            row.classList.add("hidden");
            onChange(true);
            return;
        }
        // No data for this layer, loadPOIs()/the template left the row
        // hidden. Skip wiring so we don't surface a dead control.
        if (row.classList.contains("hidden")) return;
        const onBtn = row.querySelector('[data-value="on"]');
        const offBtn = row.querySelector('[data-value="off"]');
        if (!onBtn || !offBtn) return;
        const initial = LS.get(lsKey, defaultOn);
        function applyState(isOn) {
            row.setAttribute("aria-pressed", isOn ? "true" : "false");
            onBtn.setAttribute("aria-checked", isOn ? "true" : "false");
            offBtn.setAttribute("aria-checked", isOn ? "false" : "true");
        }
        function setState(isOn) {
            // No-op if the state isn't changing, avoids spurious
            // onChange calls (which can trigger expensive recomputes
            // like updateMarkerProximity) when the user re-taps the
            // already-active button.
            const already = row.getAttribute("aria-pressed") === "true";
            if (already === isOn) return;
            applyState(isOn);
            LS.set(lsKey, isOn);
            onChange(isOn);
        }
        applyState(initial);
        onBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            setState(true);
        });
        offBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            setState(false);
        });

        // Whole-row click toggles the binary state. The buttons'
        // stopPropagation above keeps direct button clicks from
        // double-firing here. Mouse/touch only, keyboard users
        // still tab through the inner buttons (no separate row tab
        // stop, otherwise every binary row would add visual noise to
        // the keyboard tour without providing new functionality).
        // The marker class drives the cursor + hover affordance in
        // CSS so non-binary rows (Labels / Season) stay inert
        // outside their buttons.
        row.classList.add("opt-toggle-row-clickable");
        row.addEventListener("click", () => {
            const currentlyOn = row.getAttribute("aria-pressed") === "true";
            setState(!currentlyOn);
        });
    }

    // Trail markers, merged guideposts + emergency access points.
    // POI toggle handlers, each mutates marker visibility and then
    // rebuilds the finder so the search list stays in sync (WYSIWYG:
    // toggling a type off removes its rows from search, on adds them
    // back). updateMarkerProximity() already triggers a decoration
    // recompute (markers are obstacles for arrow/diamond placement);
    // the off-branches do the same explicitly because they bypass
    // proximity entirely.
    wirePeekToggle("toggle-markers", "mtb.poi.markers",
            isDefaultVisible("trail_markers"), (on) => {
        if (on) {
            updateMarkerProximity();  // already invalidates cache
        } else {
            for (const m of trailMarkerMarkers) m.remove();
            invalidateObstaclesCache();
            updateDecorationsSource();
        }
        _onPoiToggleChange("trail_marker");
    }, "trail_markers");

    // Features, proximity-filtered.
    wirePeekToggle("toggle-features", "mtb.poi.features",
            isDefaultVisible("features"), (on) => {
        if (on) {
            updateMarkerProximity();  // already invalidates cache
        } else {
            for (const m of featureMarkers) m.remove();
            invalidateObstaclesCache();
            updateDecorationsSource();
        }
        _onPoiToggleChange("feature");
    }, "features");

    // Parking / trailheads, always shown when on (no proximity
    // filter). Toggling either flips the obstacle set, so recompute
    // decorations after the marker visibility flips.
    wirePeekToggle("toggle-parking", "mtb.poi.parking",
            isDefaultVisible("parking"), (on) => {
        for (const m of parkingMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
        invalidateObstaclesCache();
        updateDecorationsSource();
        _onPoiToggleChange("parking");
    }, "parking");
    wirePeekToggle("toggle-trailheads", "mtb.poi.trailheads",
            isDefaultVisible("trailheads"), (on) => {
        for (const m of trailheadMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
        invalidateObstaclesCache();
        updateDecorationsSource();
        _onPoiToggleChange("trailhead");
    }, "trailheads");
    // Hubs, same on/off pattern as Trailheads / Parking (no proximity
    // filter). Hubs are trail-attached by definition, always relevant
    // when their layer is on.
    wirePeekToggle("toggle-hubs", "mtb.poi.hubs",
            isDefaultVisible("hubs"), (on) => {
        for (const m of hubMarkers) {
            if (on) m.addTo(map);
            else m.remove();
        }
        invalidateObstaclesCache();
        updateDecorationsSource();
        _onPoiToggleChange("hub");
    }, "hubs");

    // Toilets + drinking water, proximity-filtered (500 m threshold,
    // see POI_AMENITY_PROXIMITY_METERS). Same on/off pattern as
    // Markers and Features: when toggled on, defer to updateMarkerProximity
    // which adds only the in-range markers; when off, sweep them all.
    wirePeekToggle("toggle-toilets", "mtb.poi.toilets",
            isDefaultVisible("toilets"), (on) => {
        if (on) {
            updateMarkerProximity();  // already invalidates cache
        } else {
            for (const m of toiletMarkers) m.remove();
            invalidateObstaclesCache();
            updateDecorationsSource();
        }
        _onPoiToggleChange("toilet");
    }, "toilets");
    wirePeekToggle("toggle-drinking-water", "mtb.poi.drinking_water",
            isDefaultVisible("drinking_water"), (on) => {
        if (on) {
            updateMarkerProximity();  // already invalidates cache
        } else {
            for (const m of drinkingWaterMarkers) m.remove();
            invalidateObstaclesCache();
            updateDecorationsSource();
        }
        _onPoiToggleChange("drinking_water");
    }, "drinking_water");

    // Difficulty, drives the decor-diamond layer. Uses the shared
    // wirePeekToggle so the visual + behavior matches the other
    // toggles (segmented On/Off pill). Auto-hidden when no trail
    // carries an mtb:scale:imba value (parallel to the direction-
    // arrow gate on CONFIG.hasOnewayTrails), keeps the rider from
    // seeing a dead control on maps without IMBA tags.
    const difficultyBtn = document.getElementById("toggle-difficulty");
    if (CONFIG.eventModeActive && difficultyBtn) {
        // Event mode hides the difficulty toggle entirely, see the
        // comment on difficultyToggleOn(). Same posture as the Labels
        // segmented control under event mode.
        difficultyBtn.classList.add("hidden");
    } else if (CONFIG.showDifficulty && CONFIG.hasDifficultyTrails && difficultyBtn) {
        // Set the initial layer visibility from persisted state
        // before wiring; wirePeekToggle reads the LS state again
        // and applies it via the onChange callback only on user
        // interaction, not on mount.
        const initial = LS.get("mtb.difficulty", isDefaultVisible("difficulty"));
        if (map.getLayer("decor-diamond")) {
            map.setLayoutProperty("decor-diamond", "visibility",
                initial ? "visible" : "none");
        }
        wirePeekToggle("toggle-difficulty", "mtb.difficulty",
                isDefaultVisible("difficulty"), (on) => {
            if (map.getLayer("decor-diamond")) {
                map.setLayoutProperty("decor-diamond", "visibility",
                    on ? "visible" : "none");
            }
        }, "difficulty");
    } else if (difficultyBtn) {
        difficultyBtn.classList.add("hidden");
    }

    // Direction arrows, drives the decor-arrow MapLibre layer
    // (chevrons placed along one-way / reversible trails). Reveal
    // the toggle row only when the trails data actually contains at
    // least one oneway-tagged feature AND "direction_arrows" is not
    // in `forced_visible`. When forced, the layer's initial
    // visibility (set in addArrowLayer) is already on via
    // directionArrowsToggleOn() reading CONFIG.forcedVisible, we
    // just keep the toggle row hidden so the rider has no off
    // affordance.
    //
    // The "has any oneway trails" decision is a build-time scan
    // (CONFIG.hasOnewayTrails, populated by inject_config_into_template).
    // Doing it at runtime would mean reading the trail-decorations
    // source's feature count, but that source is intentionally
    // populated AFTER setupFloatingChrome() runs, the first
    // computeDecorations() pass is deferred to map.once('idle', …)
    // for first-paint perf, so a runtime count races the deferral
    // and reads 0 at gate time. Same wirePeekToggle pattern as
    // Difficulty otherwise.
    const arrowsBtn = document.getElementById("toggle-direction-arrows");
    const arrowsShown = CONFIG.showDirectionArrows !== false;
    if (arrowsBtn && CONFIG.hasOnewayTrails && arrowsShown && !isForcedVisible("direction_arrows")) {
        arrowsBtn.classList.remove("hidden");
        wirePeekToggle("toggle-direction-arrows", "mtb.directionArrows",
                isDefaultVisible("direction_arrows"), (on) => {
            if (map.getLayer("decor-arrow")) {
                map.setLayoutProperty("decor-arrow", "visibility",
                    on ? "visible" : "none");
            }
        }, "direction_arrows");
    } else if (arrowsBtn) {
        arrowsBtn.classList.add("hidden");
    }

    // ----- Appearance: Light / Dark / Auto color scheme ------------
    //
    // Three-state segmented control; clicking any button calls
    // applyColorScheme() which: persists the *intent* (light/dark/auto)
    // to LS, resolves to a concrete scheme (auto → matchMedia), sets
    // <html data-color-scheme>, rebuilds the basemap with the matching
    // Protomaps flavor, and re-applies map paint tokens (label colors,
    // arrow icon variant). The OS prefers-color-scheme listener
    // re-fires applyColorScheme("auto") when the rider's stored
    // intent is "auto", handles e.g. iOS sunset shift mid-session.
    const schemeGroup = document.getElementById("color-scheme-segmented");
    if (schemeGroup) {
        const schemeButtons = Array.from(
            schemeGroup.querySelectorAll(".opt-segmented-btn"));
        // Stored *intent*, may be "auto" even though the resolved
        // scheme on <html> is "light" or "dark". The pill highlights
        // the intent so the rider sees what they chose.
        const storedScheme = LS.get("mtb.colorScheme",
            CONFIG.defaultColorScheme || "light");
        const syncSchemePressed = () => {
            for (const b of schemeButtons) {
                b.setAttribute("aria-checked",
                    b.dataset.value === storedScheme ? "true" : "false");
            }
        };
        syncSchemePressed();
        for (const b of schemeButtons) {
            b.addEventListener("click", () => {
                const next = b.dataset.value;
                applyColorScheme(next);
                // Update local copy so re-syncs paint the right pill.
                for (const x of schemeButtons) {
                    x.setAttribute("aria-checked",
                        x.dataset.value === next ? "true" : "false");
                }
            });
        }
        watchSystemColorScheme();
    }

    // ----- Show Trails gating -----
    //
    // Drop the Trails Labels option when trails are hidden. Routes are
    // always present (a geometry source is required), so the Finder
    // section and the Routes label option always stay, no "both off"
    // case remains.
    const showTrails = CONFIG.showTrails !== false;

    // ----- Map options: labels + basemap -----
    // Labels are a segmented control (Routes / Trails / None) rather
    // than a <select>. Semantics: exactly one radio pressed at any
    // time; aria-checked drives the active paint.
    //
    // Event mode: hide the row entirely. labelMode is locked to
    // "routes" at boot (see the `let labelMode = ...` declaration
    // earlier) and the per-route label visibility is restricted to
    // featured routes only by updateLabels(), so the rider sees
    // exactly the event-route label and nothing else. Surfacing a
    // toggle they can't really change would just confuse them.
    const labelField = document.getElementById("label-field");
    const labelGroup = document.getElementById("label-segmented");
    if (CONFIG.eventModeActive || CONFIG.forcedLabels) {
        if (labelField) labelField.classList.add("hidden");
        // Skip the rest of the labels wiring: the segmented control
        // is invisible (event-mode locks to "routes"; forced_labels
        // locks to whatever the curator chose), no need to rig up
        // handlers or sync state.
    } else if (labelGroup) {
        // Drop the Trails button when trails are hidden. Routes always
        // show, so the Routes and None buttons always remain, the row
        // never collapses to None-only.
        if (!showTrails) {
            const btn = labelGroup.querySelector('[data-value="trails"]');
            if (btn) btn.remove();
        }
        const buttons = Array.from(labelGroup.querySelectorAll(".opt-segmented-btn"));
        const available = buttons.map((b) => b.dataset.value);
        if (!available.includes(labelMode)) {
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
            // Mirror the binary-toggle off-state visual on the row when
            // the rider has picked "None", chip grays out + slash
            // overlay, same treatment as a layer toggle that's been
            // turned off. data-multi-off is consumed by CSS; using a
            // data attribute (not aria-pressed) avoids confusing
            // screen readers since the actual control is a radiogroup.
            if (labelField) {
                if (labelMode === "none") {
                    labelField.setAttribute("data-multi-off", "true");
                } else {
                    labelField.removeAttribute("data-multi-off");
                }
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
    // accordion section. Keep both in sync, when there are no
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

    // (Section accordions removed, with ~14 rows total across three
    // sections, the panel scrolls cleanly without needing per-section
    // collapse. Section headers are now plain <h3> labels, no click
    // behavior.)

    // (The search button click handler is wired at the top of this
    // function alongside the overlay open/close functions, see
    // searchBtn.addEventListener above.)

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

    // ----- Day-of-week recheck for direction schedules -----
    // Each arrow's rotation is baked into its feature properties at
    // decoration-build time, so a flip in `reverseRoutesToday` forces
    // a full source recompute (cheap, we already do it on every
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

    // Inline clear button. Shown only when the input has content, so
    // an empty search box stays visually quiet. Click clears the
    // value, refocuses the input (so the rider can keep typing
    // immediately), and rebuilds the list. We also reset the active
    // index, clearing means there's nothing to navigate to anyway.
    const clearBtn = document.getElementById("finder-clear");
    function syncClearVisibility() {
        if (!clearBtn) return;
        const hasText = (input.value || "").length > 0;
        clearBtn.classList.toggle("hidden", !hasText);
    }
    if (clearBtn) {
        clearBtn.addEventListener("click", () => {
            input.value = "";
            syncClearVisibility();
            rebuildFinderList();
            input.focus();
        });
    }

    input.addEventListener("input", () => {
        syncClearVisibility();
        rebuildFinderList();
    });
    syncClearVisibility();

    // Desktop keyboard navigation. Up/Down move through the result
    // list, Home/End jump to the ends, Enter triggers the active row
    // (or the first row if nothing's active yet, common shortcut for
    // "search and go"). Esc has two-stage behavior: clears the input
    // first if it has text, closes the overlay otherwise. preventDefault
    // on Up/Down so the text-input caret doesn't jump around.
    input.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            moveFinderActive(+1);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            moveFinderActive(-1);
        } else if (e.key === "Home") {
            e.preventDefault();
            setFinderActive(0);
        } else if (e.key === "End") {
            const rows = getFinderRows();
            if (rows.length) {
                e.preventDefault();
                setFinderActive(rows.length - 1);
            }
        } else if (e.key === "Escape") {
            // Two-stage Esc when the input is focused: first press
            // clears the search text (if any); second press falls
            // through to the document-level handler which closes the
            // overlay. preventDefault overrides Safari's native
            // search-input Esc-clear behavior so we always run our
            // own clear path (keeps clear-button visibility, active
            // index, and result list in sync). stopPropagation
            // prevents the document handler from also closing the
            // overlay on the same keystroke.
            if ((input.value || "").length > 0) {
                e.preventDefault();
                e.stopPropagation();
                input.value = "";
                syncClearVisibility();
                rebuildFinderList();
            }
            // else: empty input, let Esc bubble to the global
            // handler, which closes the search overlay.
        } else if (e.key === "Enter") {
            const rows = getFinderRows();
            if (!rows.length) return;
            e.preventDefault();
            const idx = _finderActiveIndex >= 0 ? _finderActiveIndex : 0;
            const target = rows[idx];
            if (target) target.click();
        }
    });

    rebuildFinderList();
}

// Returns the currently-rendered .finder-row buttons (skipping
// section headers, which aren't selectable). Read from the DOM each
// time so we always reflect the latest rebuild.
function getFinderRows() {
    const list = document.getElementById("finder-list");
    if (!list) return [];
    return Array.from(list.querySelectorAll(".finder-row"));
}

// Mark a given row index as the active keyboard target. Updates the
// is-active class + aria-activedescendant + scrolls into view (block:
// "nearest" so the list doesn't jump if the row is already visible).
// Pass -1 to clear the active state entirely.
function setFinderActive(index) {
    const rows = getFinderRows();
    const input = document.getElementById("finder-input");
    // Clear prior active row regardless of where we land.
    for (const r of rows) r.classList.remove("is-active");

    if (index < 0 || index >= rows.length) {
        _finderActiveIndex = -1;
        if (input) input.setAttribute("aria-activedescendant", "");
        return;
    }
    _finderActiveIndex = index;
    const row = rows[index];
    row.classList.add("is-active");
    if (input && row.id) input.setAttribute("aria-activedescendant", row.id);
    // scrollIntoView with block: "nearest" only scrolls when the row
    // is actually off-screen, no-op when already visible.
    row.scrollIntoView({ block: "nearest" });
}

// Move the active index by delta (typically +1 or -1). Wraps at the
// ends, pressing Down on the last row goes to the first (and vice
// versa) so the rider can flick through a short list without
// thinking about boundaries.
function moveFinderActive(delta) {
    const rows = getFinderRows();
    if (!rows.length) return;
    let next;
    if (_finderActiveIndex < 0) {
        // No active row yet. Down → first row, Up → last row.
        next = delta > 0 ? 0 : rows.length - 1;
    } else {
        next = (_finderActiveIndex + delta + rows.length) % rows.length;
    }
    setFinderActive(next);
}

function rebuildFinderList() {
    const list = document.getElementById("finder-list");
    const empty = document.getElementById("finder-empty");
    if (!list) return;

    const input = document.getElementById("finder-input");
    const query = (input && input.value || "").trim().toLowerCase();
    const showTrails = CONFIG.showTrails !== false;

    list.innerHTML = "";
    // Each rebuild wipes the prior active row (since the rows
    // themselves are gone). Reset state so Enter doesn't try to fire
    // an index that no longer exists.
    _finderActiveIndex = -1;
    const finderInput = document.getElementById("finder-input");
    if (finderInput) finderInput.setAttribute("aria-activedescendant", "");

    // Active filter chip determines which kinds get included. "all"
    // is the default and includes every kind. Per-kind chip
    // restricts to just that kind.
    const filter = currentSearchFilter || "all";
    const includeRoutes = (filter === "all" || filter === "route");
    const includeTrails = (filter === "all" || filter === "trail") && showTrails;
    const includePois = (filter === "all" || filter === "poi");

    // If everything's filtered out by config, bail.
    if (!includeRoutes && !includeTrails && !includePois) {
        if (empty) empty.classList.add("hidden");
        return;
    }

    // Filter routes/trails to the currently-visible bucket, then by
    // query. Routes hidden by season/emergency toggles are still
    // searchable, selecting one force-shows it (rider toggle is the
    // explicit choice, search lets them work around it).
    const routes = routeIndex.filter((r) => visibleRoutes.has(r.id));
    const visibleRouteIds = new Set(routes.map((r) => r.id));
    const trails = trailIndex.filter((t) =>
        t.routeIds.some((rid) => visibleRouteIds.has(rid)));

    const matchedRoutes = !includeRoutes ? []
        : (query ? routes.filter((r) => r.name.toLowerCase().includes(query))
                 : routes);
    const matchedTrails = !includeTrails ? []
        : (query ? trails.filter((t) => t.name.toLowerCase().includes(query))
                 : trails);
    // POIs follow a hybrid rule: search shows a POI iff it's within
    // the type's proximity threshold of any visible trail. Toggle-
    // hidden POIs STAY searchable (the rider chose to hide the
    // category but might still want to find a specific item by name);
    // selecting one force-mounts the type's markers with a chip note.
    // Proximity-hidden POIs are excluded, the curator's auto-filter
    // says they're not relevant to any visible trail. Cached, the
    // proximity scan is far too expensive to run per keystroke.
    const inScopePois = finderPoisInScope();
    const matchedPois = !includePois ? []
        : (query ? inScopePois.filter((p) => {
            const name = p.name.toLowerCase();
            const typeLabel = (POI_TYPE_META_LABEL[p.type] || "").toLowerCase();
            return name.includes(query) || typeLabel.includes(query);
        })
                 : inScopePois);

    const total = matchedRoutes.length + matchedTrails.length + matchedPois.length;
    if (total === 0) {
        if (empty) empty.classList.remove("hidden");
        return;
    }
    if (empty) empty.classList.add("hidden");

    if (matchedRoutes.length > 0) {
        const h = document.createElement("div");
        h.className = "finder-section-header";
        h.textContent = "Routes";
        list.appendChild(h);
        for (const r of matchedRoutes) {
            list.appendChild(makeRouteRow(r));
        }
    }

    if (matchedTrails.length > 0) {
        const h = document.createElement("div");
        h.className = "finder-section-header";
        h.textContent = "Trails";
        list.appendChild(h);
        for (const t of matchedTrails) {
            list.appendChild(makeTrailRow(t, visibleRouteIds));
        }
    }

    if (matchedPois.length > 0) {
        const h = document.createElement("div");
        h.className = "finder-section-header";
        h.textContent = "Places";
        list.appendChild(h);
        // Group same-type, same-name POIs into a single row. Most
        // OSM POIs (toilets, drinking-water fountains, often
        // parking) share a generic name, listing them as N
        // identical rows wastes space AND offers no way for the
        // rider to differentiate. Better: one row that says
        // "Toilets × 5" and highlights all five on the map at once.
        // POIs with unique names (e.g. "South Trailhead", "Visitor
        // Center Toilets") stay as individual rows since they're
        // already distinguishable.
        // groupPoisForFinder collapses by category when the query
        // matches a type's label (so "parking" → one "Parking × N"
        // row), and otherwise falls back to name-grouping.
        const grouped = groupPoisForFinder(matchedPois, query);
        for (const item of grouped) {
            list.appendChild(makePoiRow(item));
        }
    }

    // Assign sequential ids so aria-activedescendant on the input can
    // reference whichever row is currently keyboard-active. Also
    // makes the rows targetable by the keydown handler in setupFinder.
    const rows = list.querySelectorAll(".finder-row");
    rows.forEach((row, i) => { row.id = `finder-opt-${i}`; });
}

// Collapse same-type same-name POIs into group entries. Entries with
// unique (type, name) pass through unchanged; multi-member entries
// become {isGroup: true, type, name, count, members: [...]}.
function groupPoisByName(pois) {
    const buckets = new Map();
    for (const p of pois) {
        const key = `${p.type}:${p.name}`;
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(p);
    }
    const out = [];
    for (const members of buckets.values()) {
        if (members.length === 1) {
            out.push(members[0]);
        } else {
            out.push({
                isGroup: true,
                uid: `group:${members[0].type}:${members[0].name}`,
                type: members[0].type,
                name: members[0].name,
                count: members.length,
                members,
            });
        }
    }
    // Preserve original alphabetical order
    out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
}

// Top-level grouping for the finder. Splits POIs into two paths:
//
//   1. Category sections, when the query matches a POI type's
//      category label (e.g. "parking" matches typeLabel "parking",
//      "trail" matches "trailhead" and "trail marker"), the type
//      gets a category-group row at the top ("Parking × N",
//      highlights ALL members at once) followed by the individual
//      name-grouped rows for that type below. The rider can pick
//      "highlight all" or a specific named one without losing
//      either affordance.
//
//      Dedup: if name-grouping for the type collapses to a single
//      entry (e.g. every toilet shares the generic name "Toilets"),
//      that one entry IS the category group, no point showing it
//      twice. The single name-group row stands in for both.
//
//   2. Name-based groups, POIs whose names match the query (but
//      whose type didn't) fall through to groupPoisByName, which
//      keeps unique-named entries as individual rows and collapses
//      duplicates by (type, name) as before.
//
// Empty query → every type gets the category treatment (so the
// blank-search overlay surfaces "Parking (× 5)" + lots, etc.,
// giving zero-keystroke access to category-level highlighting).
function groupPoisForFinder(matchedPois, query) {
    const q = (query || "").toLowerCase().trim();

    const queryMatchesType = (type) => {
        // No query means the rider just opened the search overlay
        // surface every type as its own category section so they
        // can highlight an entire type with one tap, no typing.
        if (!q) return true;
        const label = (POI_TYPE_META_LABEL[type] || "").toLowerCase();
        return label.includes(q);
    };

    // Partition matched POIs by whether their type's label matches
    // the query. Type-matched POIs get the category-section
    // treatment below; everything else falls through to plain
    // name-based grouping at the end.
    const categoryGroupTypes = new Set();
    const byType = new Map();
    const remaining = [];
    for (const p of matchedPois) {
        if (queryMatchesType(p.type)) {
            categoryGroupTypes.add(p.type);
            if (!byType.has(p.type)) byType.set(p.type, []);
            byType.get(p.type).push(p);
        } else {
            remaining.push(p);
        }
    }

    // Sort the type-matched categories by display name so result
    // order is stable across queries (Parking before Trailhead,
    // etc.).
    const sortedTypes = Array.from(categoryGroupTypes).sort((a, b) => {
        const na = POI_TYPE_FALLBACK_NAME[a] || a;
        const nb = POI_TYPE_FALLBACK_NAME[b] || b;
        return na.localeCompare(nb);
    });

    const out = [];
    for (const type of sortedTypes) {
        const members = byType.get(type);
        if (!members || members.length === 0) continue;
        // Build the per-(type, name) groups for this type using the
        // existing helper so duplicate names collapse normally.
        const namedGroups = groupPoisByName(members);

        if (namedGroups.length === 1) {
            // Single name-group means every POI of this type
            // shares the same name (typical for generic-named OSM
            // amenities like toilets / drinking water). The lone
            // name-group already represents the whole category;
            // adding a sibling category row above it would be
            // visual duplication. Just push the lone group, its
            // "(× N)" count makes the aggregate nature clear.
            out.push(namedGroups[0]);
        } else if (members.length === 1) {
            // Single POI of this type, period. No group needed,
            // just show the row as itself. (Edge case: same as
            // namedGroups.length === 1 above, but worth keeping
            // explicit for readability.)
            out.push(members[0]);
        } else {
            // 2+ name-groups for this type. Push a category-level
            // "(× N)" row first, then the individual name-grouped
            // rows below it. The category row's count reads as
            // "all of them" to the rider; no need for a separate
            // "(All)" decoration.
            const baseName = POI_TYPE_FALLBACK_NAME[type] || type;
            out.push({
                isGroup: true,
                uid: `category:${type}`,
                type,
                name: baseName,
                count: members.length,
                members,
            });
            // Suppress fully-synthesized name-groups, they
            // represent the "unnamed cluster" which is already
            // covered by the category row above. Without this
            // we'd ship two visually-identical rows ("Toilets ×
            // N" twice) with no way to tell them apart.
            for (const ng of namedGroups) {
                const allSynth = ng.isGroup
                    ? ng.members.every((m) => m.synthesized)
                    : ng.synthesized;
                if (allSynth) continue;
                out.push(ng);
            }
        }
    }

    // Append name-grouped rows for the non-type-matched
    // remainder. groupPoisByName applies its own alphabetical
    // sort, so the two blocks read as category sections first,
    // name-only matches below.
    const remainingGrouped = groupPoisByName(remaining);
    out.push(...remainingGrouped);

    return out;
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

// Shared route-swatch builder for the key rows (rebuildRoutePanel),
// the finder rows (makeRouteRow), and the highlight chip
// (showHighlightChip), so the three surfaces can never disagree
// about how a route's line is drawn. Plain routes get the flat color
// bar; dashed routes get a mini inline SVG ribbon, a two-color
// underlay beneath a dashed top line, round pills for a [0, N] pattern.
// `className` is the caller's own swatch class (.route-panel-swatch,
// .finder-row-swatch, or .highlight-chip-swatch), which the .is-dashed
// CSS variant widens for the SVG.
//
// The ribbon renders for LEGIBILITY, not map-space fidelity. Naively
// scaling the config pattern into the SVG breaks two ways at swatch
// size: (a) SVG round/square linecaps extend every dash by half the
// stroke width (2px here) at EACH end, so any scaled gap ≤ 4px is
// swallowed and the ribbon reads solid, that's [1, 1] as a solid bar;
// (b) a long-dash cycle ([4, 1]) scaled to map proportions is wider
// than the whole swatch, also reading solid. So instead: dashes render
// with butt caps (no extension) at the pattern's dash:gap duty ratio,
// two cycles across the swatch, gap clamped to stay visible; dot
// patterns render as round-capped pills. Any dashed/dotted route thus
// visibly differs from a solid bar, which is the swatch's actual job.
function routeSwatchEl(r, className) {
    if (!isDashed(r)) {
        const swatch = document.createElement("span");
        swatch.className = className;
        swatch.style.background = r.color;
        swatch.setAttribute("aria-hidden", "true");
        return swatch;
    }

    const NS = "http://www.w3.org/2000/svg";
    const W = 18, H = 8, y = H / 2, sw = 4;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", className + " is-dashed");
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("aria-hidden", "true");

    const line = (stroke, dashArray, cap, x1, x2) => {
        const el = document.createElementNS(NS, "line");
        el.setAttribute("x1", String(x1 !== undefined ? x1 : 0));
        el.setAttribute("y1", String(y));
        el.setAttribute("x2", String(x2 !== undefined ? x2 : W));
        el.setAttribute("y2", String(y));
        el.setAttribute("stroke", stroke);
        el.setAttribute("stroke-width", String(sw));
        if (cap) el.setAttribute("stroke-linecap", cap);
        if (dashArray) el.setAttribute("stroke-dasharray", dashArray);
        return el;
    };

    const dashColors = getDashColors(r);
    // Two-color dash: solid second color underlay, dashed first on top
    // (r.color already resolves to dashColors[0] via effectiveRouteColor).
    if (dashColors && dashColors.length >= 2) {
        svg.appendChild(line(dashColors[1], null));
    }

    const pattern = getDashPattern(r);
    if (pattern[0] === 0) {
        // Dot pattern ([0, N] + round cap on the map). NOT drawn as
        // perfect dots: MapLibre bakes dasharrays into a per-tile-zoom
        // SDF strip and stretches it along the line at fractional
        // zooms, so on the map the "dots" render as oblong pills most
        // of the time. Two round-capped pills match that reality,
        // perfect swatch dots over-promised (user report: swatch dots
        // vs map blobs read as different styles). Geometry: 2px dash
        // segments at path offsets 0-2 / 9-11 on a line from x=3.5 to
        // x=14.5; round caps extend each end by sw/2=2px → pills
        // spanning x 1.5-7.5 and 10.5-16.5, 3px clear between them.
        svg.appendChild(line(r.color, "2 7", "round", 3.5, 14.5));
    } else {
        // Dash pattern. Duty ratio from the config (multi-segment
        // patterns fold to their overall dash:gap ratio), two cycles
        // across the swatch, butt caps so the gap is exactly what we
        // set. Clamps keep both parts visible for extreme ratios
        // ([4, 1] would otherwise leave a 1.8px gap).
        let dashSum = 0, total = 0;
        for (let i = 0; i < pattern.length; i++) {
            total += pattern[i];
            if (i % 2 === 0) dashSum += pattern[i];
        }
        const cycle = W / 2;
        const gap = Math.min(cycle - 2,
            Math.max(2.5, cycle * (1 - dashSum / total)));
        svg.appendChild(line(r.color, `${cycle - gap} ${gap}`, "butt"));
    }
    return svg;
}

function makeRouteRow(r) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "finder-row finder-row-route";
    row.setAttribute("role", "option");
    row.dataset.routeId = r.id;

    const swatch = routeSwatchEl(r, "finder-row-swatch");

    const name = document.createElement("span");
    name.className = "finder-row-name";
    name.textContent = r.name;

    row.appendChild(swatch);
    row.appendChild(name);

    const stats = routeStatsText(r);
    if (stats) {
        const statsEl = document.createElement("span");
        statsEl.className = "finder-row-stats";
        statsEl.textContent = stats;
        row.appendChild(statsEl);
    }

    row.addEventListener("click", () => {
        highlightRoute(r.id);
        if (window.__closeSearchOverlay) window.__closeSearchOverlay();
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
        if (window.__closeSearchOverlay) window.__closeSearchOverlay();
    });

    return row;
}

// Build a finder row for a POI. Visual recipe matches the on-map
// marker's swatch so the rider's mental model is consistent: "the
// blue P I see in the search results is the blue P I'll see on the
// map." Reuses the existing .layer-swatch + per-type swatch class
// so swatch styling stays in one place. The meta line below the
// name names the POI type ("parking", "toilets", etc.) so the
// rider can disambiguate similarly-named items.
function makePoiRow(p) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "finder-row finder-row-poi";
    if (p.isGroup) row.classList.add("finder-row-poi-group");
    row.setAttribute("role", "option");
    row.dataset.poiUid = p.uid;

    // Swatch, same .layer-swatch + per-type class as the Options
    // toggle row uses, so styling/color matches the on-map marker.
    const swatch = document.createElement("span");
    swatch.className = "layer-swatch finder-row-poi-swatch";
    swatch.setAttribute("aria-hidden", "true");
    poiSwatchContent(swatch, p.type);
    row.appendChild(swatch);

    const name = document.createElement("span");
    name.className = "finder-row-name";
    name.textContent = p.name;
    row.appendChild(name);

    // Meta line: type label, plus a count badge for groups so the
    // rider sees "toilets (× 5)" instead of just "toilets", makes
    // it obvious the row represents multiple POIs. The earlier
    // "(All)" decoration on category-aggregate rows is gone, the
    // "(× N)" count IS the aggregate signal.
    const meta = document.createElement("span");
    meta.className = "finder-row-meta";
    const typeLabel = POI_TYPE_META_LABEL[p.type] || p.type;
    meta.textContent = p.isGroup
        ? `${typeLabel} (× ${p.count})`
        : typeLabel;
    row.appendChild(meta);

    row.addEventListener("click", () => {
        if (window.__closeSearchOverlay) window.__closeSearchOverlay();
        // Defer the highlight so the overlay close transition starts
        // before the map starts panning, feels less jumpy.
        setTimeout(() => {
            if (p.isGroup) highlightPoiGroup(p);
            else highlightPoi(p);
        }, 60);
    });

    return row;
}

// Apply per-type styling + glyph to a layer-swatch element used in a
// finder POI row. Each POI type has a designated swatch class
// (already styled in style.css with the right background color and
// content), we just slap on the type class and inject the glyph
// (text or SVG) that the on-map marker uses.
function poiSwatchContent(el, type) {
    switch (type) {
        case "parking":
            el.classList.add("parking-swatch");
            el.textContent = "P";
            break;
        case "trailhead":
            el.classList.add("trailhead-swatch");
            el.textContent = "TH";
            break;
        case "hub":
            el.classList.add("hub-swatch");
            // Same inline SVG as the on-map hub marker so the finder
            // swatch reads as a miniature of what the rider sees on
            // the map. See HUB_SVG above for the polygon geometry +
            // border treatment.
            el.innerHTML = HUB_SVG;
            break;
        case "trail_marker":
            el.classList.add("marker-swatch");
            el.textContent = "#";
            break;
        case "feature":
            el.classList.add("feature-swatch");
            // .feature-swatch::before draws the inner dot; nothing
            // to inject as text.
            break;
        case "toilet":
            el.classList.add("toilet-swatch");
            // mdi:human-male-female (Apache 2.0, Pictogrammers)
            el.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M7.5,2A2,2 0 0,1 9.5,4A2,2 0 0,1 7.5,6A2,2 0 0,1 5.5,4A2,2 0 0,1 7.5,2M6,7H9A2,2 0 0,1 11,9V14.5H9.5V22H5.5V14.5H4V9A2,2 0 0,1 6,7M16.5,2A2,2 0 0,1 18.5,4A2,2 0 0,1 16.5,6A2,2 0 0,1 14.5,4A2,2 0 0,1 16.5,2M15,22V16H12L14.59,8.41C14.84,7.59 15.6,7 16.5,7C17.4,7 18.16,7.59 18.41,8.41L21,16H18V22H15Z"/></svg>';
            break;
        case "drinking_water":
            el.classList.add("drinking-water-swatch");
            // mdi:water (Apache 2.0, Pictogrammers)
            el.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M12,20A6,6 0 0,1 6,14C6,10 12,3.25 12,3.25C12,3.25 18,10 18,14A6,6 0 0,1 12,20Z"/></svg>';
            break;
        case "event":
            // Same flag glyph as the on-map event-POI marker, so the
            // rider's mental model is consistent: "the red flag I
            // tap in the search results is the red flag I see on the
            // map." mdi:flag (Apache 2.0, Pictogrammers).
            el.classList.add("event-swatch");
            el.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M14.4,6L14,4H5V21H7V14H12.6L13,16H20V6H14.4Z"/></svg>';
            break;
    }
}

// ============================================================
// Trail interactions (hover, click)
// ============================================================
function setupInteractions() {
    // Every POI marker type belongs in this guard: a tap on a marker
    // chip that sits on a trail line must not bubble into the map-wide
    // click handler below and open the trail popup underneath the
    // marker the rider just tapped. toilet/water/hub were missing.
    let parkingPopupOpen = false;
    for (const marker of [...trailMarkerMarkers,
                          ...parkingMarkers, ...trailheadMarkers,
                          ...hubMarkers, ...toiletMarkers,
                          ...drinkingWaterMarkers,
                          ...featureMarkers, ...eventPoiMarkers]) {
        marker.getElement().addEventListener("click", () => {
            parkingPopupOpen = true;
            requestAnimationFrame(() => { parkingPopupOpen = false; });
        });
    }

    // Trail click handling lives in ONE map-wide handler (not
    // per-layer) so we can:
    //
    //   1. Buffer the hit-test against fat-finger taps. Per-layer
    //      `map.on("click", layerId, ...)` runs MapLibre's pixel-precise
    //      queryRenderedFeatures at the exact tap point. Trail lines
    //      are 4-6 px wide; iOS' touch recognition is stricter than
    //      Android's about sub-pixel tap accuracy. Without a buffer,
    //      taps must land within the line's rendered pixel width to
    //      register, which feels broken on iOS. The map-wide handler
    //      below uses a TRAIL_TAP_BUFFER_PX-radius bounding box
    //      around e.point \u2014 12 CSS px \u2248 Material's 48 dp tap target
    //      on a 2\u00D7 display \u2014 so taps near a line still register.
    //
    //   2. Resolve to the single tapped trail, then union that trail's
    //      routes in one pass. A way shared across N OSM routes renders
    //      as N offset lanes (one per trail-casing layer), and
    //      custom_routes (including inline event_mode.routes) bake in as
    //      parallel features on the same way, each with its own
    //      route_id + shared_routes list. A single buffered
    //      queryRenderedFeatures across every trail-casing layer gives
    //      us all overlapping features in one shot, but the buffer also
    //      crosses *unrelated* trails at junctions, so
    //      _collectAllRoutesAt first picks the feature nearest the tap,
    //      then unions routes only across features sharing that trail's
    //      name (its lanes + overlays). The previous per-layer pattern
    //      needed `_lastTrailClickEvent` dedupe state to collapse
    //      N-fired-events into one popup; that's gone now.
    //
    // The per-layer mouseenter/mouseleave handlers remain in
    // attachTrailHoverHandlers because cursor feedback IS layer-scoped
    // (we only want `cursor: pointer` over the actual rendered line,
    // not the buffered halo) and is desktop-only \u2014 iOS doesn't fire
    // mouse events.
    //
    // Buffer sizes are platform-tiered. Field-tested findings:
    //   - iOS: 12 px feels right. Below ~10 px, taps near the line
    //     don't register reliably (Safari's tap recognizer is strict
    //     about sub-pixel accuracy; finger drift cancels the tap).
    //   - Android: 6 px feels right. Android's WebView already
    //     applies ~6-8 px touch slop at the gesture-recognizer level,
    //     so we only need a small extra cushion. iOS-sized buffer
    //     here makes popups open "near" lines, feels broad.
    //   - Desktop mouse: 4 px. Mouse pointers are precise; the small
    //     buffer just forgives the occasional off-by-a-pixel click
    //     without making popups feel imprecise.
    //
    // Detection: hover/pointer media queries split mouse from touch;
    // UA + maxTouchPoints distinguishes iOS from Android within touch
    // (Safari on iPad spoofs MacIntel platform string post-iPadOS 13,
    // hence the maxTouchPoints fallback). Computed once at boot.
    const TRAIL_TAP_BUFFER_PX = (function () {
        try {
            const isTouch = window.matchMedia(
                "(hover: none) and (pointer: coarse)").matches;
            if (!isTouch) return 4;  // desktop mouse / trackpad
            const ua = navigator.userAgent || "";
            const isIOS = /iP(ad|hone|od)/.test(ua)
                || (navigator.platform === "MacIntel"
                    && (navigator.maxTouchPoints || 0) > 1);
            return isIOS ? 12 : 6;
        } catch (_) {
            // matchMedia missing or threw \u2014 fall back to a middle
            // ground that won't feel broken on any platform.
            return 6;
        }
    })();
    const _trailCasingLayerIds = Object.keys(CONFIG.routes)
        .map((rid) => `trail-casing-${rid}`);

    // Nearest point on segment A->B to P, in lng/lat. Returns
    // { dist (meters), point [lng,lat] }. Mirrors
    // pointToSegmentDistance but also yields the projected point so we
    // can anchor the popup ON the trail.
    function _segNearest(px, py, ax, ay, bx, by) {
        const cosLat = Math.cos(((py + ay + by) / 3) * Math.PI / 180);
        const dx = (bx - ax) * cosLat;
        const dy = by - ay;
        const lenSq = dx * dx + dy * dy;
        let t = 0;
        if (lenSq > 0) {
            t = Math.max(0, Math.min(1,
                ((px - ax) * cosLat * dx + (py - ay) * dy) / lenSq));
        }
        const cx = ax + t * (bx - ax);
        const cy = ay + t * (by - ay);
        const ex = (cx - px) * cosLat;
        const ey = cy - py;
        return { dist: Math.sqrt(ex * ex + ey * ey) * 111320, point: [cx, cy] };
    }

    function _collectAllRoutesAt(geometry, tapLngLat) {
        // Returns { routeIds: string[], trailName: string, anchor }.
        // routeIds: deduplicated, in stable order (custom routes first,
        // then OSM by appearance).
        // trailName: the name of the trail nearest the tap.
        // anchor: [lng,lat] on that trail, for popup placement.
        //
        // `geometry` is whatever queryRenderedFeatures accepts: a
        // single Point for hover-style precise lookups, or a
        // [[x1,y1],[x2,y2]] bounding box for buffered tap lookups.
        // `tapLngLat` is the actual tap location ({lng,lat}); a buffered
        // box can cross MANY different trails (each route renders as an
        // offset lane, so a junction packs lanes from unrelated ways
        // into a few px), so we resolve to the SINGLE trail whose
        // rendered line passes closest to the tap and union routes only
        // within that trail. Unioning across every feature in the box
        // (the old behavior) wrongly attributed unrelated trails'
        // route memberships to whichever trail's name came first.
        const feats = map.queryRenderedFeatures(geometry, {
            layers: _trailCasingLayerIds.filter((id) => map.getLayer(id)),
        });
        if (!feats.length) return { routeIds: [], trailName: "", anchor: null };

        // Find the candidate feature nearest the tap.
        let best = null; // { feat, dist, point }
        if (tapLngLat) {
            const tx = tapLngLat.lng, ty = tapLngLat.lat;
            for (const f of feats) {
                const g = f.geometry || {};
                const lines = g.type === "LineString" ? [g.coordinates]
                    : g.type === "MultiLineString" ? g.coordinates : [];
                for (const line of lines) {
                    for (let i = 0; i < line.length - 1; i++) {
                        const n = _segNearest(tx, ty,
                            line[i][0], line[i][1], line[i + 1][0], line[i + 1][1]);
                        if (!best || n.dist < best.dist) {
                            best = { feat: f, dist: n.dist, point: n.point };
                        }
                    }
                }
            }
        }
        if (!best) best = { feat: feats[0], dist: 0, point: null };

        const targetName = (best.feat.properties || {}).trail_name || "";
        const anchor = best.point;

        const seen = new Set();
        const routeIds = [];
        const addRoutes = (props) => {
            const rid = props.route_id;
            if (rid != null) {
                const k = String(rid);
                if (!seen.has(k)) { seen.add(k); routeIds.push(k); }
            }
            let shared = props.shared_routes;
            if (typeof shared === "string") {
                try { shared = JSON.parse(shared); } catch (_) { shared = []; }
            }
            if (Array.isArray(shared)) {
                for (const s of shared) {
                    const k = String(s);
                    if (!seen.has(k)) { seen.add(k); routeIds.push(k); }
                }
            }
        };

        // Named trails group by name, the offset lanes of one way, and
        // any custom-route overlay baked onto the same way, all share
        // the OSM name, so unioning across them recovers the full route
        // membership of that one trail. An unnamed way can't be grouped
        // safely (distinct unnamed ways would merge), so it reports only
        // the nearest feature's own routes.
        if (targetName) {
            for (const f of feats) {
                if ((f.properties || {}).trail_name === targetName) {
                    addRoutes(f.properties || {});
                }
            }
        } else {
            addRoutes(best.feat.properties || {});
        }

        return { routeIds, trailName: targetName, anchor };
    }

    function attachTrailHoverHandlers(layerId) {
        // Cursor feedback only \u2014 desktop hover. iOS / Android touch
        // doesn't fire these events; click handling is map-wide
        // (below) with a buffered hit-test.
        map.on("mouseenter", layerId, () => {
            map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layerId, () => {
            map.getCanvas().style.cursor = "";
        });
    }

    // Per-route hover handlers (desktop cursor feedback only).
    for (const routeId of Object.keys(CONFIG.routes)) {
        attachTrailHoverHandlers(`trail-casing-${routeId}`);
    }

    // Single map-wide click handler. Runs once per click regardless
    // of how many trail-casing layers the buffered hit-test crosses.
    // Uses a TRAIL_TAP_BUFFER_PX-radius bounding box around the tap
    // point \u2014 platform-tiered (iOS 12 / Android 6 / desktop 4) per
    // the design rationale comment above.
    map.on("click", (e) => {
        if (parkingPopupOpen) return;
        const r = TRAIL_TAP_BUFFER_PX;
        const box = [
            [e.point.x - r, e.point.y - r],
            [e.point.x + r, e.point.y + r],
        ];
        const { routeIds, trailName, anchor } = _collectAllRoutesAt(box, e.lngLat);
        if (!routeIds.length) return;

        const matchedRoutes = routeIds
            .map((id) => CONFIG.routes[id])
            .filter(Boolean);
        const routeItems = matchedRoutes
            .map((rel) => {
                // effectiveRouteColor returns a hex / rgb() string
                // from a validated palette path; still escape for
                // attribute-context safety in case a future change
                // ever lets a non-validated string through.
                const color = effectiveRouteColor(rel);
                return `<div class="popup-routes"><span style="color:${escapeHtml(color)};">\u25CF</span> ${escapeHtml(rel.name)}</div>`;
            })
            .join("");

        let html = "";
        if (trailName) {
            // trailName comes from OSM `name=` tag \u2014 UNTRUSTED.
            // Escape to neutralize any vandalism (script tags,
            // event handlers) in OSM data.
            html += `<div class="popup-title">${escapeHtml(trailName)}</div>`;
        }
        if (routeItems) {
            const label = matchedRoutes.length === 1
                ? "Part of Route:"
                : "Part of Routes:";
            if (trailName) {
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
            .setLngLat(anchor || e.lngLat)
            .setHTML(html)
            .addTo(map);
    });
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

function suppressBasemapPathLabels() {
    if (!CONFIG.suppressBasemapPathLabels) return;
    if (map.getLayer("roads_labels_minor")) {
        map.setFilter("roads_labels_minor", ["in", "kind", "minor_road"]);
    }
}

function suppressBasemapPois() {
    if (!CONFIG.suppressBasemapPois) return;
    // Two basemap layers fall under this flag, both are "decorative
    // detail that competes with the trail layer for visual attention":
    //   - "pois"             generic POI labels (peaks, museums, viewpoints)
    //   - "places_locality"  small-place labels (named neighborhoods,
    //                        clearings, hamlets). These can read as
    //                        "trail features" inside a trail-system bbox
    //                        and clutter the map even though they're
    //                        actually settlement labels.
    // Higher-tier place labels (places_country / places_region /
    // places_subplace) stay visible, they help with context at low
    // zooms and don't crowd the trail layer the way locality does.
    for (const layerId of ["pois", "places_locality"]) {
        if (map.getLayer(layerId)) {
            map.setLayoutProperty(layerId, "visibility", "none");
        }
    }
}

function suppressBasemapOnewayArrows() {
    if (!CONFIG.suppressBasemapOnewayArrows) return;
    // The Protomaps basemap's "roads_oneway" layer stamps a direction
    // arrow along anything tagged oneway=yes in the roads source-layer,
    // oneway roads AND oneway paths/trails alike. On a trail map those
    // compete with the framework's own one-way trail arrows, so let
    // curators drop them. The layer filters on oneway only (no
    // road-vs-path split), so this clears every basemap oneway arrow,
    // not just the ones on paths.
    if (map.getLayer("roads_oneway")) {
        map.setLayoutProperty("roads_oneway", "visibility", "none");
    }
}

// ============================================================
// Basemap rebuild, only when user picks a different basemap from the
// (optional) base_layers selector. Light theme only; no theme rebuilds.
// ============================================================
function rebuildBasemapLayers() {
    const base = getBaseUrl();
    const currentStyle = map.getStyle();

    // Collect non-basemap layers (trails, hillshade, highlights, etc.).
    // The type check drops the basemap flavor's own background layer,
    // background layers carry no `source`, so the source check can't
    // catch it, but OUR dim-tint spotlight wash (added by loadTrails)
    // is also a background layer and must survive the rebuild: it is
    // never re-added afterwards, and refreshSpotlightDim() needs it.
    const overlayLayers = currentStyle.layers.filter(
        (l) =>
            l.source !== "basemap" &&
            l.id !== "custom-raster" &&
            (l.type !== "background" || l.id === "dim-tint")
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
        // Same flavor logic as buildPmtilesStyle, picks dark/light
        // Protomaps tiles to match the current color scheme.
        const flavor = currentColorScheme() === "dark" ? "dark" : "light";
        baseLayers = basemaps.layers("basemap", basemaps.namedFlavor(flavor), { lang: "en" });
        spritePath = `${base}sprites/v4/${flavor}`;

        const newStyle = {
            ...currentStyle,
            sprite: spritePath,
            layers: [...baseLayers, ...overlayLayers],
        };
        map.setStyle(newStyle, { diff: true });
    }

    // Re-promote basemap labels above trail layers after style rebuild
    promoteBasemapLabels();
    suppressBasemapPathLabels();
    suppressBasemapPois();
    suppressBasemapOnewayArrows();

    // Re-register difficulty/arrow icons if lost during style rebuild.
    // The decoration layers themselves come back via the serialized
    // style (setStyle preserves overlayLayers); icons need to be
    // re-uploaded if dropped.
    if (CONFIG.showDifficulty && !map.hasImage("imba-0")) {
        registerDifficultyIcons();
    }
    if (!map.hasImage(ARROW_ICON_LIGHT_BG_ID) ||
            !map.hasImage(ARROW_ICON_DARK_BG_ID)) {
        registerArrowIcons();
    }
    // After style rebuild (including scheme-driven flavor swap),
    // re-apply the paint tokens so labels and arrow icons match
    // the current scheme. setStyle preserves overlay layers but
    // not their JS-driven paint overrides.
    applyMapPaintForScheme(currentColorScheme());
}

// ============================================================
// PWA update toast (B.7), shows an auto-dismissing toast with a
// "Reload" button when the service worker has a new version waiting.
// ============================================================
//
// PWAs in standalone mode have no built-in reload affordance, the
// user would otherwise have to swipe up from the app switcher and
// reopen, which is meaningful friction. The Reload button calls
// skipWaiting on the waiting SW (via postMessage) then reloads on
// the controllerchange event, all inside the existing standalone
// window. One tap, no app close/reopen.
//
// Design intentionally simple:
//   * No dismiss button.
//   * Toast auto-dismisses after ~15s if the user doesn't interact.
//   * No localStorage tracking of dismissals, every page load with
//     a waiting SW shows the toast fresh.
//
// Why no per-version dismissal: a user who ignores the toast still
// gets the update naturally on next app close+reopen (browser default
// SW lifecycle: waiting SW activates as soon as all old-SW clients
// close). So "ignore for now" is a valid path that doesn't strand
// them on a stale version. The toast just nudges them to update
// sooner if they want to without leaving the app.
//
// CACHE_VERSION (content-hashed at build time) ticks on any deploy,
// code, data, PMTiles, icons, so this fires for any rebuild.
if (CONFIG.pwa && "serviceWorker" in navigator) {
    let _hasShownUpdateToast = false;

    // Was this page navigation already controlled by a service worker?
    // Sampled by index.html's inline script BEFORE register(), false on a
    // first/uncontrolled visit, true on a return visit. We gate the update
    // toast on this instead of a live navigator.serviceWorker.controller read
    // because sw.js's clients.claim() sets controller mid-first-load, and the
    // controllerchange/statechange ordering it races with is browser-specific
    // (Firefox lands controllerchange first, wrongly tripping the live check on
    // a first install; Chrome/Safari don't). This sampled flag is race-free.
    const wasControlled = !!window.__swControlledAtLoad;

    function showSwUpdateToast(reg) {
        // Suppress within the current page load only. A future page
        // load that finds a waiting SW will show the toast again,
        // there's no persistent dismissal flag.
        if (_hasShownUpdateToast) return;
        _hasShownUpdateToast = true;
        showToast("Updated map available.", {
            // Persistent: stays until the rider taps Reload or Later.
            // Auto-dismiss would be wrong here, a 15s window means
            // any rider whose attention is elsewhere when the toast
            // fires never knew there was an update. Updates carry no
            // criticality signal, so we default to "make sure they
            // see it" and let them defer via Later if mid-task. The
            // _hasShownUpdateToast guard above prevents re-showing
            // in the same session after dismissal; next page load
            // re-detects the waiting SW and surfaces the toast
            // again, matching the Gmail / Google Docs pattern.
            //
            // Two labeled actions (Reload + Later) instead of
            // Reload + ×. Both choices are explicit text, reads
            // as a binary decision rather than "do this thing or
            // hit the close icon". showToast suppresses its auto-×
            // when 2+ explicit actions are present.
            persistent: true,
            actions: [{
                label: "Later",
                onClick: () => {
                    // No-op, showToast dismisses after any action's
                    // onClick. The toast will re-surface next page
                    // load if the SW is still waiting.
                },
            }, {
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
                        // between toast-show and click. Just reload,
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
        if (reg.waiting && wasControlled) {
            showSwUpdateToast(reg);
        }
        // Case B: a new SW is discovered during this session.
        reg.addEventListener("updatefound", () => {
            const installing = reg.installing;
            if (!installing) return;
            installing.addEventListener("statechange", () => {
                // "installed" + the page was already SW-controlled at
                // load means this is an UPDATE (not a first install).
                // First installs shouldn't show the reload toast,
                // there's nothing to reload from. wasControlled is
                // sampled before clients.claim() can fire, so unlike a
                // live controller read it can't be tripped by claim on
                // a first install (the Firefox-only false toast).
                if (installing.state === "installed" && wasControlled) {
                    showSwUpdateToast(reg);
                }
            });
        });
    }

    // Re-register on load so we capture the registration that
    // index.html's inline script created (idempotent, same scope =
    // same registration object). Safer than racing the inline
    // script.
    navigator.serviceWorker.register("sw.js").then(watchForSwUpdate)
        .catch((e) => console.warn("SW update watcher: registration failed", e));

    // Nudge the active SW to resume an unfinished background precache.
    // The browser can terminate the worker mid-precache (Chrome ~30 s
    // idle, Safari sooner) and `install` never re-fires for that
    // version, without this ping a rider could keep a permanently
    // truncated offline cache (UI assets present, multi-MB PMTiles
    // missing) until the next deploy. The SW's handler is a no-op
    // one cache.match once the precache-complete sentinel is written.
    navigator.serviceWorker.ready.then((reg) => {
        if (reg.active) reg.active.postMessage({ type: "RESUME_PRECACHE" });
    }).catch(() => { /* no active SW, nothing to resume */ });
}

// ============================================================
// PWA Install, promoted out of About into a dedicated Options row.
// Per-map opt-out via CONFIG.pwaInstallPrompt (default true). Two modes:
//
//   pwaInstallPrompt: true (default), show-both strategy: register
//     beforeinstallprompt without preventDefault so Chrome's native
//     mini-infobar still appears, AND show our custom Install row in
//     the Options overlay as a persistent surface for second-visit
//     installs. The flow is described in the second mode block below.
//
//   pwaInstallPrompt: false (per-map opt-out), DO NOT register
//     beforeinstallprompt at all. This silences the Chrome console
//     warning ("Banner not shown: beforeinstallpromptevent.preventDefault()
//     called...") that fires when a handler is registered but never
//     calls prompt(). Chrome still shows its native mini-infobar /
//     omnibox install icon on its own (browser behavior, not ours),
//     and Chrome's three-dot menu still has "Install app". Our custom
//     Install button is hidden everywhere, this is the "no install
//     promotion on this map" mode (use for personal/family maps where
//     install nagging would be unwanted).
//
//   pwaInstallPrompt: true, show-when-armed strategy:
//     * Register beforeinstallprompt without preventDefault so
//       Chrome's native mini-infobar still appears, AND stash the
//       event so the button click can call prompt() on it.
//     * The Install button is hidden until beforeinstallprompt
//       fires; once fired, it's revealed and clicking it opens
//       Chrome's native install dialog. Click ALWAYS works because
//       the button only exists when there's an armed prompt to
//       call. No fallback-hint surprise.
//     * Riders on a low-engagement first visit don't see the
//       button until they've poked around enough for Chrome's
//       heuristic to decide the page is "real PWA worthy"
//       (~30 s of interaction). Chrome's three-dot menu has a
//       browser-provided "Install app" option as the always-
//       available alternative.
//     * iOS Safari (no beforeinstallprompt) gets the static
//       Add-to-Home-Screen instructions hint instead of a button.
//     * On 'appinstalled' (any UI triggered it), hide the row in
//       this tab for immediate feedback.
//
// CONFIG.pwa still gates the entire block, maps without PWA support
// at build time skip all of this.
// ============================================================
if (CONFIG.pwa && CONFIG.pwaInstallPrompt) {
    let deferredInstallPrompt = null;
    // Two signals, no persistent state of our own:
    //
    //   standalone, definitive "currently running as the PWA";
    //                short-circuits everything (no install UI ever).
    //   beforeinstallprompt, Chrome's authoritative "the PWA is
    //                installable AND not currently installed" signal.
    //                Its firing un-hides our Install button; its
    //                silence (and the HTML default `hidden` class
    //                on #install-btn) keeps the button hidden.
    //                Uninstall is handled automatically: Chrome
    //                re-fires beforeinstallprompt on the rider's
    //                next visit after they remove the app.
    //
    // The button is intentionally tied 1:1 to a working native
    // prompt: the click ALWAYS calls Chrome's install dialog. No
    // "armed-but-might-not-work" state, no fallback-text branch.
    // If Chrome's engagement heuristic hasn't tripped yet, the
    // button is hidden, riders can fall back to Chrome's three-
    // dot menu (browser-provided, always available for installable
    // apps).
    //
    // iOS Safari lacks beforeinstallprompt entirely; we show the
    // manual Add-to-Home-Screen hint instead, which is just static
    // text (no programmatic install API on iOS).
    const standalone = window.matchMedia("(display-mode: standalone)").matches;
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

    function revealInstallSection(showButton) {
        const section = document.getElementById("sheet-install-section");
        const btn = document.getElementById("install-btn");
        if (btn) btn.classList.toggle("hidden", !showButton);
        if (section) section.classList.toggle("hidden", !showButton);
    }

    function setInstallButtonEnabled(enabled) {
        const btn = document.getElementById("install-btn");
        if (!btn) return;
        btn.disabled = !enabled;
        btn.classList.toggle("is-disabled", !enabled);
    }

    if (!standalone) {
        window.addEventListener("beforeinstallprompt", (e) => {
            // NOTE: deliberately NOT calling e.preventDefault(). That
            // lets Chrome's native mini-infobar appear (free, one-shot
            // install affordance). We stash the event so the click
            // handler can call prompt() on it, Chrome allows both
            // UIs as long as we eventually call prompt() (which
            // silences the "page must call prompt()" warning).
            deferredInstallPrompt = e;
            revealInstallSection(true);
            setInstallButtonEnabled(true);
        });
    }

    // 'appinstalled' fires regardless of which UI triggered the install
    // (mini-infobar, omnibox icon, our button). Hide the install UI in
    // this tab so the rider sees immediate feedback that the install
    // succeeded.
    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        revealInstallSection(false);
    });

    document.addEventListener("DOMContentLoaded", () => {
        // Wire the install button. The HTML keeps it `hidden` by
        // default; beforeinstallprompt above un-hides it when fired.
        const installBtn = document.getElementById("install-btn");
        if (installBtn) {
            installBtn.addEventListener("click", async () => {
                if (!deferredInstallPrompt) return;
                setInstallButtonEnabled(false);
                deferredInstallPrompt.prompt();
                const result = await deferredInstallPrompt.userChoice;
                if (result.outcome === "accepted") {
                    revealInstallSection(false);
                }
                // Either way, the BeforeInstallPromptEvent is
                // single-use. Discard it; if Chrome re-fires the event
                // later (its own heuristics) the listener above will
                // re-arm.
                deferredInstallPrompt = null;
            });
        }

        // iOS Safari: show the install row with the platform-specific
        // help text swapped in ("Tap Share, then Add to Home Screen"
        // instead of the Android-side "Install locally for offline
        // use."). iOS has no programmatic install API, the actual
        // flow lives in the browser chrome (Share → Add to Home
        // Screen), so the row is tagged .is-static to suppress the
        // tap-target affordance. The icon stays for visual consistency
        // with Android (riders see the same "this is the install
        // option" cue regardless of platform).
        if (isIOS && !standalone) {
            const installBtn = document.getElementById("install-btn");
            if (installBtn) {
                installBtn.classList.add("is-static");
                const help = installBtn.querySelector(".opt-action-help");
                if (help) {
                    help.innerHTML = "Tap <strong>Share</strong>, "
                                   + "then <strong>Add to Home Screen</strong>.";
                }
            }
            revealInstallSection(true);
        }
    });
}

// ============================================================
// Off-screen location indicator
// ============================================================
// Distance helper that takes [lng, lat] tuples instead of four scalars
// the off-screen indicator code naturally has lngLat objects/arrays
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

// Compact paired gain/loss display for route stats. Either or both
// values may be null (unknown / not computed). Returns "" if neither
// is present so the caller can omit the elevation portion entirely.
//
// Format: "↑NNN / ↓NNN ft", the unit is shared across both numbers
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
        // viewport inside maxBounds, the user ends up at the viewport
        // edge rather than dead center. We detect that post-pan and
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
                    `${CONFIG.name} area. The map can't pan further.`);
            }

            // Resume follow-me tracking. We're already at the user's
            // position (the flyTo above just got us there), so any
            // auto-pan MapLibre wants to do is a no-op, but we do
            // want subsequent per-fix easeTo updates to keep us
            // tracking. Set the same flags the Locate-button click
            // handler sets for a re-engagement, then trigger() if
            // we're not already active. (No trackuserlocationstart
            // handler arms these any more; the click handler is the
            // single source of truth, see the state-machine
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
    // edge accounts for safe-area-inset-bottom (notch / home bar).
    // The FAB stack at bottom-right (Search alone) is corner-
    // localized so the standard 48px edgeMargin keeps the indicator
    // clear. The TOP-RIGHT FAB stack (Locate + Options) needs more
    // headroom: 12px inset + two 48px FABs + 10px gap + 12px buffer
    // = 130px from the top edge to clear both buttons. The brand at
    // top-left and the highlight chip at top-center are handled by
    // the same standard 48px edgeMargin.
    const cs = getComputedStyle(document.documentElement);
    const safeBottom = parseFloat(cs.getPropertyValue("--safe-bottom")) || 0;
    const edgeMargin = 48;
    const fabStackTopReserve = 130;
    const xLeft = edgeMargin;
    const xRight = w - edgeMargin;
    const yTop = fabStackTopReserve;
    const yBottom = h - safeBottom - edgeMargin;

    // Degenerate rectangle (canvas too short for the reserve), skip.
    if (xRight <= xLeft || yBottom <= yTop) {
        if (el) el.classList.add("hidden");
        return;
    }

    // Hide the indicator when the dot is actually visible on the
    // canvas. This uses the TRUE canvas bounds (0..w, 0..h), NOT the
    // inset rectangle the clamp uses below: a dot sitting inside the
    // edge margin or under the top FAB reserve is still visible to the
    // rider, so drawing a pointer on top of it is wrong. Matches the
    // off-screen definition already used by maybeShowOffScreenToast and
    // the post-pan check in attachOffScreenIndicatorHandler.
    if (point.x >= 0 && point.x <= w &&
        point.y >= 0 && point.y <= h) {
        if (el) el.classList.add("hidden");
        return;
    }

    // The element is pre-rendered in index.html, so el is always
    // truthy here. The click listener is attached once at app init
    // (see attachOffScreenIndicatorHandler below), historically the
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

    // Distance the dot sits BEYOND the screen edge, "how far off-
    // screen", not its distance from the map center. Walk the same
    // center→dot ray out to where it crosses the TRUE viewport edge
    // (0..w, 0..h), unproject that pixel, and measure the ground
    // distance from there to the dot. So a dot just past the edge
    // reads ~0 and grows as it moves further away, independent of the
    // current zoom. (The arrow above is clamped to the inset rectangle
    // for FAB clearance; the distance uses the true edge so the band
    // between them isn't counted as off-screen.)
    let tEdge = Infinity;
    if (cosA > eps)  tEdge = Math.min(tEdge, (w - cx) / cosA);
    if (cosA < -eps) tEdge = Math.min(tEdge, (0 - cx) / cosA);
    if (sinA > eps)  tEdge = Math.min(tEdge, (h - cy) / sinA);
    if (sinA < -eps) tEdge = Math.min(tEdge, (0 - cy) / sinA);
    const edge = map.unproject([cx + cosA * tEdge, cy + sinA * tEdge]);
    const dist = haversineDistance([edge.lng, edge.lat], userLocation);

    // mdi:arrow-up-bold (Apache 2.0, Pictogrammers), a chunky
    // shafted arrow that reads as a directional pointer rather
    // than a location marker. Earlier this was the Unicode
    // triangle ▲ (U+25B2), which had a 3-fold rotational
    // symmetry problem: at rotations like 60° / 120° / 180° the
    // base corners read as visually equal to the apex, making
    // the pointing direction ambiguous at small sizes. The
    // shafted arrow's clear head + tail kills that ambiguity.
    // Apex is at the top of the 24×24 viewBox so the existing
    // bearing-to-rotation math (0° = north = up) is unchanged.
    el.innerHTML = `<span class="off-screen-indicator-arrow" style="transform:rotate(${degrees}deg)">`
        + `<svg viewBox="0 0 24 24" aria-hidden="true">`
        + `<path d="M14,20H10V11L6.5,14.5L4.08,12.08L12,4.16L19.92,12.08L17.5,14.5L14,11V20Z"/>`
        + `</svg>`
        + `</span>`
        + `<span class="off-screen-indicator-dist">${formatDistance(dist)}</span>`;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.style.display = "flex";
}

// ============================================================
// Toast notifications
// ============================================================
// Show a toast. Two forms:
//
//   showToast("message"): transient hint; auto-dismisses after 4s.
//                                    The default for off-screen indicator
//                                    nudges, geolocation errors, copy
//                                    confirmations, etc.
//
//   showToast("message", {         persistent: stays until an action
//       persistent: true,            button (or the auto-✕ fallback) is
//       actions: [...],              tapped. Used for the SW-update
//       onDismiss: fn,               toast (Reload + Later) and any
//   })                               other notice that must be
//                                    acknowledged.
//
// The toast element is rebuilt on each call (not just text-replaced) so
// switching between forms works without stale buttons left behind.
//
// Auto-✕ rules:
//   - Transient toasts (no `persistent`) never get a ✕, they
//     auto-dismiss on the timeout.
//   - Persistent toasts with 0 or 1 explicit actions get an auto-✕ so
//     the rider always has a dismiss path.
//   - Persistent toasts with 2+ explicit actions skip the auto-✕,
//     the caller's secondary action (e.g. "Later") already provides a
//     labeled dismiss; adding ✕ on top would be a redundant third
//     dismiss with an inconsistent visual treatment.
// A persistent toast displaced by a TRANSIENT one is stashed here and
// re-shown once the transient dismisses. There's a single #map-toast
// element, so without this any passing hint (geolocate error, copy
// confirmation, "outside the area" nudge) destroyed the SW-update
// toast's Reload/Later buttons, and since the update check runs once
// per page load, the prompt was gone for the session (a real cost on
// a field device whose "next launch" may be offline). A NEW persistent
// toast replaces the stash, latest persistent wins.
let _displacedPersistentToast = null;

function _restoreDisplacedToast() {
    if (!_displacedPersistentToast) return;
    const [msg, dOpts] = _displacedPersistentToast;
    _displacedPersistentToast = null;
    showToast(msg, dOpts);
}

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
    // Displacement bookkeeping, see _displacedPersistentToast above.
    if (persistent) {
        _displacedPersistentToast = null;
    } else if (el._persistent && el.classList.contains("visible")) {
        _displacedPersistentToast = el._toastArgs;
    }
    el._persistent = persistent;
    el._toastArgs = [message, opts];
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
                // Dismiss after action, caller can re-show if they want
                // a different post-action state (most won't).
                dismissToast();
            }
        });
        el.appendChild(btn);
    }

    // Auto-dismiss "×", only added when persistent AND there's
    // either zero or one explicit action. With 2+ actions the
    // caller has already provided a labeled secondary (e.g.
    // "Later"), so an additional × would be a third dismiss
    // affordance with a different visual treatment, which reads as
    // inconsistent. Single-action persistent toasts still get the ×
    // so the rider isn't trapped with a button they don't want to
    // tap.
    if (persistent && actions.length < 2) {
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
        el._timeout = setTimeout(() => {
            el.classList.remove("visible");
            el.classList.add("hidden");
            // Bring back a persistent toast this transient displaced.
            _restoreDisplacedToast();
        }, 4000);
    }
}

// Hide any visible toast immediately. Used by interactions that act on
// the toast's instruction (e.g. tapping the off-screen indicator after
// being told to do so), without this, the user sees their action AND
// the same nagging toast for up to 4 s, which reads as "didn't work".
function dismissToast() {
    const el = document.getElementById("map-toast");
    if (!el) return;
    clearTimeout(el._timeout);
    el._timeout = null;
    el.classList.remove("visible");
    el.classList.add("hidden");
    // If the dismissed toast was a transient that displaced a
    // persistent one, restore it. Dismissing the persistent itself
    // (action button / ×) finds the stash empty, showToast cleared
    // it when the persistent was displayed.
    _restoreDisplacedToast();
}

// ============================================================
// Start
// ============================================================
init();
