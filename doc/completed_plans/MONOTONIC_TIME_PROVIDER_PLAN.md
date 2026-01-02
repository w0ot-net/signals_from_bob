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
- Standalone scripts under `scripts/` must remain runnable via `python3` from
  the repo root.

## Affected Components
- `sfb/time_provider.py` (new)
- `sfb/tunnel/*` (`alice_tunnel.py`, `bob_tunnel.py`, `base_tunnel.py`)
- `sfb/reliability/send_window.py`
- `sfb/transport/*` (`transport_base.py`, `dns/*`, `icmp/*`, `lossy.py`)
- `sfb/channel/*` (`channel.py`, `channel_manager.py`)
- `sfb/modules/*` (`socks/data_pump.py`, `socks/socks_server.py`,
  `file_transfer/file_transfer.py`)
- `sfb/logging_util.py`, `sfb/cli.py`
- `scripts/icmp_socks_diag.py`, `scripts/icmp_socks_test.py`
- `tests/*` (unit + `tests/e2e/*`)
- Docs: `doc/ASYMMETRY.md`, `doc/RELIABILITY_PERF_CORRECTNESS_PLAN.md`,
  `doc/DNS_TRANSPORT.md`

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
- Expose a small test hook (module-level override of the time source) to
  validate non-decreasing behavior and restore the default.

## Work Items
1. Add `sfb/time_provider.py` with `now()`, `sleep()`, and `wall_time()` using
   the monotonic fallback strategy above; add a tiny module-level lock for the
   clamp path and a test hook to override the underlying time source.
2. Replace `time.time()` in production code with `time_provider.now()`:
   - `sfb/tunnel/*`, `sfb/transport/*`, `sfb/reliability/*`, `sfb/channel/*`,
     `sfb/modules/*`, `sfb/logging_util.py`, `sfb/cli.py`.
   - Update defaults that accept `now=None` to call `time_provider.now()` when
     `now` is not provided.
   - Update docstrings that mention `time.time()` defaults.
   - Keep wall-clock timestamps sourced by the logging module (for example
     `record.created`); only replace local interval math.
3. Replace `time.time()` in scripts and tests with `time_provider.now()` where
   they are used for timeouts/intervals. Use `wall_time()` for user-facing
   timestamps/metrics that should remain epoch-based (for example diagnostic
   timestamps in scripts).
   - Add the repo root to `sys.path` in standalone scripts before importing
     `sfb.time_provider`, so `python3 scripts/...` still works.
   - Keep progress/timeline timestamps on the monotonic clock so duration math
     stays consistent.
4. Standardize on `time_provider.sleep()` for codepaths where tests monkeypatch
   `time.sleep` (notably `sfb/modules/socks/socks_server.py` and
   `sfb/modules/socks/data_pump.py`); update tests to patch
   `time_provider.sleep` or the module-level provider instead of `time.sleep`
   directly.
5. Add unit coverage for the provider:
   - `time_provider.now()` is non-decreasing under a mocked time source.
   - Default source is restored after tests.
6. Document the monotonic requirement and the provider:
   - Update `doc/RELIABILITY_PERF_CORRECTNESS_PLAN.md` to align with the shared
     time provider and monotonic silence (no wall-clock exception).
   - Update `doc/ASYMMETRY.md` and `doc/DNS_TRANSPORT.md` only if they do not
     already reference `time_provider.now()`.
7. Ensure all `time.time()` call sites are updated in this change; no partial
   conversions.

## Acceptance Criteria
- No direct `time.time()` usage remains in runtime code under `sfb/` except
  inside the provider module or an explicit `wall_time()` call.
- Bob timeout logic and all other timeouts use `time_provider.now()`.
- Unit tests and scripts are updated to use the provider where they measure
  durations or deadlines, and patch `time_provider.sleep()` where applicable.
- Standalone scripts still run via `python3 scripts/...` with `time_provider`
  imports resolving.
- `time_provider.now()` has coverage proving non-decreasing behavior.
- Docstrings and docs reference monotonic time defaults, including
  `doc/ASYMMETRY.md`, `doc/RELIABILITY_PERF_CORRECTNESS_PLAN.md`, and
  `doc/DNS_TRANSPORT.md`.
- Python 2.7 and 3 behavior remains correct on Linux and Windows.

## Testing
- Run targeted unit tests with `python3` (no E2E tests).
- Include the new time provider test module in the unit run.

## Execution Notes
- Added `sfb/time_provider.py` with monotonic selection, clamp fallback, and test hooks; added `tests/test_time_provider.py`.
- Replaced runtime timing and sleeps with `time_provider.now()`/`time_provider.sleep()` across `sfb/`, scripts, and tests; updated script `sys.path` setup for standalone runs.
- Updated `doc/RELIABILITY_PERF_CORRECTNESS_PLAN.md` and `doc/DNS_TRANSPORT.md` to reference the shared monotonic provider.
- Tests run: `python3 -m unittest tests.test_time_provider`.
