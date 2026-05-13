#!/bin/bash
#
# Build and deploy trail maps.
#
# Each map lives in its own folder: configs/<slug>/<slug>.yaml + assets.
# By default, this processes every such folder except configs/reference/.
# Pass one or more slugs to limit the run to a subset.
#
# See --help for full usage.

set -euo pipefail

# ── Default configuration ─────────────────────────────────────
# The default deploy destination is read from the
# TRAILMAPS_DEPLOY_DEST environment variable. If unset, the script
# errors out with a clear hint when --dest also isn't passed (see
# the deploy-dest check after argument parsing). Set this in your
# shell rc, e.g.
#   export TRAILMAPS_DEPLOY_DEST=user@host:/var/www/maps
# so daily `./tools/build_and_deploy.sh <slug>` invocations Just
# Work. Forkers see no hardcoded server address in the script.
DEFAULT_DEPLOY_DEST="${TRAILMAPS_DEPLOY_DEST:-}"
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
CONFIGS_DIR="${PROJECT_ROOT}/configs"

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [slug ...] [-- build-flag ...]

Builds and deploys trail map(s). Each map lives in configs/<slug>/.
Without a slug list, processes every configs/<slug>/ folder except
configs/reference/.

Options:
  --all              Process every config (default if no names given)
  --build-only       Build but skip deploy
  --deploy-only      Deploy existing builds without rebuilding
  --validate-only    Only run config validation (no fetch/build/deploy)
  --force            Pass --force to build.py (re-fetch all data)
  --dry-run          Show what would happen; don't build or transfer
  --dest <ssh-path>  Override deploy destination
                     (default: \$TRAILMAPS_DEPLOY_DEST env var;
                     required if the env var isn't set)
  --skip-ssh-check   Skip the pre-flight SSH connectivity check
  -h, --help         Show this help

Anything after a literal -- is forwarded to build.py:
  $(basename "$0") ramba -- --skip-basemap --skip-terrain

Examples:
  $(basename "$0")                      # build & deploy everything
  $(basename "$0") ramba                # one map
  $(basename "$0") --build-only ramba dte
  $(basename "$0") --validate-only      # lint every config
  $(basename "$0") --dry-run --force ramba
EOF
}

# ── Parse arguments ───────────────────────────────────────────
BUILD=true
DEPLOY=true
VALIDATE_ONLY=false
DRY_RUN=false
SKIP_SSH_CHECK=false
FORCE=""
DEPLOY_DEST="$DEFAULT_DEPLOY_DEST"
configs=()
build_extra_args=()
past_separator=false

while [ $# -gt 0 ]; do
    arg="$1"
    if $past_separator; then
        build_extra_args+=("$arg")
        shift; continue
    fi
    case "$arg" in
        --)              past_separator=true ;;
        --all)           ;; # default behavior, no-op
        --build-only)    DEPLOY=false ;;
        --deploy-only)   BUILD=false ;;
        --validate-only) VALIDATE_ONLY=true; BUILD=false; DEPLOY=false ;;
        --force)         FORCE="--force" ;;
        --dry-run)       DRY_RUN=true ;;
        --dest)          shift; DEPLOY_DEST="${1:?--dest needs a value}" ;;
        --dest=*)        DEPLOY_DEST="${arg#--dest=}" ;;
        --skip-ssh-check) SKIP_SSH_CHECK=true ;;
        -h|--help)       usage; exit 0 ;;
        --*)             build_extra_args+=("$arg") ;;
        *)               configs+=("$arg") ;;
    esac
    shift
done

# ── Sanity checks ─────────────────────────────────────────────
cd "$PROJECT_ROOT"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: Python interpreter not found at $PYTHON" >&2
    echo "       Create the venv with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# If no slugs specified, discover every configs/<slug>/<slug>.yaml except
