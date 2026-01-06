# Real Data Segments Plan

## Goal
- Define "real data" as presence of any segments (control or data) for tunnel
  pacing and Bob data-state updates.
- Ensure POLL_HINT remains advisory and does not signal that data was sent.
- Remove the O(n) scan for non-control segments in the hot path.

## Non-Goals
- Change MTU negotiation, retransmit strategy, or transport protocol behavior
  beyond the "real data" definition.
- Add new flags or modify packet encoding formats.
- Update or run E2E tests.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/transport/dns/dns_client.py
- doc/TUNNEL.md
- doc/PROTOCOL.md
- doc/DNS_TRANSPORT.md

## Plan
1) In BaseTunnel packet processing, treat "peer data" as
   FLAG_HAS_SEGMENTS (or bool(packet.segments)) instead of scanning for
   non-control segments. Ensure control-only segments count as real data and
   avoid O(n) checks in the hot path.
2) In DNS client poll handling, call _update_bob_data_state(True) only when
   HAS_SEGMENTS is set. Keep self._retransmit_guard = poll_hint to preserve
   clamp behavior, but do not treat POLL_HINT as data received.
3) Update doc/TUNNEL.md, doc/PROTOCOL.md, and doc/DNS_TRANSPORT.md to state:
   "real data = HAS_SEGMENTS (control or data)" and "POLL_HINT is advisory
   and does not imply data was sent."

## Testing
- Do not run tests here. The user will run E2E tests as needed.
