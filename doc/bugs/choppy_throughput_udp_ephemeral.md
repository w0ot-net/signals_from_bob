# Choppy Throughput (UDP Ephemeral, Jan 7 2026)

## Summary
- Symptom: throughput oscillates in ~1s bursts instead of steady flow.
- Primary signals: SACK hole stalls, pacer feedback freezes, and window_distance blocks.
- Impact: Alice send rate drops to the teens/low 30s inflight, then recovers, repeating.

## Logs
- logs/client_log.db (Alice)
- logs/server_log.db (Bob)
- Client window: 2026-01-07 09:55:12-09:55:41 localtime
- Server window: 2026-01-07 09:55:49 localtime

## Observations (Alice)
- `tunnel.pacer_feedback_freeze` flaps with `reason=sack_stall` and `missing_age`
  around 0.51-0.79s; `ack_miss_count` near 98k; `missing_in_unacked=true`.
- `tunnel.pacer_state` / `tunnel.pacer_summary` show `block_reason=window_distance`
  and `target_inflight` falling into ~16-36; send_rate varies ~70-325 pps
  and recv_rate ~70-330 pps per 1s interval.
- `tunnel.send_blocked` is `reason=pacer` with `unacked` ~29-33.
- `tunnel.send_window_distance` shows a persistent missing seq (e.g., 7207) with
  `buffered` ~126-127, `distance=128`, and `missing_in_unacked=true`.
- Loss signals: frequent `tunnel.retransmit` fast retransmits (seq 7204-7207,
  5912-5915, 4786-4788) plus at least one RTO (seq 3055) with send ages
  ~0.39-0.85s.
- `sock.pump_stats` target_to_channel reports `buffer_full` ~1370-1950 per
  interval with `sleep_time` ~0.93-0.97s and bytes ~0.20-0.43 MB.
- `sock.pump_stats` channel_to_target shows ~0 bytes with `wait_time` ~0.997s,
  indicating the return path is mostly idle while the forward path is blocked.
 - Latest run: `tunnel.retransmit_skip` shows `rto_sec` backed off to 8.0s while
   `tunnel.retransmit` fast retransmits fire at `prev_send_age` ~2.0s; this matches
   `tunnel.pacer_feedback_freeze` missing_age values around 2.0s, so the SACK hole
   persists for ~2s before retransmit.

## Observations (Bob)
- `tunnel.retransmit_skip` dominates the last window; no pacer summary events.
- `tunnel.send_window_distance` shows a missing seq (e.g., 10012) in unacked
  with `ack_miss_count` ~19k-21k and `missing_age` ~0.14-0.20s.

## Latest Findings (2026-01-15, ICMP)
- Logs: `logs/client_log.db` (Alice), `logs/server_log.db` (Bob).
- Alice: `tunnel.pacer_summary` shows probe bursts up to ~1,060 pps with
  `target_inflight` ~249-256 followed by window-distance stalls where
  `block_reason=window_distance` cuts `target_inflight` to ~138 and
  `send_rate` drops to ~70 pps.
- Alice: `tunnel.send_window_distance` repeats at `distance=256` with
  `buffered` ~221-223, `unacked` ~33-35, and a persistent `missing_seq=3112`
  in unacked (`missing_retransmit_count` ~4, `missing_age` ~0.10s).
  `ack_miss_count` is ~26k in the same window.
- Alice: `tunnel.pacer_feedback_freeze` fires once with `missing_age` ~0.050s,
  `buffered` ~201, `unacked` 55, then unfreezes ~60 ms later on
  `ack_progress`, so the freeze is short-lived.
- Alice: `tunnel.retransmit` is all `fast_retransmit` with repeated seqs
  (3153-3327) and `prev_send_age` ~0.05-1.40s, indicating recurring SACK holes.
- Bob: `tunnel.send_window_distance` shows `missing_seq=24967` with
  `missing_retransmit_count` ~133-142, `buffered` 247-255, and `unacked` 1-9,
  indicating the hole persists while later packets are already SACKed.

## Latest Findings (2026-01-15, post fast-retransmit change)
- Logs: `logs/client_log.db` (Alice), `logs/server_log.db` (Bob).
- Window: Alice 19:13:05-19:13:16 UTC, Bob 19:12:55-19:14:30 UTC.
- Alice: `tunnel.send_window_distance` count is 0 and `tunnel.pacer_state`
  has 0 `block_reason=window_distance` entries; `tunnel.send_blocked` is
  `reason=pacer` (6,446 events), suggesting the distance cap is not firing.
