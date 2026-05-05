#!/usr/bin/env python3
"""Clean a production map YAML against the canonical template.

Produces a sibling `<name>-cleaned.yaml` next to the input, with the
TEMPLATE'S structure (section dividers, key ordering, default-value
documentation comments) and the PRODUCTION'S set values filled in.

The intent is housekeeping: production configs accumulate cruft over
time as they're maintained by hand — keys reordered, comments edited,
sections renamed, etc. This tool re-aligns a config to the canonical
template without losing any explicitly-set values.

Usage:
    python tools/clean_config.py configs/potoloo/potoloo.yaml
    python tools/clean_config.py configs/potoloo/potoloo.yaml \\
        --template configs/reference/reference-minimal.yaml

The output file is `<input-stem>-cleaned.yaml` in the same directory.
The original file is never modified — review the cleaned output and
swap it in manually when satisfied.

Behaviour:
- Lines in the template that match a known config key (commented or
  not) are replaced with the production's value when production sets
  that key. Multi-line template blocks (e.g. commented `# welcome:`
  with indented continuation comments) are skipped past wholesale.
- Template lines that aren't key lines (section dividers, prose
  comments, blank lines) are preserved verbatim.
- Production keys that don't appear anywhere in the template are
  appended at the end under a `# --- Keys not in template ---`
  header. Catches drift in either direction (key the template
  forgot, or key the curator added that the template doesn't model).
- Inline comments in the production file (e.g. `accent_color: auto
  # logo is B/W`) are NOT preserved — the template's structure wins
  for layout and the production's value wins for content.
"""

import argparse
import os
import re
import sys

import yaml

# Pull KNOWN_KEYS from scripts/validate_config so we can distinguish
# "real top-level key line" from "prose comment that happens to
# contain a colon" (e.g. `# Skip the relevant Overpass query / asset
# generation when false.`).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
from validate_config import KNOWN_KEYS  # noqa: E402

KEY_NAMES = set(KNOWN_KEYS.keys())

# Matches `key:` or `# key:` at column 0. Captures the key name; we
# then check it against KEY_NAMES to filter out prose comments.
KEY_LINE_RE = re.compile(r"^(?:#\s)?([A-Za-z_][A-Za-z0-9_]*)\s*:")

# A continuation line for a commented multi-line block looks like
# `#   sub: val` or `#     - item` (`#` followed by 2+ spaces then
# content). Plain `# Some prose` (one space, content) is NOT a
# continuation — it's a fresh comment.
COMMENTED_CONTINUATION_RE = re.compile(r"^#\s{2,}\S")


def extract_top_level_key(line):
    """Return the key name if `line` is a top-level key line for a
    KNOWN config key (commented or uncommented). Otherwise None."""
    m = KEY_LINE_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    if name not in KEY_NAMES:
        return None
    return name


def find_block_end(lines, start):
    """Given that `lines[start]` is a top-level key line, return the
    index just past the last line of that key's block.

    Single-line key (`key: value`):     returns start + 1.
    Multi-line uncommented key:         skips indented continuation lines.
    Multi-line commented key (`# key:`):skips `#   ...` continuation comments.
    """
    line = lines[start]
    commented = line.lstrip().startswith("#")

    # Strip everything from `#` (inline comment) onward when looking
    # for "is this line a single-line value?". A line like
    # `accent_color: auto    # explanation` should still be treated
    # as single-line.
    after_colon = line.split(":", 1)[1]
    after_colon_no_inline = re.sub(r"\s+#.*$", "", after_colon).strip()
    if after_colon_no_inline:
        return start + 1

    # Multi-line continuation
    i = start + 1
    while i < len(lines):
        nxt = lines[i]
        if not nxt.strip():
            break  # blank line ends the block
        if commented:
            if COMMENTED_CONTINUATION_RE.match(nxt):
                i += 1
                continue
            break
        else:
            # Uncommented continuation: indented (starts with whitespace)
            if nxt[0] in (" ", "\t"):
                i += 1
                continue
            break
    return i


