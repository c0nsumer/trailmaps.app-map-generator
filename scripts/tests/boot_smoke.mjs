// Headless boot driver for test_boot_smoke.py.
//
// Usage: node boot_smoke.mjs <url> <chromium-executable>
//
// Opens a built map in headless Chromium and prints one JSON object
// describing how boot went. The pass/fail judgment lives in the Python
// test; this script only observes. Exit 2 means the browser could not
// launch (the test reports that distinctly from a boot failure).

import { chromium } from "playwright-core";

const [url, executablePath] = process.argv.slice(2);
const IDLE_TIMEOUT_MS = 20000;

let browser;
try {
  // The swiftshader flags are required: without a software GL stack
  // MapLibre gets no WebGL context and the map never boots.
  browser = await chromium.launch({
    executablePath,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
  });
} catch (err) {
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(2);
}

const pageErrors = [];
const consoleErrors = [];
let result;
try {
  // The service worker would add caching state that has nothing to do
  // with whether boot works.
  const context = await browser.newContext({
    serviceWorkers: "block",
    viewport: { width: 390, height: 700 },
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    const where = m.location().url || "";
    // The smoke build skips basemap and terrain, so their archives and
    // tiles 404. That is expected; any other error is not.
    // pmtiles.js reports it as "Bad response code: 404" with no URL.
    const missingArchive = /404|Failed to load resource/.test(text) &&
      /\.pmtiles|pmtiles\.js|\/tiles\/|\/\d+\/\d+\/\d+/.test(`${where} ${text}`);
    if (!missingArchive) consoleErrors.push(text);
  });

  await page.goto(url, { waitUntil: "load" });

  // `let map` at the top level of app.js is a script-scope binding, so
  // page.evaluate can see it even though it is not on window.
  await page
    .waitForFunction(() => typeof map !== "undefined" && map.loaded && map.loaded(), null, {
      timeout: IDLE_TIMEOUT_MS,
    })
    .catch(() => {});
  await page
    .evaluate(
      (ms) =>
        new Promise((resolve) => {
          if (typeof map === "undefined") return resolve();
          const t = setTimeout(resolve, ms);
          map.once("idle", () => {
            clearTimeout(t);
            resolve();
          });
        }),
      IDLE_TIMEOUT_MS,
    )
    .catch(() => {});

  // The modal opens a moment after idle and swallows clicks beneath it,
  // so give it a short window to appear before dismissing it.
  const close = page.locator("#welcome-modal-close");
  if (await close.waitFor({ state: "visible", timeout: 2000 }).then(() => true, () => false)) {
    await close.click().catch(() => {});
  }

  const state = await page.evaluate(() => {
    const shown = (el) => !!el && !el.hidden && !el.classList.contains("hidden");
    return {
      panelPresent: shown(document.getElementById("route-panel")) ||
        shown(document.getElementById("key-panel")),
      optionsFab: !!document.getElementById("toggle-options"),
      zoom: typeof map !== "undefined" ? map.getZoom() : null,
    };
  });

  let finderRows = 0;
  const search = page
    .locator("button")
    .filter({ has: page.getByText("Search", { exact: true }) })
    .first();
  // The Search button lives inside the route panel card, which starts
  // collapsed behind its chip on a phone-width viewport.
  if (!(await search.isVisible())) {
    await page.locator("#route-panel-chip").click({ timeout: 3000 }).catch(() => {});
  }
  if (await search.count()) {
    await search.click({ timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(500);
    finderRows = await page.locator(".finder-row").count();
  }

  result = { pageErrors, consoleErrors, ...state, finderRows };
} finally {
  await browser.close();
}
console.log(JSON.stringify(result));