- Alice: `tunnel.pacer_summary` shows `target_inflight` ~235-237 in feedback
  mode with `send_rate` ~568-1,048 pps; `stat_delta_retransmit_packets`
  remains high (62-155 per second), so fast retransmit is active.
- Bob: `tunnel.send_window_distance` still fires (1,616 events) with
  `missing_flags=4` (keepalive), `missing_retransmit_count` ~97-101,
  `buffered` 254-255, and `unacked` 1-2, indicating the hole persists on
  Bob's send window even though Alice no longer hits distance stalls.
- Bob: `tunnel.retransmit` repeats the same keepalive seq (45333) with
  `reason=window_distance`, `prev_send_age` ~0.0015s, and `first_send_age`
  ~0.45s, showing rapid retransmit churn while the original keepalive stays
  unacked.

## Interpretation
- A single missing packet (SACK hole) blocks cumulative ACK, holds the send
  window at distance=128, and triggers pacer feedback freezes. The block
  penalty and reduced target inflight drive the bursty cadence.
- Retransmit bursts plus pacer clamp explain the oscillation more than simple
  ack silence; the pump backpressure aligns with the pacing drops.
- The pacer gates on `unacked` only, so it can keep sending while distance
  (next_seq - last_cum_ack) climbs during a SACK hole. That allows distance
  to reach the 128 cap even though unacked is low, which triggers the
  window_distance stall.

## Log Review: ACK-Silence Stall (Jan 7, 11:14 run)
- Logs: `logs/client_log.db` (Alice).
- `tunnel.send_blocked` is `reason=pacer` with `inflight=127`, `unacked=1`,
  `cap=128` for long stretches; no `tunnel.packet_send` in the same window.
- `tunnel.retransmit_skip` shows `ack_silence` ~7.9s while `rto_sec` is 10.0s,
  so RTO retransmit is delayed until the max backoff.
- `tunnel.retransmit` has no recent entries in the last 2k events, implying
  fast retransmit is not triggering (no SACK progress).
- Effect: Alice stops sending and Bob sees no packets during the stall window
  even though only one sequence is missing.

## Log Review: DNS Clamp Mode Window (Jan 7, 20:17 run)
- Logs: `logs/client_log.db` (Alice), `logs/server_log.db` (Bob).
- Alice: repeated `tunnel.send_blocked` with `block_reason=window_distance`,
  `inflight` ~119/128; `tunnel.retransmit_skip` shows `ack_silence` ~0.47-0.55s
  with `rto_sec` 10.0s even as `send_oldest_age` is ~10.4-10.5s; `tunnel.ack`
  stays at `ack=1558` with mostly SACK-only acks and minimal `acked_count`.
- Alice: `dns.clamp_select` reports `mode=clamp_max_bob`, `query_payload_cap=60`,
  and `target_response_payload=146` while `poll_hint_mode=keepalive`.
- Bob: `tunnel.response_cap` shows `response_payload_cap=43` with
  `qname_wire_len=200` and `max_packet_size=512`; `tunnel.retransmit_skip`
  is gated by `reason=cooldown` with `unacked` ~65-68.
- Effect: clamp mode selection is active, but the DNS response payload cap is
  far below the target and the cumulative ACK stalls, so window-distance
  blocks dominate and throughput remains bursty.

## Hypotheses
1) SACK hole handling is too conservative. The missing seq persists long
   enough to trigger repeated feedback freezes.
2) Pacer feedback freeze on `sack_stall` is too aggressive when a single hole
   blocks progress; the block penalty shrinks inflight more than necessary.
3) Pump backoff at ~1s aligns with the send stalls, amplifying perceived
   choppiness once the window is blocked.

## Next Steps
- Add a targeted log summary to capture `missing_age`, `ack_miss_count`, and
  `missing_seq` deltas per second to quantify stall duration.
- Consider earlier retransmit of the missing seq when `missing_age` exceeds a
  fraction of RTO or when `ack_miss_count` crosses a threshold.
- Review `tunnel.pacer_feedback_freeze` policy for `sack_stall`; test a less
  aggressive freeze or a smaller block penalty when the window is full due to
  a single hole.
- Compare with ICMP transport under the same load to isolate transport-specific
  stalls versus tunnel/pacer behavior.
