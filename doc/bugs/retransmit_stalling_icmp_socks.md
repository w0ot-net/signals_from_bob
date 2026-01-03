# Excess retransmits and stall bursts (ICMP + SOCKS)

## Summary
- Reported symptom: frequent retransmits and stalls under ICMP tunnel with SOCKS.
- Latest default logs do not include retransmit/recv-window events, so we need a
  debug profile to confirm the rate and causes.

## Latest logs reviewed (2026-01-03)
- Sources: `logs/server_log.db` (Bob) and `logs/client_log.db` (Alice).
- Transport: ICMP, SOCKS module loaded.
- Session timeline: handshake, MTU/window negotiation (1312/128), two SOCKS
  connects (`172.67.177.210:443` and `127.0.0.1:22`), clean shutdown ~56s later.
- Missing signals: no `tunnel.retransmit`, `tunnel.send_blocked`,
  `tunnel.recv_window`, or transport-loss events were logged.

## Logging profile to use
Use log profile `icmp_retransmit_debug` on both sides; it captures:
- `tunnel.packet_*`, `tunnel.ack`, `tunnel.retransmit*`,
  `tunnel.send_blocked`, `tunnel.recv_window`
- `icmp.*`, channel, and SOCKS connection events

## Data to collect
- Fresh DB logs with the profile above while reproducing the stall.
- Clear old logs before the run so the timeline is clean:
  - delete `logs/server_log.db` and `logs/client_log.db`
- Include workload details (command, target, duration).

## Latest findings (2026-01-03, icmp_retransmit_debug)
- Logs captured with `icmp_retransmit_debug` on both sides.
- Timeline windows: Bob ~50.2s, Alice ~15.5s (client log ended earlier).
- Alice `tunnel.packet_send`: 8844; `tunnel.retransmit`: 2787 (~31.5%),
  all `reason=fast_gap`; 2327 unique seqs, max 29 repeats on one seq.
- Alice `tunnel.send_blocked`: 3576 total; 3515 `transport_headroom`
  at `pending=120` (max_in_flight=128, headroom=8).
- Alice `icmp.send`: 87.5% of sends at `pending>=110`; 40.6% at `pending=120`.
- Bob `tunnel.packet_send`: 20404; `tunnel.retransmit`: 480 (~2.4%).
- Bob `tunnel.retransmit_skip`: 19913, mostly `ack_progress` (19753),
  with some `cooldown` (160).
- `tunnel.recv_window` ready counts:
  - Bob ready=0 in 11400/20404 (~56%); Alice ready=0 in 382/8728 (~4.4%).
- No `tunnel.send_window_distance`, `tunnel.send_window_full`, or
  `tunnel.packet_decode_failed` events observed.

## Latest findings (2026-01-03, icmp_retransmit_debug run 2)
- Timeline windows: Bob ~32.9s, Alice ~11.5s (client log ended earlier).
- Alice `tunnel.packet_send`: 5879; `tunnel.retransmit`: 1804 (~30.7%),
  all `reason=fast_gap`; retransmits are data packets (`seg_count` 1/2).
- Alice `tunnel.send_blocked`: 2323 total; 2266 `transport_headroom`
  at `pending=120` (max_in_flight=128, headroom=8).
- Alice `tunnel.packet_recv`: 5761 total; 2111 with non-zero SACK (~36.7%).
  Max repeated ACK run: 118 packets at the same ACK (1210).
- SACK highest-offset histogram: most gaps are small (offset 1 dominates),
  suggesting frequent out-of-order by 1 rather than deep loss.
- Bob `tunnel.packet_send`: 11910; `tunnel.retransmit`: 466 (~3.9%);
  `tunnel.retransmit_skip`: 11433 (mostly `ack_progress`).
- No `icmp.prune_stale` or `tunnel.packet_decode_failed` events observed.

## Change applied (2026-01-03)
- Removed Alice fast retransmit and fast recovery (no `fast_gap` retransmits).
- Expectation: retransmits should be RTO-only; compare rates after re-run.

## Latest findings (2026-01-03, post fast-retransmit removal)
- Timeline windows: Bob ~28.4s, Alice ~23.6s.
- Alice `tunnel.packet_send`: 2091; `tunnel.retransmit`: 169 (~8.1%),
  all `reason=rto`.
- Alice `tunnel.send_blocked`: 12101 total; 11219 `window_distance`,
  468 `transport_headroom`, 78 `retransmit_budget`.
- Alice `tunnel.send_window_distance`: 11220 events with `buffered=128`;
  typical `distance`/`distance_limit` ~236-243 and `unacked` ~108-115.
