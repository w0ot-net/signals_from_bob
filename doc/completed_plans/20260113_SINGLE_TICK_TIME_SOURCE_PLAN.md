# Single Tick-Time Source Plan

Status: completed

## Summary
Guarantee non-negative age calculations by construction: capture a single
monotonic tick time in the tunnel loops and require it for all send-window
writes and age reads. Remove internal `time_provider.now()` fallbacks and the
`age < 0` clamps so the invariant is enforced by API, not by defensive checks.

## Goals
- Require a caller-provided `now` for all send-window time computations.
- Ensure `send_time`/`first_send_time` are written with the same tick time
  used for age reads.
- Remove `age < 0` clamps once monotonic tick time is enforced.
- Document the single tick-time contract and monotonic requirement.

## Non-Goals
- Change Alice or Bob retransmit policy beyond time plumbing.
- Add new transport behavior or MTU negotiation changes.
- Add or run automated tests; do not touch `tests/` or `tests/e2e/`.

## Affected Components
- `sfb/reliability/send_window.py`
- `sfb/reliability/fast_retransmit.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `sfb/time_provider.py`
- `doc/architecture/RELIABILITY.md`
- `doc/architecture/ALICE_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md`

## Plan
1. Make send-window time usage explicit.
   - Remove `now=None` fallbacks in send-window methods that compute time.
   - Require `now` parameters for `send()`, `mark_retransmit()`,
     `get_retransmits()`, `ack_silence()`, `ack_progress_silence()`,
     `distance_details()`, `debug_state()`, `get_keepalive_drop_info()`, and
     `get_ack_debug_info()`.
   - Delete `age < 0` clamps in send-window debug/state paths once callers
     pass the same tick time.

2. Thread a single tick time through tunnel loops.
   - Capture `now` once per `AliceTunnel.tick()` and `BobTunnel.handle_request()`.
   - Pass that same `now` into every send-window read/write in those paths,
     including retransmit selection and debug logging.
   - Ensure any send-window use outside the main loop is explicitly given a
     `now` from the caller.

3. Enforce a monotonic time source contract.
   - Keep `time_provider.now()` monotonic/clamped in production paths.
   - Treat unclamped time sources as test-only and add a warning or guard if
     used while tunnels are active.

4. Update documentation.
   - Document the single tick-time contract and monotonic requirement in
     reliability and retransmit architecture docs.
   - Remove language that implies age can go negative or is clamped.

## Testing
- Do not run tests unless requested; use `python3` if needed later.

## Execution Notes
- Required caller-provided tick time for send-window timing and removed
  negative-age clamps in reliability/retransmit paths.
- Disallowed unclamped time sources; `set_time_source()` always clamps and
  rejects `clamp=False`.
- Updated retransmit/reliability architecture docs to document the
  single tick-time contract.
