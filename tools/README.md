# Tools

Helper scripts for building and deploying trail maps.

## build_and_deploy.sh

Builds and/or deploys one or more trail map configs. By default, processes all YAML configs in `configs/` (excluding `example.yaml`).

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
- When no configs are specified, all `.yaml` files in `configs/` are processed except `example.yaml`.
- A summary is printed at the end showing which maps succeeded and which failed.
