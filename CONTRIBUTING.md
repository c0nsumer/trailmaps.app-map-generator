# Contributing

This is a personal project, built and maintained by one person in spare time.
Contributions are welcome with that context in mind:

- **Issues** are the best way to help. Bug reports with a config that
  reproduces the problem, or pointers to incorrect docs, are genuinely useful.
- **Pull requests** may sit for a while, and may be declined if they don't fit
  the project's direction (small, self-hosted, no runtime dependencies, no
  tracking). Opening an issue to discuss first is a good idea for anything
  bigger than a typo fix.

## Before submitting a change

Run the checks from the repo root:

```bash
python -m pytest scripts/tests/ -q   # no network needed, runs in seconds
ruff check scripts/ tools/ map_generator/
```

Both must pass. The test suite runs entirely offline against the bundled
`configs/example/` config.

## Conventions

- American English in docs, comments, and UI text. Literal OSM tags keep their
  British spelling (`colour=` stays `colour=`).
- The orchestrator CLI contract must not break: `scripts/build.py <config>
  --dry-run --output-dir [--refresh]` is driven by external tooling.
- Comments in this codebase carry design rationale, not narration. Keep that
  standard: explain why, not what.
