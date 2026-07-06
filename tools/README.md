# Tools

Helper scripts for building and deploying trail maps.

## build_and_deploy.sh

A convenience wrapper for the **SSH/rsync** deploy workflow.
Builds and/or rsyncs one or more trail map configs. By default,
processes all YAML configs in `configs/` (excluding the
`configs/reference/` folder of templates).

This is *one* way to deploy, not the only one. The build pipeline
itself (`scripts/build.py`) produces production-quality output by
default. If you deploy via S3, Netlify, GitHub Pages, Cloudflare
Pages, or manual upload, call `python scripts/build.py <config>`
directly and ship the resulting `build/<slug>/` tree with whichever
tool fits your host. See [`docs/deployment.md#deploying-by-other-means`](../docs/deployment.md#deploying-by-other-means)
for recipes.

The deploy destination is read from the `TRAILMAPS_DEPLOY_DEST`
environment variable. Set it in your shell rc (`~/.zshrc` /
`~/.bashrc`):

```bash
export TRAILMAPS_DEPLOY_DEST=user@host:/var/www/your-maps
```

Override per-run with `--dest <ssh-path>`. The script errors out
with a clear hint if neither is set.

### Usage

```bash
# Build and deploy all configs
./tools/build_and_deploy.sh

# Build and deploy specific configs (use YAML filename without extension)
./tools/build_and_deploy.sh ramba
./tools/build_and_deploy.sh ramba stony glacialhills

# Build only (no deploy)
./tools/build_and_deploy.sh --build-only ramba

# Deploy only (skip build, use existing output)
./tools/build_and_deploy.sh --deploy-only ramba

# Re-fetch all remote data (passes --refresh to build.py)
./tools/build_and_deploy.sh --refresh ramba

# Pass extra flags to build.py after a -- separator
./tools/build_and_deploy.sh ramba -- --no-basemap --no-terrain
```

### Options

| Flag | Description |
|------|-------------|
| `--all` | Build and deploy all configs (default if no args given) |
| `--build-only` | Build but skip deploy |
| `--deploy-only` | Deploy existing builds without rebuilding |
| `--validate-only` | Run config validation, no fetch/build/deploy |
| `--refresh` | Pass `--refresh` to `build.py` (re-fetch all OSM and tile data) |
| `--dry-run` | Show what would happen; don't build or transfer |
| `--dest <ssh-path>` | Override deploy destination (default: `$TRAILMAPS_DEPLOY_DEST`) |
| `--skip-ssh-check` | Skip the pre-flight SSH connectivity probe |
| `--` | Separator; everything after this is passed directly to `build.py` |

### Notes

- The YAML filename is used to locate the config, but the `slug` field inside the config determines the build output directory and deploy path. These do not need to match.
- When no configs are specified, every per-map config under `configs/<slug>/<slug>.yaml` is processed; the `configs/reference/` template folder is skipped.
- A summary is printed at the end showing which maps succeeded and which failed.

## clean_config.py

Re-align a production map YAML against the canonical template
(`configs/reference/reference-minimal.yaml` by default). Production configs
accumulate cruft over time as they're hand-maintained: keys reordered,
comments edited, sections renamed, drift from the template's structure.
This tool produces a sibling `<input>-cleaned.yaml` that adopts the
template's structure (section dividers, key ordering, default-value
documentation comments) while preserving every value the production
file explicitly set — and every curator comment along with it.

The original file is never modified. Review the cleaned output and
swap it in manually when satisfied. One behaviour worth knowing
before swapping: live template keys the production file doesn't set
are commented out in the output rather than inherited (so a
custom-route-only map that omits `relations:` never picks up the
template's placeholder ID).

### Usage

```bash
# Default template (configs/reference/reference-minimal.yaml)
python tools/clean_config.py configs/example/example.yaml

# Custom template (e.g. the verbose annotated reference)
python tools/clean_config.py configs/foo/foo.yaml \
    --template configs/reference/reference.yaml

# Custom output path
python tools/clean_config.py configs/foo/foo.yaml -o /tmp/foo-clean.yaml
```

### Behaviour

- Set keys are spliced, not re-serialized: the production file's own
  lines for each key it sets are copied verbatim into the template's
  position for that key (key lines matched against
  `validate_config.KNOWN_KEYS`). Inline comments (`- 20502171 #
  Addison Connector`), comment lines inside a block, and the
  curator's own formatting all survive by construction.
- Full-line comments sitting directly above a set key travel with it.
- A commented-out known-key block (a stashed alternative like
  `# forced_labels: routes` or a whole `#trailheads:` block) replaces
  the template's generic `# key: default` line for that key, so saved
  alternatives keep their place instead of being flattened back to
  the default.
- Other template lines (section dividers, prose comments, commented
  defaults for unset keys, blank lines) pass through verbatim, so
  every supported option stays visible at its default.
- Production keys with no corresponding template line are appended at
  the end under a `# --- Keys not in template ---` header. Catches drift
  in either direction (key the template forgot, or key the curator
  added that the template doesn't model, usually a sign the
  template needs updating too).
- Comments the placement heuristics can't attach anywhere (e.g. a
  commented-out chunk trailing below a set block) are appended under a
  `# --- Unplaced comments carried from the previous file
  (review/relocate) ---` header — misplaced but kept, never lost.
  Relocate them by hand while reviewing the output.

### Verification

After writing, the tool re-parses both files and compares: if the
cleaned output would parse to different data than the original, it
deletes the output and exits non-zero. The gate is always on — the
tool cannot hand back a config that behaves differently. The output
should also pass `validate_config.py` and build via `build.py`; any
gate failure means the cleaner mishandled something, file an issue.
