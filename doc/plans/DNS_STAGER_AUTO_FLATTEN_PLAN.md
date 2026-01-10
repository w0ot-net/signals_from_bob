# DNS Stager Auto Flatten Plan

Status: draft

## Summary
Follow-on plan after Phases 1-3 to auto-generate `sfb_flat.py` when Bob is
started with `--stager` and no path. The auto-flatten step will attempt to
minify if `python-minifier` is installed, but will warn and proceed without
minification when it is not available.

## Goals
- Auto-run `scripts/flatten.py` when `--stager` is provided without a path.
- Minify only when `python-minifier` is available; never require external
  libraries for normal operation.
- Keep behavior deterministic and compatible with Python 2/3 tooling.

## Non-Goals
- Changes to stager DNS logic or one-liner generation.
- Introducing new runtime dependencies.
- Tests or e2e validation.

## Affected Components
- `sfb/cli.py`
- `scripts/flatten.py` (if optional-minify handling needs adjustment)

## Plan
1. Extend `--stager` handling for the server role.
   - If `--stager <path>` is provided, validate the path and use it as-is.
   - If `--stager` is provided without a path, run the auto-flatten step.

2. Implement auto-flatten with optional minify.
   - Use `sys.executable` to invoke `scripts/flatten.py`.
   - Always pass:
     - `--manifest doc/flatten_manifest.txt`
     - `--output sfb_flat.py`
     - `--strip-logs`
     - `--alice`
     - `--transport <bob transport>`
   - Attempt `import python_minifier` in the CLI before spawning:
     - If available, add `--minify`.
     - If missing, print a warning that minification is skipped and proceed.
   - Do not attempt external `pyminify` fallback when `python-minifier` is
     missing.

3. Validate output.
   - After the auto-flatten step, verify `sfb_flat.py` exists.
   - If missing, exit with a clear error.

## Testing
- Do not run tests here.
