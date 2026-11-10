"""Keep a file's mtime when a rebuild writes the same bytes back.

The production web server derives its ETag from mtime and size, and rsync -a
carries mtime to the server. A build that rewrites an unchanged file (a
sidecar deleted and recompressed, a template re-injected and re-minified)
therefore changes its ETag, and every rider's service worker re-downloads
it. Many stages write into the output directory, and some write the same
file twice (inject, then minify), so a per-site "skip if equal" check would
compare against an intermediate version. Comparing the finished build to
the one that was there before it started catches all of them in one place.
"""

import hashlib
import os

# Big files (the PMTiles archives) are only replaced by a deliberate
# re-extract, never rewritten with equal bytes, and hashing them twice
# would cost more than the rest of a no-change rebuild.
MAX_HASHED_BYTES = 8 * 1024 * 1024


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).digest()


def snapshot(output_dir):
    """Map each small file under output_dir to (mtime_ns, sha256)."""
    seen = {}
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            # The fetched base is never served, and its mtime is a fact
            # of its own: a map with no OSM stamp dates its "Map data"
            # row by it, so a refetch must be allowed to move it.
            if fname.endswith(".src.geojson"):
                continue
            path = os.path.join(root, fname)
            try:
                st = os.stat(path)
                if st.st_size <= MAX_HASHED_BYTES:
                    seen[path] = (st.st_mtime_ns, _digest(path))
            except OSError:
                continue
    return seen


def restore(before):
    """Put back the old mtime on every file whose bytes did not change.

    Files that are gone (a sidecar for a file no longer shipped) stay gone.
    Returns how many mtimes were restored.
    """
    restored = 0
    for path, (mtime_ns, digest) in before.items():
        try:
            st = os.stat(path)
            if st.st_mtime_ns == mtime_ns or st.st_size > MAX_HASHED_BYTES:
                continue
            if _digest(path) == digest:
                os.utime(path, ns=(st.st_atime_ns, mtime_ns))
                restored += 1
        except OSError:
            continue
    return restored
