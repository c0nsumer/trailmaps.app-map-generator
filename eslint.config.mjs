// Dev-only lint for the runtime templates (templates/app.js, sw.js).
// NOT part of the build pipeline: maps build with pure Python and no
// JS toolchain. This exists because the templates ship with no build
// step, so a runtime ReferenceError (a refactor dropping a helper
// another code path still calls) parses fine, ships silently, and
// kills the app at boot. `no-undef` catches that class statically;
// every other rule stays off so a 10k-line plain-JS file doesn't
// drown in style opinions.
//
// scripts/tests/test_eslint.py runs this via pytest when Node and
// the installed eslint are available, and skips cleanly otherwise,
// so Python-only contributors are unaffected.
import globals from "globals";

export default [
    {
        files: ["templates/app.js", "templates/sw.js"],
        languageOptions: {
            ecmaVersion: 2022,
            // Plain <script>-loaded files, not ES modules.
            sourceType: "script",
            globals: {
                ...globals.browser,
                ...globals.serviceworker,
                // Injected at build time, absent from the templates:
                // CONFIG into app.js and SW_CONFIG into sw.js (the
                // /*__SW_CONFIG__*/ placeholder). The vendor globals
                // (maplibregl, pmtiles, basemaps) are declared by the
                // /* global */ comment at the top of app.js instead,
                // since that dependency is app.js-specific.
                CONFIG: "readonly",
                SW_CONFIG: "readonly",
            },
        },
        // app.js carries a few inline eslint-disable directives for
        // rules this minimal config doesn't enable (e.g. the
        // force-reflow `overlay.offsetHeight;` expression). They
        // document intent and would matter under a broader rule set,
        // so don't warn about them being unused here.
        linterOptions: {
            reportUnusedDisableDirectives: "off",
        },
        rules: {
            "no-undef": "error",
        },
    },
];
