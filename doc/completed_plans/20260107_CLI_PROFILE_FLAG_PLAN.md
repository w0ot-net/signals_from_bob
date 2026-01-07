# CLI Profile Flag Plan

Status: completed

## Goal
- Add a `--cprofile` CLI flag that runs sfb normally but writes a cProfile
  output file when the flag is present.
- Keep the default path predictable and safe while allowing a custom path.
- Preserve Python 2.7/3 compatibility and standard-library-only behavior.

## Non-Goals
- No protocol, transport, or module behavior changes.
- No external profiling tools or dependencies.
- No code changes under ./tests.

## Affected Components
- sfb/cli.py
- README.md

## Design Notes
- Use `cProfile.Profile()` with `enable()`/`disable()` and `dump_stats()`.
- `--cprofile` accepts an optional path. If omitted, write to
  `/tmp/sfb_<role>_<transport>_<YYYYMMDD_HHMMSS>_<pid>.prof`.
- For Windows compatibility, if `/tmp` is not available or not writable,
  fall back to `tempfile.gettempdir()`.
- Create the output directory if needed and always dump in a `finally` block
  so profiles are written on errors or clean exits.
- When `--cprofile` is absent, the execution path stays unchanged.
- Note that cProfile only tracks the main thread; document this caveat.

## Plan
1. CLI parsing
   - Add `--cprofile` with `nargs='?'` and a `const` to detect the flag when no
     path is provided.
   - Keep the option in `add_common_args` so it appears in both parse passes.
2. Profile path resolution
   - Add a small helper to derive the output path from role/transport, PID, and
     wall clock time; normalize to an absolute path.
   - Ensure the parent directory exists before starting the profile.
3. Profile wrapper
   - Wrap the main run path (post-parse) with a helper that starts the profiler,
     executes the selected role, and writes the `.prof` file in `finally`.
   - Log the resolved profile path at startup for visibility.
4. Documentation
   - Add a short README note with example usage and the default output path.

## Validation
- Manual run with and without `--cprofile` to confirm the `.prof` file appears
  only when requested.
- Do not run tests/e2e/.

## Execution notes
- 2026-01-07: Implemented `--cprofile` with /tmp default, fallback to
  `tempfile.gettempdir()`, and README usage notes.
