// Dev-only lint for the runtime templates (templates/app.js, sw.js).
// NOT part of the build pipeline: maps build with pure Python and no
// JS toolchain. This exists because the templates ship with no build
// step, so a runtime ReferenceError (a refactor dropping a helper
// another code path still calls) parses fine, ships silently, and
// kills the app at boot. `no-undef` catches that class statically;
// every other rule stays off so a large plain-JS file doesn't
// drown in style opinions.
//
// scripts/tests/test_eslint.py runs this via pytest when Node and
// the installed eslint are available, and skips cleanly otherwise,
// so Python-only contributors are unaffected.
import globals from "globals";

// app.js runs on the page and sw.js in a service worker, so each gets
// only its own scope's globals: `document` in sw.js, or `skipWaiting`
// in app.js, is a ReferenceError at runtime and must fail the lint.
const shared = {
    // Plain <script>-loaded files, not ES modules.
    ecmaVersion: 2022,
    sourceType: "script",
};

// app.js carries a few inline eslint-disable directives for rules this
// minimal config doesn't enable (e.g. the force-reflow
// `overlay.offsetHeight;` expression). They document intent and would
// matter under a broader rule set, so don't warn about them being
// unused here.
const linterOptions = { reportUnusedDisableDirectives: "off" };

export default [
    {
        files: ["templates/app.js"],
        languageOptions: {
            ...shared,
            globals: {
                ...globals.browser,
                // Injected at build time, absent from the template. The
                // vendor globals (maplibregl, pmtiles, basemaps) are
                // declared by the /* global */ comment at the top of
                // app.js instead.
                CONFIG: "readonly",
            },
        },
        linterOptions,
        rules: { "no-undef": "error" },
    },
    {
        files: ["templates/sw.js"],
        languageOptions: {
            ...shared,
            globals: {
                ...globals.serviceworker,
                // Injected at the /*__SW_CONFIG__*/ placeholder.
                SW_CONFIG: "readonly",
            },
        },
        linterOptions,
        rules: { "no-undef": "error" },
    },
];
