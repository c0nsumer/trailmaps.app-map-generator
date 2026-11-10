# Deployment

How to host the build output on a static server, what cache headers to set, how
the PWA install flow works in production, and what to expect from the service
worker over the long term.

## Contents

- [Quick deploy: any static file server](#quick-deploy-any-static-file-server)
- [Deploying by other means](#deploying-by-other-means)
- [Caddy configuration](#caddy-configuration)
- [Content Security Policy](#content-security-policy)
- [Service worker update cadence](#service-worker-update-cadence)
- [PWA and offline support](#pwa-and-offline-support)
- [PMTiles and HTTP Range requests](#pmtiles-and-http-range-requests)
- [Open Graph and share previews](#open-graph-and-share-previews)

## Quick deploy: any static file server

Copy the `build/<slug>/` directory to any static file server. The server must
support HTTP Range requests for PMTiles. Caddy, nginx, and Apache all do. No
special CORS or rewrite rules are needed; a standard `file_server` config is
sufficient.

Serve it over HTTPS. Without it the map draws, but Locate, offline use, and
Install don't work. The map can live at any path (eg: `example.org/maps/pinehollow/`),
as nothing in it is tied to the site root.

`scripts/build.py` produces production-quality output by default; no flag is
needed. The output includes minified `app.js` / `style.css`, a content-hashed
service worker, and a trimmed font set:

```bash
python scripts/build.py configs/<slug>/<slug>.yaml
# → build/<slug>/ is ready to ship
```

The convenience wrapper `tools/build_and_deploy.sh` handles
validate-build-rsync in one shot for the **SSH/rsync** case:

```bash
./tools/build_and_deploy.sh <slug>
```

The wrapper reads the deploy destination from the `TRAILMAPS_DEPLOY_DEST`
environment variable. Set it in your shell rc:

```bash
# in ~/.zshrc or ~/.bashrc
export TRAILMAPS_DEPLOY_DEST=user@host:/var/www/your-maps
```

Override per-run with `--dest <ssh-path>`. If neither the env var
nor `--dest` is set, the wrapper errors out with a clear hint
rather than silently shipping to a wrong (or empty) target.

## Deploying by other means

The wrapper above assumes SSH/rsync. For any other static-host deploy
mechanism, run `python scripts/build.py <config>`. Then ship the resulting
`build/<slug>/` tree with whichever tool fits your host. The output is the
same regardless of how you transport it.

```bash
# Build once (production-quality by default, no flag needed)
python scripts/build.py configs/<slug>/<slug>.yaml

# Then ship. Pick one:

# AWS S3 (the --exclude flags skip build-only files, see below)
aws s3 sync build/<slug>/ s3://your-bucket/<slug>/ --delete \
  --exclude '*.sig' --exclude '*.src.geojson' --exclude '*.tmp'

# Netlify CLI
netlify deploy --dir=build/<slug> --prod

# GitHub Pages (using gh-pages npm helper)
npx gh-pages -d build/<slug>

# Cloudflare Pages (wrangler CLI)
wrangler pages deploy build/<slug>

# Any SFTP / FTP / WebDAV / manual host:
# point your tool at build/<slug>/ as the source directory
```

The build directory also holds build-only files that must never reach the
server. Exclude `*.sig`, `*.src.geojson`, and `*.tmp` from every upload. The
rsync wrapper already does this. With Netlify, GitHub Pages, Cloudflare Pages,
or a manual upload, delete those files from a copy of the directory first.

Every static host that serves HTTP Range requests properly will work. That
includes S3, Netlify, Cloudflare Pages, GitHub Pages, nginx, Apache, and
Caddy. The Caddy-specific config below is one example of headers you may want
to set on whichever host you use. The same intent translates to most server
configs: cache JS/CSS for a day, revalidate HTML, allow Range on PMTiles.

The build also writes precompressed `.gz` and `.br` (Brotli) sidecars next to
each text asset. Brotli is the encoding every modern browser accepts,
including Safari, which never accepts zstd. Caddy, nginx, and Apache can
serve the sidecars directly (see the `precompressed` block below). Managed hosts like S3, Netlify, Cloudflare
Pages, and GitHub Pages cannot; they compress on their own terms and ignore
sidecar files. If you deploy to one of those, build with `--no-precompress`
so the sidecars are not generated and uploaded for nothing. A `--no-precompress` build also deletes the sidecars left by an earlier build.

## Caddy configuration

Recommended Caddy config serving maps at `mytrailmaps.com`:

```caddyfile
mytrailmaps.com {
    root * /var/www/mytrailmaps.com
    encode zstd gzip

    # Serve the build's precompressed .br/.gz sidecars when the client
    # accepts them. Without `precompressed`, Caddy ignores the sidecars
    # and re-compresses on the fly at a lower level - and `encode` skips
    # types outside its default allowlist entirely, which includes
    # .geojson and the font .pbf glyphs. Sidecars are never generated
    # for .pmtiles, so HTTP Range slicing is unaffected. Order matters:
    # it is Caddy's preference list, and br must win when both are
    # accepted.
    file_server {
        precompressed br gzip
    }

    # nosniff keeps a browser from guessing a file's type. The referrer
    # policy keeps the map's path out of requests to other sites. The
    # permissions policy limits geolocation to this site's own pages.
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(self)"
    }

    # Cache static assets for a day: long enough for a ride, short enough that
    # rebuilds propagate. app.js and style.css keep stable filenames, so a
    # stale cache clears when the entry expires or the service worker swaps it.

    # The explicit *.geojson / *.webmanifest / *.gpx globs matter: those
    # extensions do not end in .json, so without them the trail data and
    # manifest would ship with no Cache-Control header at all.
    @immutable path *.pmtiles *.pbf *.js *.mjs *.css *.woff2 *.png *.webp *.ico *.svg *.json *.geojson *.webmanifest *.gpx

    # Pick one of these two:

    # Production use: one-day cache.
    header @immutable Cache-Control "public, max-age=86400"

    # Development / map testing: no caching.
    # header @immutable Cache-Control "no-store"

    # Revalidate the entry point and service worker on every load so new
    # builds are picked up promptly. The `*/` forms cover a map served
    # from a subdirectory (`/ramba/`, `/ramba/index.html`, `/ramba/sw.js`).
    # This block must come after @immutable: sw.js matches `*.js` too, and
    # the later header wins.
    @nocache path / /index.html */ */index.html */sw.js
    header @nocache Cache-Control "no-cache"

    # An SVG opened by its own URL is a document, and a script inside it
    # would run on this site. The map only loads SVGs as images, so they
    # need no script.
    @svg path *.svg
    header @svg Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; img-src data:"

    handle_errors {
        respond "{err.status_code} {err.status_text}"
    }

    log {
        output file /var/log/caddy/access.log
    }
}
```

The same logical setup translates directly to nginx or Apache. The required
pieces are HTTPS, Range request support on `.pmtiles`, `Cache-Control: no-cache`
on every map's `index.html` and `sw.js` (in a subdirectory too), `.mjs` files
served as `text/javascript`, and a sane TTL on everything else. The three
security headers and the SVG policy are recommended, and none of them changes
how the map behaves. The vendored MapLibre and uqr scripts are module
scripts. A browser refuses them when the server sends another MIME type.

## Content Security Policy

The trail data comes from OpenStreetMap, which anyone can edit, and every map
on a site shares one origin. The app escapes everything it shows, but if that
ever failed, a script injected through one map's data would run with whatever
a rider had granted the site (location, mostly). So the build writes a
Content Security Policy into each map's `index.html` as a `<meta>` tag, right
after the charset:

- Scripts: the map's own files, plus the page's four inline scripts by SHA-256
  hash. Nothing from another host, no `eval`.
- Fetches, fonts, and the manifest: the map's own origin only.
- Images: the map's own origin, plus `data:` URLs (the rating icons and the
  map control icons).
- Workers: the map's own origin, plus `blob:` (the lanes plugin starts its
  worker that way).
- Styles: the map's own stylesheets, plus `style=` attributes (MapLibre and
  the popups set them).
- No `<base>`, no form posts.

You don't need to do anything for this to work. The build computes the hashes
from the finished page, so they're right for every map and every rebuild.

It matters when you add something to a built page yourself (eg: an analytics
snippet), as the browser will block whatever the policy doesn't list:

- An inline script: put it in a file next to `index.html` and load it with
  `<script src>`. (Or add its hash to `script-src`. Chrome prints the hash in
  the console when it blocks the script.)
- A script or beacon on another host: add that host to `script-src` and
  `connect-src`, and to `img-src` if it uses a tracking pixel.
- A web font from a CDN: host the font files with the map, or add the CSS
  host to `style-src` and the font host to `font-src`.
- An inline `<style>` block: move it to a `.css` file.

The same goes for a host or proxy that changes pages on the way out. A feature
that injects scripts (eg: Cloudflare's Rocket Loader, email obfuscation, or
auto-injected analytics, or a host's banner or snippet injection) gets blocked
by the policy. One that rewrites the page's own inline scripts changes their
hashes, and the map stops booting. Turn those features off for the map's path.
Plain file serving is unaffected.

Two things to keep in mind when editing the tag. A rebuild writes a fresh one,
so the edit has to be reapplied after each build (a script that patches the
build output is the usual way). And a `Content-Security-Policy` header sent by
your server is applied on top of the tag: it can tighten the policy, but it
can't loosen it.

The tag doesn't cover framing (a meta policy can't carry `frame-ancestors`),
so whether other sites may embed the map is up to your server's headers. It
also doesn't cover an SVG opened by its own URL, which is what the `@svg`
block in the Caddy config is for.

As of this writing (2026-Nov-10) this is tested in Chromium only. Firefox
before 108 doesn't know `style-src-attr` and would block the `style=`
attributes, which breaks the popup layout there.

## Service worker update cadence

Deploying a new build changes `CACHE_VERSION`, a hash of every file the build
produced for the map. Any code, data, or asset change therefore produces a new
service worker. Riders pick it up two ways:

- **On a page load or refresh**, the browser re-fetches `sw.js`. If it differs,
  the browser installs the new worker.
  If the new core files finish caching within 60 seconds of the page load,
  the app swaps silently. It shows the top loading bar, reloads once, and
  restores the camera and highlight. A one-shot "Map updated" toast follows.
  If the core files take longer than 60 seconds, the app shows an
  "Updated map available" toast with a Reload button instead.
- **Without a refresh**, the browser runs its own update check about every 24
  hours. That is standard service-worker behavior, outside the framework's
  control. A left-open tab generally sees the toast within a day of a deploy.
  The app also re-checks for an update each time the tab returns to the
  foreground. A mid-session update always uses the toast, never the silent swap.

A rider who closes and re-opens the map gets the new build on the next launch.

## PWA and offline support

Every generated map is a fully installable Progressive Web App that works
offline after the first visit. This is always on. There is no config key to
turn it off.

**How it works:**

1. **Service worker.** Each build ends by generating a service worker (`sw.js`)
   with a precache list of every file the build produced for the map. Other
   files in the output directory are not precached. On first visit, all assets
   are cached. Subsequent visits and offline use are served entirely from the
   cache.

2. **PMTiles offline.** The service worker handles HTTP Range requests for
   `.pmtiles` files by slicing from the cached full file. Map tiles work fully
   offline.

3. **Install row.** An "Install this app" action row appears in the Options
   overlay on supported browsers. On iOS, tapping it reveals a Share to
   Add-to-Home-Screen hint instead of firing an install prompt.

4. **Cache updates.** Each build produces a unique cache version. On the next
   visit after a rebuild, the new service worker installs and fills its own cache in the
   background, then waits. It activates after the silent swap, a Reload tap,
   or the next cold launch. Then it deletes the old caches.

The PWA is transparent: the map works identically in a regular browser tab,
and offline capability layers on top.

### Install affordance behavior by platform

Install promotion is always on.

| Platform | Behavior |
|---|---|
| Chrome / Android | Native mini-infobar appears (we don't call `preventDefault`); custom Install row in Options is also visible as a persistent fallback |
| iOS Safari | Install row in Options opens manual Add-to-Home-Screen instructions |
| Other browsers | Install row hidden (no support) |

## PMTiles and HTTP Range requests

PMTiles relies on HTTP Range requests to read tile chunks instead of downloading
the entire archive. This is critical for fast first-load performance. Most
trail-map basemap PMTiles are under 10 MB.
Rendering any given view needs only a few hundred KB of tile chunks.

Verify Range support manually before deploying a new server config:

```bash
curl -H "Range: bytes=0-1000" -I https://yourserver/path/to/basemap.pmtiles
```

The response should include `206 Partial Content` and `Content-Range: bytes
0-1000/<total>`. If you see `200 OK` with the full body, the server does not
honor Range requests. Every tile read will then re-fetch the whole archive.

The runtime detects this on first cold load. It prints
`[mtb-map] HTTP Range requests not honored...` to the browser console (DevTools
to Console) with diagnostic detail. After the service worker caches the full
file, the warning stops firing. That is correct behavior, but every new visitor
still pays the slow first-load cost.

## Open Graph and share previews

Every generated map emits Open Graph and Twitter Card meta tags referencing the
icon and title. When the map URL is shared on
Slack, Discord, iMessage, Android Messages, Facebook, X, or any other platform
that consumes OG tags, the preview card shows:

- The map title.
- The map's icon (one of the generated PWA icons).
- A short description (the `welcome.body` first paragraph, trimmed to a
  sensible length, or the map title when there is no `welcome.body`).

No separate config gate is needed; the meta tags are inert until something
fetches the URL.

The Share link and QR code use the page's `og:url` tag as their base address when it exists. Without it, they use the address the page was loaded from.
