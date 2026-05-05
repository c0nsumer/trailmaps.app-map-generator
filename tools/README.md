# Tools

Helper scripts for building and deploying trail maps.

## build_and_deploy.sh

Builds and/or deploys one or more trail map configs. By default, processes all YAML configs in `configs/` (excluding the `configs/reference/` folder of templates).

The deploy destination is configured via the `DEPLOY_DEST` variable at the top of the script.

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

# Force re-fetch all data (passes --force to build.py)
./tools/build_and_deploy.sh --force ramba

# Pass extra flags to build.py after a -- separator
./tools/build_and_deploy.sh ramba -- --skip-basemap --skip-terrain
```

### Options

| Flag | Description |
|------|-------------|
| `--all` | Build and deploy all configs (default if no args given) |
| `--build-only` | Build but skip deploy |
| `--deploy-only` | Deploy existing builds without rebuilding |
| `--force` | Pass `--force` to `build.py` (re-fetch all OSM and basemap data) |
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
file explicitly set.

The original file is never modified. Review the cleaned output and
swap it in manually when satisfied.

### Usage

```bash
# Default template (configs/reference/reference-minimal.yaml)
python tools/clean_config.py configs/potoloo/potoloo.yaml

# Custom template (e.g. the verbose annotated reference)
python tools/clean_config.py configs/foo/foo.yaml \
    --template configs/reference/reference.yaml

# Custom output path
python tools/clean_config.py configs/foo/foo.yaml -o /tmp/foo-clean.yaml
```

### Behaviour

- Walks the template line-by-line. When a top-level key line (commented
  `# key:` or uncommented `key:`) corresponds to a known config key
  (sourced from `validate_config.KNOWN_KEYS`), and that key is set in
  the production file, replaces the line (or its multi-line block) with
  the production value formatted to the template's house style.
- Other template lines (section dividers, prose comments, blank lines)
  pass through verbatim.
- Production keys with no corresponding template line are appended at
  the end under a `# --- Keys not in template ---` header. Catches drift
  in either direction (key the template forgot, or key the curator
  added that the template doesn't model — usually a sign that the
  template needs updating too).
- Inline comments in the production file (e.g. `accent_color: auto  #
  logo is B/W`) are NOT preserved. The template's structure wins for
  layout; production's value wins for content.

### Output formatting

A custom YAML dumper handles three PyYAML quirks:

- Multi-line strings get `|` block-literal style (default would
  single-quote with embedded `\n` escapes, ugly + lossy on trailing
  whitespace).
- Dicts are always block-style (default flows single-key dicts like
  `relation_colors: {1234: '#fff'}`).
- Lists go inline when all-scalar (`coordinates: [lon, lat]`,
  `pattern: [1, 1]`); block-style otherwise (`trailheads:\n- name:
  ...`). Matches the template's house style.

### Verification

The cleaned output parses to an identical Python dict as the original
(verifiable with `python -c "import yaml; print(yaml.safe_load(open('a'))
== yaml.safe_load(open('b')))"`), passes `validate_config.py`, and
builds successfully via `build.py`. Any value drift means the cleaner
mishandled something — file an issue.
