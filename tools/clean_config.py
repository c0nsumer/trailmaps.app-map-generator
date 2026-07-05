#!/usr/bin/env python3
"""Clean a production map YAML against the canonical template.

Produces a sibling `<name>-cleaned.yaml` next to the input, with the
TEMPLATE'S structure (section dividers, key ordering, default-value
documentation comments) and the PRODUCTION'S set values filled in.

The intent is housekeeping: production configs accumulate cruft over
time as they're maintained by hand — keys reordered, comments edited,
sections renamed, etc. This tool re-aligns a config to the canonical
template without losing any explicitly-set values or any curator
comments.

Usage:
    python tools/clean_config.py configs/example/example.yaml
    python tools/clean_config.py configs/example/example.yaml \\
        --template configs/reference/reference-minimal.yaml

The output file is `<input-stem>-cleaned.yaml` in the same directory.
The original file is never modified — review the cleaned output and
swap it in manually when satisfied.

Behaviour:
- Set keys are SPLICED, not re-serialized: the production file's own
  lines for each key it sets are copied verbatim into the template's
  position for that key. Inline comments, list-item annotations, and
  comment lines inside a block survive by construction, and values
  cannot be reformatted into something that parses differently.
- Full-line comments directly above a set key travel with it.
- A commented-out known-key block in production (a curator's stashed
  alternative, e.g. `# forced_labels: routes`) replaces the template's
  generic `# key: default` line for that key, so saved alternatives
  keep their place instead of being flattened back to the default.
- Keys production neither sets nor stashes appear as the template's
  commented `# key: default` lines — every supported option stays
  visible at its default.
- LIVE (uncommented) template keys that production does NOT set are
  commented out rather than copied — the cleaned output must never
  inherit a template placeholder value (e.g. the example `relations:`
  ID on a custom-route-only map that legitimately omits the key).
- Production keys that don't appear anywhere in the template are
  appended at the end under a `# --- Keys not in template ---`
  header. Catches drift in either direction (key the template
  forgot, or key the curator added that the template doesn't model).
- Comments the placement heuristics can't attach anywhere are
  appended under `# --- Unplaced comments carried from the previous
  file (review/relocate) ---` — misplaced but kept, never lost.
- Hard gate: after writing, the output is re-parsed and compared to
  the original. Any difference in parsed data deletes the output and
  aborts — the tool cannot hand back a config that behaves
  differently from the file it was given.
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

# Uncommented top-level key line in the production file. Deliberately
# NOT filtered through KEY_NAMES: an unknown key the curator added
# must still be captured as a block (it lands under "Keys not in
# template"), or its lines would be silently dropped and the equality
# gate would abort the run.
LIVE_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")

# Commented-out known-key line: a curator's stashed alternative.
# Tolerant of `#key:` with no space after the hash — real configs
# contain that form.
STASH_KEY_RE = re.compile(r"^#\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")

# A continuation line for a commented multi-line block looks like
# `#   sub: val` or `#     - item` (`#` followed by 2+ spaces then
# content). Plain `# Some prose` (one space, content) is NOT a
# continuation — it's a fresh comment.
COMMENTED_CONTINUATION_RE = re.compile(r"^#\s{2,}\S")

# Commented list items (`# - 123`, `#- name: ...`) continue a stashed
# block even at zero/one spaces of indent. The lookahead excludes
# `# ---` section dividers.
COMMENTED_LIST_ITEM_RE = re.compile(r"^#\s*-(?!-)")

DIVIDER_RE = re.compile(r"^#\s*(-{3,}|={3,})")

# Zero-indent sequence item (`- name: Main` at column 0) — valid YAML
# that several production configs use under `trailheads:`/`parking:`.
# It continues the preceding key's block despite not being indented.
ZERO_INDENT_ITEM_RE = re.compile(r"^-(\s|$)")


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


def _is_commented_continuation(line):
    if DIVIDER_RE.match(line):
        return False
    return bool(
        COMMENTED_CONTINUATION_RE.match(line) or COMMENTED_LIST_ITEM_RE.match(line)
    )


def find_block_end_prod(lines, start):
    """`find_block_end` for PRODUCTION files.

    Production blocks can contain interior blank lines (e.g. between
    `trailheads:` entries) and full-line comments; the template never
    does, and `find_block_end`'s other callers depend on blank lines
    terminating a block, so that function stays untouched. Here a
    blank or full-line comment is interior only when the block's own
    content resumes after it — otherwise it terminates the block.
    """
    line = lines[start]
    commented = line.lstrip().startswith("#")

    after_colon = line.split(":", 1)[1]
    if re.sub(r"\s+#.*$", "", after_colon).strip():
        return start + 1

    n = len(lines)
    i = start + 1
    while i < n:
        nxt = lines[i]
        if commented:
            if not nxt.strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and _is_commented_continuation(lines[j]):
                    i = j
                    continue
                return i
            if _is_commented_continuation(nxt):
                i += 1
                continue
            return i
        else:
            if nxt.strip() and (
                nxt[0] in (" ", "\t") or ZERO_INDENT_ITEM_RE.match(nxt)
            ):
                i += 1
                continue
            if not nxt.strip() or nxt.startswith("#"):
                j = i
                while j < n and (not lines[j].strip() or lines[j].startswith("#")):
                    j += 1
                if (
                    j < n
                    and lines[j].strip()
                    and (
                        lines[j][0] in (" ", "\t")
                        or ZERO_INDENT_ITEM_RE.match(lines[j])
                    )
                ):
                    i = j
                    continue
                return i
            return i
    return i


def _index_production(prod_lines, is_boilerplate):
    """Classify the production file's lines.

    Returns (consumed, set_blocks, stashes, leading):
    - consumed:   per-line flags; unconsumed comments feed the
                  unplaced-comments safety net.
    - set_blocks: key -> (start, end) line range the curator set.
    - stashes:    key -> verbatim lines of commented-out alternatives
                  for keys that are NOT set.
    - leading:    key -> full-line comments sitting directly above a
                  set block (they travel with it).
    """
    n = len(prod_lines)
    consumed = [False] * n
    set_blocks = {}
    stashes = {}
    leading = {}

    i = 0
    while i < n:
        m = LIVE_KEY_RE.match(prod_lines[i])
        if m and m.group(1) not in set_blocks:
            end = find_block_end_prod(prod_lines, i)
            set_blocks[m.group(1)] = (i, end)
            for k in range(i, end):
                consumed[k] = True
            i = end
        else:
            i += 1

    # Stashes must be found before leading comments: a stash's own
    # continuation lines (`#   - name: Old`) would otherwise be
    # picked up as leading comments of the block below and then
    # emitted twice.
    i = 0
    while i < n:
        m = None if consumed[i] else STASH_KEY_RE.match(prod_lines[i])
        if m and m.group(1) in KEY_NAMES and m.group(1) not in set_blocks:
            end = find_block_end_prod(prod_lines, i)
            block = prod_lines[i:end]
            if all(not l.strip() or is_boilerplate(l) for l in block):
                # Template residue (the template's own commented
                # default carried around) — the template re-supplies
                # it at its canonical position.
                i = end
                continue
            # A key stashed twice keeps both alternatives, in order.
            stashes.setdefault(m.group(1), []).extend(block)
            for k in range(i, end):
                consumed[k] = True
            i = end
        else:
            i += 1

    for key, (start, _end) in set_blocks.items():
        picked = []
        i = start - 1
        while i >= 0:
            line = prod_lines[i]
            if not line.strip() or not line.lstrip().startswith("#"):
                break
            if not consumed[i]:
                m = STASH_KEY_RE.match(line)
                stash_like = bool(m and m.group(1) in KEY_NAMES)
                if not stash_like and not is_boilerplate(line):
                    picked.append(i)
            i -= 1
        if picked:
            leading[key] = [prod_lines[k] for k in reversed(picked)]
            for k in picked:
                consumed[k] = True

    return consumed, set_blocks, stashes, leading


def _assert_same_data(production_path, output_path):
    """Hard gate: the cleaned file must parse to exactly the same
    data as the original. Anything else deletes the output and aborts
    — the tool physically cannot hand back a config that behaves
    differently."""
    with open(production_path, encoding="utf-8") as f:
        original = yaml.safe_load(f)
    with open(output_path, encoding="utf-8") as f:
        cleaned = yaml.safe_load(f)
    if original != cleaned:
        os.remove(output_path)
        sys.exit(
            "ERROR: cleaned output would change parsed values — "
            "aborted, no file written"
        )


def clean_config(template_path, production_path, output_path):
    with open(template_path, encoding="utf-8") as f:
        template_lines = f.read().splitlines()
    with open(production_path, encoding="utf-8") as f:
        prod_lines = f.read().splitlines()

    template_stripped = {l.strip() for l in template_lines if l.strip()}

    def is_boilerplate(line):
        # A production line the template already supplies (verbatim
        # modulo indentation) or a section divider — never carried,
        # the template's own copy wins.
        s = line.strip()
        return s in template_stripped or bool(DIVIDER_RE.match(s))

    consumed, set_blocks, stashes, leading = _index_production(
        prod_lines, is_boilerplate
    )

    output_lines = []
    seen = set()
    emitted_stashes = set()
    commented_out = set()
    i = 0
    while i < len(template_lines):
        line = template_lines[i]
        key = extract_top_level_key(line)
        if key is not None and key in set_blocks and key not in seen:
            seen.add(key)
            output_lines.extend(leading.get(key, []))
            s, e = set_blocks[key]
            output_lines.extend(prod_lines[s:e])
            i = find_block_end(template_lines, i)
        elif key is not None and key in stashes and key not in emitted_stashes:
            # The curator's saved alternative replaces the template's
            # generic default line for this key.
            emitted_stashes.add(key)
            output_lines.extend(stashes[key])
            i = find_block_end(template_lines, i)
        elif key is not None and not line.lstrip().startswith("#"):
            # Template key is LIVE (uncommented — the template's
            # always-set examples: name / slug / title / relations)
            # but production doesn't set it. Copying the block
            # verbatim would smuggle the template's placeholder value
            # into the cleaned output — this bit the two
            # custom-route-only event maps, whose legitimately-omitted
            # `relations:` gained the template's example ID. Comment
            # the block out instead ("# " + line matches the
            # template's commented-key style, and "#   - item" parses
            # as a commented continuation), so the key stays
            # discoverable but sets nothing.
            end = find_block_end(template_lines, i)
            for tl in template_lines[i:end]:
                output_lines.append("# " + tl if tl.strip() else tl)
            commented_out.add(key)
            i = end
        else:
            output_lines.append(line)
            i += 1

    extra = [k for k in set_blocks if k not in seen]
    if extra:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append("# --- Keys not in template ---")
        for k in extra:
            output_lines.extend(leading.get(k, []))
            s, e = set_blocks[k]
            output_lines.extend(prod_lines[s:e])

    # Safety net: anything the heuristics couldn't place is carried
    # at the end — misplaced but kept, never lost.
    unplaced = []
    for k, block in stashes.items():
        if k not in emitted_stashes:
            unplaced.extend(block)
    for idx, line in enumerate(prod_lines):
        if consumed[idx] or not line.strip():
            continue
        if not line.lstrip().startswith("#"):
            continue
        if is_boilerplate(line):
            continue
        unplaced.append(line)
    if unplaced:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append(
            "# --- Unplaced comments carried from the previous file "
            "(review/relocate) ---"
        )
        output_lines.extend(unplaced)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    _assert_same_data(production_path, output_path)

    return {
        "set": sorted(seen),
        "stashed": sorted(emitted_stashes),
        "commented_out": sorted(commented_out),
        "extra": extra,
        "unplaced_comments": len(unplaced),
    }


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
    default_template = os.path.join(_PROJECT_ROOT, "configs", "reference", "reference-minimal.yaml")
    parser.add_argument(
        "--template",
        default=default_template,
        help=(
            "Path to the canonical template YAML "
            "(default: configs/reference/reference-minimal.yaml relative "
            "to the project root)."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Override output path. Default: same directory as input, stem with '-cleaned' suffix."
        ),
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

    summary = clean_config(args.template, args.production, output_path)
    print(f"Wrote {output_path}")
    if summary["set"]:
        print(
            f"  {len(summary['set'])} set key(s) spliced at template "
            f"positions: {summary['set']}"
        )
    if summary["stashed"]:
        print(
            f"  {len(summary['stashed'])} stashed (commented-out) key(s) "
            f"carried: {summary['stashed']}"
        )
    if summary["commented_out"]:
        print(
            f"  {len(summary['commented_out'])} live template key(s) not set "
            f"by production, commented out: {summary['commented_out']}"
        )
    if summary["extra"]:
        print(
            f"  {len(summary['extra'])} key(s) appended (not in template): "
            f"{summary['extra']}"
        )
    if summary["unplaced_comments"]:
        print(
            f"  {summary['unplaced_comments']} comment line(s) carried to "
            f"the unplaced-comments section — review and relocate by hand"
        )


if __name__ == "__main__":
    main()
