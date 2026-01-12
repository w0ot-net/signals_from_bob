# Alice Keepalive Window Full Retransmit Plan

Status: draft

## Summary
Stop dropping unacked keepalives when Alice's send window is full. Instead,
reuse the existing keepalive sequence (retransmit) or skip the keepalive when
there is no keepalive to reuse, so cumulative ACK holes cannot be made
permanent by local bookkeeping.

## Goals
- Preserve reliable sequencing by never removing keepalive seq entries from the
  send window while they are unacked.
- Keep the poll/response flow alive under window pressure by retransmitting an
  existing keepalive when appropriate.
- Add clear logging for window-full keepalive decisions to aid stall diagnosis.

## Non-Goals
- Change the RTO/fast-retransmit algorithms or pacing caps.
- Alter transport behavior, DNS response caps, or ICMP limitations.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/alice_tunnel.py`
- `sfb/reliability/send_window.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Add a send-window helper to locate the oldest unacked keepalive.
   - Return seq/segments/flags/encrypted_body plus timing for logging.
   - Use explicit loops (no comprehensions) for PY2 safety.

2. Replace keepalive drop behavior on window-full sends.
   - In `_send_keepalive_or_break`, if the window is full, attempt a
     keepalive retransmit of the oldest keepalive instead of dropping it.
   - If no keepalive exists, log a keepalive skip and release the permit.

3. Tag and log keepalive retransmits triggered by window-full conditions.
   - Use a specific `reason` string (for example `window_full_keepalive`).
   - Emit a log event when keepalive is skipped due to a full window and no
     keepalive candidate, to surface potential stalls.

4. Update protocol notes.
   - Document that keepalive-only packets remain part of the reliable stream
     even under window-full pressure, and that window-full keepalives reuse
     existing seqs rather than dropping them.

## Testing
- Do not run tests.