def _scalar_text(v):
    """YAML text for a single scalar value (no newline, no key prefix)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Defer to yaml.dump for quoting decisions (handles "yes"-vs-True
        # ambiguity, leading colons, special chars). yaml.dump of a bare
        # scalar appends `\n...\n` (document end marker); strip both.
        # explicit_end=False suppresses `...` from the dumper directly,
        # but PyYAML still emits it for some scalar shapes — belt-and-
        # suspenders strip after.
        out = yaml.dump(v, default_flow_style=True, explicit_end=False)
        return out.replace("...\n", "").rstrip("\n").rstrip()
    raise TypeError(f"_scalar_text: unsupported type {type(v).__name__}")


def _is_scalar(v):
    return v is None or isinstance(v, (bool, int, float, str))


class _CleanDumper(yaml.SafeDumper):
    """Custom dumper to fix two PyYAML default-formatting quirks:

    1. Multi-line strings get `|` block-literal style (default would
       single-quote with embedded \\n escapes, ugly + lossy on
       trailing whitespace).
    2. Dicts are ALWAYS block-style. Default `default_flow_style=None`
       smart-flows single-key dicts (`relation_colors: {1234: '#fff'}`),
       which loses the multi-line readability we want for sparse maps.
    3. Lists are flow-style when ALL items are scalar (so
       `coordinates: [lon, lat]` and `pattern: [1, 1]` stay one-liners),
       block-style otherwise (so `trailheads: \\n- name: ...\\n  ...`
       reads cleanly).
    """
    pass


def _block_str_representer(dumper, data):
    if isinstance(data, str) and "\n" in data:
        cleaned = "\n".join(line.rstrip() for line in data.split("\n"))
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", cleaned, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _block_dict_representer(dumper, data):
    return dumper.represent_mapping(
        "tag:yaml.org,2002:map", data, flow_style=False)


def _smart_list_representer(dumper, data):
    # All-scalar lists go flow; anything with nested structure goes block.
    flow = all(_is_scalar(v) for v in data)
    return dumper.represent_sequence(
        "tag:yaml.org,2002:seq", data, flow_style=flow)


_CleanDumper.add_representer(str, _block_str_representer)
_CleanDumper.add_representer(dict, _block_dict_representer)
_CleanDumper.add_representer(list, _smart_list_representer)


def format_key(key, value):
    """Render `key: value` as a list of YAML lines.

    Handles each value shape with the formatting that matches the
    template's house style:
    - Scalars (None, bool, number, string) → single line `key: value`
      with appropriate quoting.
    - All-scalar lists → flow style `key: [a, b, c]`.
    - Nested structures (dicts, lists of dicts) → block style.
    """
    if _is_scalar(value):
        return [f"{key}: {_scalar_text(value)}"]
    if isinstance(value, list) and all(_is_scalar(v) for v in value):
        items = ", ".join(_scalar_text(v) for v in value)
        return [f"{key}: [{items}]"]
    # Nested: dict or list-of-dicts. _CleanDumper's representers
    # handle the per-node formatting (always block dicts; lists block
    # unless all-scalar; multi-line strings as `|`).
    dumped = yaml.dump(
        {key: value},
        Dumper=_CleanDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
        indent=2,
    )
    return dumped.rstrip("\n").split("\n")


def clean_config(template_path, production_path, output_path):
    with open(template_path) as f:
        template_lines = f.read().splitlines()
    with open(production_path) as f:
        production = yaml.safe_load(f) or {}

    output_lines = []
    seen_keys = set()
    i = 0
    while i < len(template_lines):
        line = template_lines[i]
        key = extract_top_level_key(line)
        if key is not None and key in production:
            output_lines.extend(format_key(key, production[key]))
            seen_keys.add(key)
            i = find_block_end(template_lines, i)
        else:
            output_lines.append(line)
            i += 1

    extra = [k for k in production.keys() if k not in seen_keys]
    if extra:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append("# --- Keys not in template ---")
        for k in extra:
            output_lines.extend(format_key(k, production[k]))

    with open(output_path, "w") as f:
        f.write("\n".join(output_lines) + "\n")

    return seen_keys, extra


def main():
    parser = argparse.ArgumentParser(
        description="Re-align a production map YAML against the canonical template.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Output goes to <input-stem>-cleaned.yaml in the same directory.\n"
            "The original file is never modified."
        ),
    )
    parser.add_argument(
        "production",
        help="Path to the production YAML to clean.",
    )
    # Resolve the default template relative to project root (the
    # parent of tools/) so the tool works from any cwd, not just
    # the project root. Curators running it via `python tools/...`
    # from the project root see the same default either way.
    _PROJECT_ROOT = os.path.dirname(_HERE)
    default_template = os.path.join(
        _PROJECT_ROOT, "configs", "reference", "reference-minimal.yaml")
    parser.add_argument(
        "--template",
        default=default_template,
        help=("Path to the canonical template YAML "
              "(default: configs/reference/reference-minimal.yaml relative "
              "to the project root)."),
    )
    parser.add_argument(
        "-o", "--output",
        help=("Override output path. Default: same directory as input, "
              "stem with '-cleaned' suffix."),
    )
    args = parser.parse_args()

    if not os.path.isfile(args.template):
        sys.exit(f"ERROR: template not found: {args.template}")
    if not os.path.isfile(args.production):
        sys.exit(f"ERROR: production not found: {args.production}")

    if args.output:
        output_path = args.output
    else:
        base, ext = os.path.splitext(args.production)
        output_path = f"{base}-cleaned{ext or '.yaml'}"

    seen, extra = clean_config(args.template, args.production, output_path)
    print(f"Wrote {output_path}")
    print(f"  {len(seen)} key(s) from production matched template positions")
    if extra:
        print(f"  {len(extra)} key(s) appended (not in template): {sorted(extra)}")
    else:
        print("  No extra keys to append (all production keys matched the template).")


if __name__ == "__main__":
    main()
