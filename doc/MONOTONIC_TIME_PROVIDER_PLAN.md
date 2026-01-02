# Monotonic Time Provider Plan

## Goal
Make all timing in the codebase monotonic by routing time reads through a
single provider, so Bob timeouts and every other timeout/duration use the
same monotonic clock.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux.
- ICMP transport remains Linux-only.
- All timing used for timeouts, intervals, pacing, and stats is monotonic.
- Keep public APIs stable unless updated everywhere in the same change.

## Approach
Introduce a small time provider module with a monotonic clock and update all
call sites to use it instead of `time.time()`. Use `wall_time()` only for
user-facing timestamps/metrics that need epoch time.

Monotonic source selection:
- Prefer `time.monotonic()` when available (Python 3).
- On Python 2 / older runtimes:
  - Use `time.clock()` on Windows if available (monotonic wall clock there).
  - Otherwise fall back to `time.time()` wrapped with a non-decreasing clamp
    (store last value, never return a smaller one).
  - Document and accept that backwards system clock jumps can stall timeouts
    when using the clamp fallback.

Provider API:
- `now()`: monotonic seconds as float.
- `sleep(seconds)`: pass-through to `time.sleep` (for consistent patching).
- `wall_time()`: epoch seconds for user-facing timestamps/metrics only.

## Work Items
1. Add `sfb/time_provider.py` with `now()`, `sleep()`, and `wall_time()` using
   the monotonic fallback strategy above; add a tiny module-level lock for the
   clamp path.
2. Replace `time.time()` in production code with `time_provider.now()`:
   - `sfb/tunnel/*`, `sfb/transport/*`, `sfb/reliability/*`, `sfb/channel/*`,
     `sfb/modules/*`, `sfb/logging_util.py`, `sfb/cli.py`.
   - Update defaults that accept `now=None` to call `time_provider.now()` when
     `now` is not provided.
   - Update docstrings that mention `time.time()` defaults.
3. Replace `time.time()` in scripts and tests with `time_provider.now()` where
   they are used for timeouts/intervals. Use `wall_time()` for user-facing
   timestamps/metrics that should remain epoch-based (for example diagnostic
   timestamps in scripts).
4. Standardize on `time_provider.sleep()` for codepaths where tests monkeypatch
   `time.sleep`; update tests to patch `time_provider.sleep` or the module-level
   provider instead of `time.sleep` directly.
5. Document the monotonic requirement and the provider in an existing doc
   (for example `doc/ARCHITECTURE.md` or `doc/RELIABILITY.md`) and update
   `doc/ASYMMETRY.md` to describe Bob timeouts as monotonic silence.
6. Ensure all `time.time()` call sites are updated in this change; no partial
   conversions.

## Acceptance Criteria
- No direct `time.time()` usage remains in runtime code under `sfb/` except
  inside the provider module or an explicit `wall_time()` call.
- Bob timeout logic and all other timeouts use `time_provider.now()`.
- Unit tests and scripts are updated to use the provider where they measure
  durations or deadlines, and patch `time_provider.sleep()` where applicable.
- Docstrings and docs reference monotonic time defaults, including
  `doc/ASYMMETRY.md`.
- Python 2.7 and 3 behavior remains correct on Linux and Windows.

## Testing
- Run targeted unit tests with `python3` (no E2E tests).
