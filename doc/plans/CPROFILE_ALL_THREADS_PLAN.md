# CProfile All Threads Plan

Status: draft

## Goal
- Extend `--cprofile` so profiling captures all threads and merges results into
  a single `.prof` output file.
- Keep standard-library-only implementation and Python 2.7/3 compatibility.

## Non-Goals
- No protocol, transport, or module behavior changes.
- No external profiling tools or dependencies.
- No code changes under ./tests.

## Affected Components
- sfb/cli.py
- sfb/profiling.py
- README.md

## Design Notes
- Implement the cProfile manager in `sfb/profiling.py` to keep CLI logic
  minimal and reusable.
- Use per-thread `cProfile.Profile()` instances and merge with `pstats.Stats`.
- Install a thread wrapper by patching `threading.Thread.run` so each thread
  enables its own profiler and disables it on exit.
- Enable profiling for the main thread before running the CLI so all threads
  spawned by sfb are covered.
- Restore the original `threading.Thread.run` after completion.
- Aggregate thread profiles into a single stats object and dump to the
  resolved `--cprofile` path.
- Document that profiling covers threads started after the wrapper is enabled.

## Plan
1. Add a small multi-thread cProfile manager in `sfb/profiling.py`.
   - Store per-thread `cProfile.Profile` objects in a list guarded by a lock.
   - Wrap `threading.Thread.run` to enable/disable profiling inside each thread.
   - Enable profiling for the main thread and include it in the list.
   - Expose a minimal start/stop API for the CLI to use.
2. Update the `--cprofile` execution path.
   - Start the manager before `_run_main` and stop it in `finally`.
   - Merge per-thread profiles with `pstats.Stats.add` and write one `.prof`.
   - Keep the current path resolution and error handling.
3. Logging.
   - Log that multi-thread profiling is enabled and the output path.
4. Documentation.
   - Update README usage to note that `--cprofile` captures all threads and
     merges results into one file (threads must start after profiling begins).

## Validation
- Manual run with `--cprofile` and confirm the output `.prof` includes work
  from background threads (e.g., relay or tunnel threads).
- Do not run tests/e2e/.