- Alice `icmp.prune_stale`: 4 (unexpected with 0% loss).
- SACK gaps persist: 560/2077 packet receives had non-zero SACK; highest
  offsets include 137/153, and max repeated ACK run was 239 packets.
- Bob retransmits: 271/2139 (~12.7%) with 1847 skips.

## Additional findings (2026-01-03, post fast-retransmit removal)
- 160/169 Alice retransmits occurred within 0.5s of the most recent response
  (most within ~50ms), indicating RTO firing while responses were active.
- Largest response gap on Alice was ~4.0s; during that window, only 2 packets
  were sent while `tunnel.send_blocked` logged ~2.6k `reason=window_distance`.
- `icmp.prune_stale` total was 9 requests, matching the remaining retransmits
  that occurred after response gaps >= 0.5s.

## Change applied (2026-01-03)
- Gate Alice RTO retransmits on response silence (no responses within RTO).
- Goal: suppress spurious retransmits while polling is active, leaving only
  retransmits tied to actual response gaps.

## Latest findings (2026-01-03, response-silence gate logs)
- Timeline windows: Bob ~28.4s, Alice ~44.0s.
- Alice `tunnel.packet_send`: 1573; `tunnel.retransmit`: 112 (~7.1%),
  all `reason=rto`.
- Alice `tunnel.send_blocked`: 21285 total; 20174 `window_distance`,
  738 `send_window_full`, 317 `transport_headroom`, 56 `retransmit_budget`.
- Alice `icmp.prune_stale`: 5 (still unexpected with 0% loss).
- Largest response gap on Alice: ~10.1s; max repeated ACK run was 243 packets.
- Bob `tunnel.retransmit`: 271; `tunnel.retransmit_skip`: 1847
  (ack_progress 1611, cooldown 236).

## Change applied (2026-01-03)
- Clamp send-window distance to `max_in_flight` (not `max_in_flight + unacked`).
- Goal: prevent senders from getting ahead of the receiver's reorder buffer
  and reduce out-of-window drops that trigger retransmits/stalls.

## Latest findings (2026-01-03, default logs snapshot)
- Sources: `logs/client_log.db` (Alice) had 107486 rows (~09:23:45-09:24:21 UTC);
  `logs/server_log.db` (Bob) had 273722 rows (~09:23:33-09:25:07 UTC).
- Transport: ICMP (`icmp.send`/`icmp.recv` present).
- Alice `tunnel.send_window_distance`: 5515; `tunnel.send_blocked`: 11680
  (5515 `window_distance`, 6094 `transport_headroom`); `tunnel.retransmit`: 25.
- `distance` and `distance_limit` pinned at 128 (max_in_flight/effective_cap).
- `unacked` low while `buffered` high (median `unacked` 4, p90 10, max 117;
  median `buffered` 124, p90 127, max 127), consistent with cumulative ACK
  stalling on a gap while later packets are SACKed.
- `last_cum_ack` stalls in 25 runs at ~0.58-0.61s each (~380-410 events/run);
  max repeated ACK run in `tunnel.packet_recv` was 128 packets.
- SACK non-zero in 6157/12910 packet receives (~47.7%), highest offset 127.
- Bob `tunnel.send_window_distance`: 37; `tunnel.send_blocked`: 37 (all
  `window_distance`); `tunnel.retransmit`: 1965 and `tunnel.retransmit_skip`:
  32294 (ack_progress 31684, cooldown 610).
- Bob `distance` and `distance_limit` pinned at 128; `unacked` median 108
  (max 116) while `buffered` median 20 (max 127).
- Bob `tunnel.recv_window` ready=0 in 12832/34273 (37.4%).
- Bob `tunnel.packet_recv` SACK non-zero in 385/34273 (~1.1%), highest offset 127.

## Change applied (2026-01-03)
- Bob retransmit cooldown now floors to `poll_ewma * max_in_flight` (one window
  of polls) and remains capped by `tunnel_bob_retransmit_max_interval`.
- Goal: reduce opportunistic retransmit spam under fast polling with high
  Bob->Alice reordering while keeping the opportunity-driven model intact.

## Next steps from the latest logs
- Re-run with the same profile to confirm retransmit rate drops with the
  response-silence gate and to quantify any remaining stalls.
- The dominant block is now `tunnel.send_window_distance`; investigate why
  cumulative ACK stalls while `next_seq` advances.
- Re-run with a longer Alice log window to match Bob's timeframe.