# configs/reference/ (which holds the reference + reference-minimal templates).
if [ ${#configs[@]} -eq 0 ]; then
    for d in "${CONFIGS_DIR}"/*/; do
        name="$(basename "$d")"
        [ "$name" = "example" ] && continue
        # Each map folder is expected to contain <slug>.yaml; skip any
        # stray directory that doesn't.
        [ -f "${CONFIGS_DIR}/${name}/${name}.yaml" ] || continue
        configs+=("$name")
    done
fi

if [ ${#configs[@]} -eq 0 ]; then
    echo "ERROR: No config files found in ${CONFIGS_DIR}/" >&2
    exit 1
fi

# ── Helpers ───────────────────────────────────────────────────

# Resolve the build output directory for a config. Honors the optional
# `output_dir:` key, falling back to `build/<slug>`. Uses Python+yaml
# rather than grep so quoting variants, comments, and overrides are
# handled correctly.
resolve_output_dir() {
    local cfg="$1"
    "$PYTHON" - "$cfg" <<'PY'
import os, sys, yaml
with open(sys.argv[1]) as f:
    c = yaml.safe_load(f) or {}
slug = c.get("slug") or os.path.splitext(os.path.basename(sys.argv[1]))[0]
print(c.get("output_dir") or os.path.join("build", slug))
PY
}

format_seconds() {
    local s="$1"
    if [ "$s" -ge 60 ]; then
        printf '%dm%02ds' $((s / 60)) $((s % 60))
    else
        printf '%ds' "$s"
    fi
}

# Quick SSH connectivity probe — saves you from finding out an hour into a
# rebuild that the key isn't loaded. Hits the deploy host with BatchMode so
# it fails fast instead of prompting.
ssh_precheck() {
    local dest="$1"
    local host="${dest%%:*}"
    if [[ "$host" != *@* && "$host" != *.* ]]; then
        # Doesn't look like a remote spec — skip the check.
        return 0
    fi
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 \
            -o StrictHostKeyChecking=accept-new \
            "$host" true 2>/dev/null; then
        echo "ERROR: SSH connectivity check failed for $host" >&2
        echo "       Add your key with ssh-add, or pass --skip-ssh-check to bypass." >&2
        return 1
    fi
}

# Ensure the deploy destination directory exists before rsync runs. rsync
# creates the innermost leaf directory on its own, but bails out if any
# intermediate path component is missing — and even the leaf case is only
# reliable with certain rsync versions. An explicit `mkdir -p` covers both
# (and is a no-op when the directory already exists), which matters most on
# the first deploy of a newly-added map.
#
# Works for both remote (user@host:/path) and local destinations.
ensure_dest_dir() {
    local dest_prefix="$1"    # e.g. user@host:/path/to/dest  OR  /path/to/dest
    local subdir="$2"         # e.g. ramba
    if [[ "$dest_prefix" == *:* ]]; then
        local host="${dest_prefix%%:*}"
        local path="${dest_prefix#*:}"
        local remote_full="${path%/}/${subdir}"
        if $DRY_RUN; then
            echo "[dry-run] ssh ${host} mkdir -p ${remote_full}"
            return 0
        fi
        ssh -o BatchMode=yes "$host" "mkdir -p '${remote_full}'"
    else
        local local_full="${dest_prefix%/}/${subdir}"
        if $DRY_RUN; then
            echo "[dry-run] mkdir -p ${local_full}"
            return 0
        fi
        mkdir -p "$local_full"
    fi
}

# ── Pre-flight ────────────────────────────────────────────────

# Always validate up-front. The validator is fast and surfaces every
# config error at once before any expensive fetch/build work starts.
echo "━━━ Validating ${#configs[@]} config(s) ━━━"
validate_args=()
for name in "${configs[@]}"; do
    validate_args+=("${CONFIGS_DIR}/${name}/${name}.yaml")
done
if ! "$PYTHON" "${PROJECT_ROOT}/scripts/validate_config.py" "${validate_args[@]}"; then
    echo "Aborting: fix config errors above and rerun." >&2
    exit 1
fi
echo ""

if $VALIDATE_ONLY; then
    echo "All ${#configs[@]} config(s) validated successfully."
    exit 0
fi

# Deploy-destination resolution. We need a destination if we're
# going to deploy at all. Surface the missing-config case with a
# helpful "set this env var or pass --dest" hint rather than
# silently failing later inside rsync.
if $DEPLOY && [ -z "$DEPLOY_DEST" ]; then
    cat >&2 <<EOF
ERROR: No deploy destination set.

Either pass --dest <user@host:/path> on this invocation, or set
TRAILMAPS_DEPLOY_DEST in your shell environment:

    export TRAILMAPS_DEPLOY_DEST=user@host:/var/www/your-maps

(Add the export to your ~/.zshrc or ~/.bashrc to persist it.)

To build without deploying, pass --build-only.
EOF
    exit 1
fi

if $DEPLOY && ! $SKIP_SSH_CHECK && ! $DRY_RUN; then
    echo "━━━ Checking SSH connectivity to ${DEPLOY_DEST%%:*} ━━━"
    ssh_precheck "$DEPLOY_DEST" || exit 1
    echo "OK"
    echo ""
fi

# ── Main loop ─────────────────────────────────────────────────

echo "Maps: ${configs[*]}"
$DRY_RUN && echo "(dry-run mode — nothing will be built or transferred)"
echo ""

succeeded=()
failed=()
overall_start=$(date +%s)

for name in "${configs[@]}"; do
    config_file="${CONFIGS_DIR}/${name}/${name}.yaml"
    if [ ! -f "$config_file" ]; then
        echo "ERROR: Config not found: ${config_file}" >&2
        failed+=("$name")
        continue
    fi

    map_start=$(date +%s)

    # Build
    if $BUILD; then
        echo "━━━ Building ${name} ━━━"
        # scripts/build.py produces ready-to-deploy artifacts by
        # default (minification on, etc.) — quality-posture
        # decisions live in the build pipeline, not in this helper.
        # If you need unminified output for local debug via this
        # script (rare; usually you'd just call build.py directly),
        # pass `-- --no-minify` after the slug list:
        #   ./tools/build_and_deploy.sh --build-only ramba -- --no-minify
        if $DRY_RUN; then
            echo "[dry-run] $PYTHON scripts/build.py $config_file $FORCE ${build_extra_args[*]:-}"
        else
            # The "${arr[@]+...}" expansion is the standard workaround for
            # nounset (set -u) tripping on empty arrays in older Bash.
            if ! "${PYTHON}" scripts/build.py "$config_file" $FORCE \
                    "${build_extra_args[@]+"${build_extra_args[@]}"}"; then
                echo "ERROR: Build failed for ${name}" >&2
                failed+=("$name")
                continue
            fi
        fi
        echo ""
    fi

    # Deploy
    if $DEPLOY; then
        # Resolve the actual build dir from the config (honors output_dir).
        if ! build_dir_rel=$(resolve_output_dir "$config_file"); then
            echo "ERROR: Could not resolve output dir from ${config_file}" >&2
            failed+=("$name")
            continue
        fi
        if [[ "$build_dir_rel" = /* ]]; then
            build_dir="$build_dir_rel"
        else
            build_dir="${PROJECT_ROOT}/${build_dir_rel}"
        fi
        if [ ! -d "$build_dir" ] && ! $DRY_RUN; then
            echo "ERROR: Build directory not found: ${build_dir}" >&2
            failed+=("$name")
            continue
        fi

        # Slug for the remote path (may differ from local config name).
        slug=$("$PYTHON" -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1])).get('slug','$name'))" "$config_file")

        echo "━━━ Deploying ${name} → ${DEPLOY_DEST}/${slug}/ ━━━"
        if ! ensure_dest_dir "$DEPLOY_DEST" "$slug"; then
            echo "ERROR: Could not create destination directory ${DEPLOY_DEST}/${slug}" >&2
            failed+=("$name")
            continue
        fi
        if $DRY_RUN; then
            echo "[dry-run] rsync -avz --delete ${build_dir}/ ${DEPLOY_DEST}/${slug}/"
        else
            rsync -avz --delete "${build_dir}/" "${DEPLOY_DEST}/${slug}/"
        fi
        echo ""
    fi

    map_elapsed=$(( $(date +%s) - map_start ))
    echo "  ${name} done in $(format_seconds "$map_elapsed")"
    echo ""
    succeeded+=("$name")
done

# ── Summary ───────────────────────────────────────────────────
overall_elapsed=$(( $(date +%s) - overall_start ))

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Total time: $(format_seconds "$overall_elapsed")"
if [ ${#failed[@]} -eq 0 ]; then
    echo "All maps processed successfully: ${succeeded[*]}"
else
    echo "Completed with errors."
    [ ${#succeeded[@]} -gt 0 ] && echo "  Succeeded: ${succeeded[*]}"
    echo "  Failed:    ${failed[*]}"
    exit 1
fi
