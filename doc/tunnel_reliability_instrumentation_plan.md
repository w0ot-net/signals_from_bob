# Tunnel Reliability Instrumentation Plan

## Summary
Add comprehensive structured logging across tunnel send/recv, reliability
windows, and retransmit decision points so we can reconstruct subtle stalls and
state mismatches from logs without changing behavior.

## Goals
- Capture enough detail to replay tunnel state transitions (seq/ack/sack, MTU,
  window, pacing, retransmit gating) from logs.
- Keep overhead low by gating heavy instrumentation behind log profiles and
  sampling; default logging remains unchanged.
- Preserve asymmetry rules: Alice initiates/polls and uses RTT-driven
  retransmit; Bob only responds to polls and retransmits opportunistically.
- Maintain Python 2.7/3 compatibility, standard library only, Windows and
  Linux support (ICMP logs remain Linux-only by transport).

## Affected Components
- `sfb/tunnel/base_tunnel.py` (packet send/recv, ACK processing, MTU/window negotiation logs)
- `sfb/tunnel/alice_tunnel.py` (poll/tick, retransmit timing, keepalive decisions)
- `sfb/tunnel/bob_tunnel.py` (poll response construction, opportunistic retransmit)
- `sfb/tunnel/pacing.py` (pacing state snapshots)
- `sfb/reliability/send_window.py` (debug snapshots and retransmit/ACK context)
- `sfb/reliability/recv_window.py` (debug snapshots and receive/drop context)
- `sfb/reliability/rtt.py` (RTT/backoff snapshot helpers)
- `sfb/reliability/stats.py` (optional structured snapshot helper)
- `sfb/log_profiles.py` (new instrumentation profile and whitelist updates)
- `sfb/config.py` (optional knobs for snapshot cadence/sampling)
- `doc/LOGGING.md` (new events/profile documentation)
- `doc/ALICE_RETRANSMIT_LOGIC.md` (event mapping for Alice decisions)
- `doc/BOB_RETRANSMIT_LOGIC.md` (event mapping for Bob decisions)
- `doc/bugs/retransmit_stalling_icmp_socks.md` (updated capture guidance if needed)

## Plan
1. Audit existing tunnel/reliability logs and define a shared event schema:
   - Catalog current `tunnel.*` events and identify gaps (retransmit selection,
     ACK silence, MTU/window negotiation, keepalive suppression).
   - Define standard fields to include when available: `side`, `state`,
     `seq/ack/sack`, `flags`, `seg_count`, `bytes`, `send_mtu`, `recv_mtu`,
     `window`, `max_in_flight`, `unacked`, `recv_buffered`, `rto_ms`, `srtt_ms`,
     `backoff`, `pacer_target`, `transport_blocked`, `poll_id`, `tick_id`.
   - Add a stable per-tunnel identifier (ex: `local_isn` or an explicit
     `tunnel_id`) to all new events for cross-log correlation.
2. Add reliability snapshot helpers:
   - `SendWindow.debug_state(now)` with unacked counts, oldest seq age,
     retransmit counts, ACK miss history, keepalive drop summary.
   - `RecvWindow.debug_state()` with next expected, buffer size, and
     out-of-window/duplicate counters.
   - `RttEstimator.debug_state()` with srtt/rto/backoff values.
   - Keep all helpers lightweight and computed on demand to avoid hot-path cost.
3. Instrument tunnel send/recv and ACK handling:
   - Emit `tunnel.packet_send` and enrich `tunnel.packet_recv` with
     size/MTU/window context.
   - Add `tunnel.ack_progress` when cumulative ACK advances (include silence
     duration and unacked deltas).
   - Add `tunnel.send_window_state` and `tunnel.recv_window_state` snapshots
     after ACK processing or when drops occur.
   - Log MTU/window negotiation with asymmetric values (`send_mtu` vs
     `recv_mtu`) and effective caps.
4. Instrument retransmit decision points with explicit reasons:
   - Alice: log RTO-driven selection, backoff, send_window_distance gate,
     transport blocked, and resend outcomes (`tunnel.retransmit_*`).
   - Bob: log opportunistic retransmit selection, payload cap gating, and
     skipped reasons; include age of oldest unacked.
   - Keepalive: log keepalive send vs suppression when data is pending (do not
     change suppression behavior).
5. Add a dedicated log profile for heavy instrumentation:
   - New profile (ex: `tunnel_reliability_verbose`) that whitelists the new
     `tunnel.*` reliability/retransmit events plus relevant transport logs.
   - Update `icmp_retransmit_debug` to include new events so existing
     troubleshooting flows still capture them.
   - If needed, add a config knob for snapshot cadence (ex: log every N packets
     or when state changes) to control volume.
6. Update documentation with the new event map:
   - `doc/LOGGING.md`: document the new profile, event names, and key fields.
   - `doc/ALICE_RETRANSMIT_LOGIC.md` and `doc/BOB_RETRANSMIT_LOGIC.md`: map new
     events to retransmit decisions and skip reasons.
   - `doc/bugs/retransmit_stalling_icmp_socks.md`: update capture instructions
     to include the new profile/event list if it improves diagnosis.
7. Add minimal unit coverage for instrumentation helpers (optional):
   - Validate snapshot helper output keys/values and Python 2.7/3 compatibility.
   - Keep tests in unit scope only; do not add or run E2E tests.

## Success Criteria
- Using the new log profile, we can reconstruct retransmit decisions and ACK
  stalls with clear, structured events on both sides.
- Logs show asymmetric MTU/window values and keepalive suppression decisions.
- Default logging behavior and performance are unchanged; heavy logs are gated
  behind profiles/sampling.
