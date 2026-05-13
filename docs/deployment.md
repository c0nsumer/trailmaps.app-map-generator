# Deployment

How to host the build output on a static server, what cache headers to set, how
the PWA install flow works in production, and what to expect from the service
worker over the long term.

## Contents

- [Quick deploy: any static file server](#quick-deploy-any-static-file-server)
- [Deploying by other means](#deploying-by-other-means)
- [Caddy configuration](#caddy-configuration)
- [Service worker update cadence](#service-worker-update-cadence)
- [PWA and offline support](#pwa-and-offline-support)
- [PMTiles and HTTP Range requests](#pmtiles-and-http-range-requests)
- [Open Graph and share previews](#open-graph-and-share-previews)

## Quick deploy: any static file server

Copy the `build/<slug>/` directory to any static file server. The server must
support HTTP Range requests for PMTiles (Caddy, nginx, Apache all do). No
special CORS or rewrite rules are needed; a standard `file_server` config is
sufficient.

`scripts/build.py` produces production-quality output by default
(minified `app.js` / `style.css`, content-hashed service worker,
trimmed font set, etc.) — no flag needed:

```bash
python scripts/build.py configs/<slug>/<slug>.yaml
# → build/<slug>/ is ready to ship
```

The convenience wrapper `tools/build_and_deploy.sh` handles
validate-build-rsync in one shot for the **SSH/rsync** case:

```bash
./tools/build_and_deploy.sh <slug>
```

The deploy destination is read from the `TRAILMAPS_DEPLOY_DEST`
environment variable. Set it in your shell rc:

```bash
# in ~/.zshrc or ~/.bashrc
export TRAILMAPS_DEPLOY_DEST=user@host:/var/www/your-maps
```

Override per-run with `--dest <ssh-path>`. If neither the env var
nor `--dest` is set, the wrapper errors out with a clear hint
rather than silently shipping to a wrong (or empty) target.

## Deploying by other means

The wrapper above assumes SSH/rsync. For any other static-host
deploy mechanism, run `python scripts/build.py <config>` and ship
the resulting `build/<slug>/` tree with whichever tool fits your
host. The output is the same regardless of how you transport it.

```bash
# Build once (production-quality by default — no flag needed)
python scripts/build.py configs/<slug>/<slug>.yaml

# Then ship. Pick one:

# AWS S3
aws s3 sync build/<slug>/ s3://your-bucket/<slug>/ --delete

# Netlify CLI
netlify deploy --dir=build/<slug> --prod

# GitHub Pages (using gh-pages npm helper)
npx gh-pages -d build/<slug>

# Cloudflare Pages (wrangler CLI)
wrangler pages deploy build/<slug>

# Any old-school SFTP / FTP / WebDAV / manual host
# — point your tool at build/<slug>/ as the source directory
```

Every static host that serves HTTP Range requests properly will
work (S3, Netlify, Cloudflare Pages, GitHub Pages, nginx, Apache,
Caddy). The Caddy-specific config below is one example of headers
you may want to set on whichever host you use; the same intent
(cache JS/CSS forever, revalidate HTML, allow Range on PMTiles)
translates to most server configs.

## Caddy configuration

Recommended Caddy config serving maps at `mytrailmaps.com`:

```caddyfile
mytrailmaps.com {
    root * /var/www/mytrailmaps.com
    encode zstd gzip
    file_server

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    }

    # Entry point + service worker: always revalidate so users pick up new
    # builds promptly. `/` and `/index.html` are listed separately because
    # Caddy's `path` matcher is exact-match: a request to the bare root has
    # URI path `/` (file_server resolves it to index.html only internally),
    # and an explicit /index.html link bypasses that. Without an explicit
    # Cache-Control on HTML, browsers fall back to heuristic caching (~10%
    # of the file's age), which is unpredictable and almost always wrong
    # for a PWA's entry point. Browsers already cap sw.js at 24h per spec,
    # but explicit no-cache keeps behavior consistent across browsers and
    # ensures every load checks for a new service worker.

    @nocache path / /index.html /sw.js
    header @nocache Cache-Control "no-cache"

    # Cache static assets for 1 day: long enough for a trail ride, short
    # enough that rebuilds propagate quickly. app.js and style.css change
    # every build but keep the same filename (no content hash), so a user
    # on a stale cache won't see new code until the cache entry expires or
    # the service worker swaps assets.

    @immutable path *.pmtiles *.pbf *.js *.css *.png *.webp *.ico *.svg *.json

    # Pick one of these two:

    # Production use: one-day cache.
    header @immutable Cache-Control "public, max-age=86400"

    # Development / map testing: no caching.
    # header @immutable Cache-Control "no-store"

    handle_errors {
        respond "{err.status_code} {err.status_text}"
    }

    log {
        output file /var/log/caddy/access.log
    }
}
```

The same logical setup translates directly to nginx or Apache: the required
pieces are HTTPS, Range request support on `.pmtiles`, `Cache-Control: no-cache`
on `index.html` and `sw.js`, and a sane TTL on everything else.

## Service worker update cadence

Deploying a new build ticks `CACHE_VERSION` (a content-based hash of every
output file), so any code, data, or asset change cuts a new service worker. How
fast riders see it without reloading the page:

- **On any page load or refresh** within the map's scope, the browser fetches
  `sw.js`, byte-compares it, and installs a new SW into the "waiting" state if
  it differs. The page detects this via `updatefound` and shows an "Updated map
  available" toast with a Reload button.
- **Without a refresh**, the browser performs its own automatic update check
  **~every 24 hours**. Per the SW spec, modern browsers cap `sw.js` at a 24h
  staleness threshold regardless of `Cache-Control`, then bypass HTTP cache for
  that fetch: a stale `max-age=86400` response from the origin can't keep the
  old SW pinned past a day. The check fires when the SW handles a fetch event
  after the threshold has elapsed; if a new SW is found, the same "Updated map
  available" toast appears live on the open page.
- **The framework does not call `registration.update()` on a timer.** Update
  cadence is entirely the browser's default behaviour. Riders who keep a tab
  open across days will typically see the toast within a day of any deploy, on
  the next request the SW handles. Riders who close + re-open the map see the
  toast almost immediately on the next launch.

## PWA and offline support

When `pwa: true` (the default), every generated map is a fully installable
Progressive Web App that works offline after the first visit. Set `pwa: false`
to disable the service worker and install UI while still keeping locally bundled
vendor libraries.

**How it works:**

1. **Service worker.** A service worker (`sw.js`) is generated at the end of
   each build with a precache list of every file in the output. On first visit,
   all assets are cached. Subsequent visits and offline use are served entirely
   from the cache.

2. **PMTiles offline.** The service worker handles HTTP Range requests for
   `.pmtiles` files by slicing from the cached full file. Map tiles work fully
   offline.

3. **Install row.** An "Install as an app" action row appears in the Options
   overlay on supported browsers. On iOS, tapping it reveals a Share to
   Add-to-Home-Screen hint instead of firing an install prompt.

4. **Cache updates.** Each build produces a unique cache version. On the next
   visit after a rebuild, the new service worker installs, re-caches all files,
   and activates immediately. Old caches are automatically cleaned up.

The PWA is transparent: the map works identically in a regular browser tab.
Offline capability is purely additive.

### Install affordance behaviour by platform

The `pwa_install_prompt` config key controls install promotion:

| Platform | `pwa_install_prompt: true` (default) | `pwa_install_prompt: false` |
|---|---|---|
| Chrome / Android | Native mini-infobar appears (we don't call `preventDefault`); custom Install row in Options is also visible as a persistent fallback | No `beforeinstallprompt` handler registered; no infobar; Install row hidden |
| iOS Safari | Install row in Options opens manual Add-to-Home-Screen instructions | Install row hidden |
| Other browsers | Install row hidden (no support) | Install row hidden |

## PMTiles and HTTP Range requests

PMTiles relies on HTTP Range requests to read tile chunks instead of downloading
the entire archive. This is critical for fast first-load performance: a typical
trail map's basemap PMTiles is 10 to 30 MB, but only a few hundred KB of tile
chunks are needed to render any given view.

Verify Range support manually before deploying a new server config:

```bash
curl -H "Range: bytes=0-1000" -I https://yourserver/path/to/basemap.pmtiles
```

The response should include `206 Partial Content` and `Content-Range: bytes
0-1000/<total>`. If you see `200 OK` with the full body, Range requests are not
honoured and every tile read will re-fetch the whole archive.

The runtime detects this on first cold load and prints
`[mtb-map] HTTP Range requests not honored...` to the browser console (DevTools
to Console) with diagnostic detail. After the service worker caches the full
file, the warning stops firing (that's correct behaviour, but every new visitor
still pays the slow first-load cost).

## Open Graph and share previews

Every generated map emits Open Graph and Twitter Card meta tags referencing the
icon and title, regardless of `share_button`. When the map URL is shared on
Slack, Discord, iMessage, Android Messages, Facebook, X, or any other platform
that consumes OG tags, the preview card shows:

- The map title.
- The map's icon (one of the generated PWA icons).
- A short description (the `about.description` first paragraph, trimmed to a
  sensible length, or a sober default).

No separate config gate is needed; the meta tags are inert until something
fetches the URL.
