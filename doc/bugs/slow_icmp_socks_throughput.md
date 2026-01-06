# Slow ICMP SOCKS Throughput (local-to-local)

## Summary
- **Symptom:** ICMP transport with SOCKS proxy on localhost (Bob+Alice both local) downloads `2MB.bin` in ~10s (~0.16 MB/s aggregate, ~0.19 MB/s client rate).
- **Expected:** Near-localhost speeds; direct HTTP baseline delivers 2MB in ~15ms (~138 MB/s).
- **Status:** Body truncation fixed (relay stop-event change). Throughput remains very low.

## Observations
- Direct HTTP baseline: 2MB in ~15ms (diag script output). Tunnel path: ~10s for 2MB.
- Progress timeline shows early burst (~1.1 MB/s) then decays to ~0.2 MB/s.
- TTFB is fast (~20ms), so handshake/module load are not the bottleneck.
- ICMP transport uses raw sockets; polling/retransmit pacing likely limiting throughput.
- Default SOCKS relay buffer size is small (2048 bytes); relay sleep/backoff may be slowing reads.

## Hypotheses to Explore
1) **Relay buffer/poll throttling:** `relay_buffer_size` (2048) and `non_blocking_poll_timeout` backoffs could be capping throughput.
2) **Channel send window/backpressure:** Tunnel window/MTU negotiation may be small; send window growth may be slow.
3) **ICMP payload MTU:** Default 1200 payload MTU; small payloads increase per-packet overhead.
4) **Polling cadence (Alice/Bob):** `tunnel_send_rate` and retransmit/poll intervals may be conservative for local links.
5) **Pending/queue limits:** `max_in_flight`/`icmp_pending_timeout` could be constraining in-flight requests.

## Next Diagnostic Steps
- Run `scripts/icmp_socks_diag.py` with tunables to see rate impact:
  - Increase ICMP MTU: `--icmp-packet-mtu 1400` (or larger if path allows).
  - Increase relay buffer: config override (see below) to 8192 or 16384.
  - Allow unlimited send: `--send-rate 0 --send-burst 0`.
  - Try multiple clients to see if aggregate scales or stalls.
- Compare timelines at each setting; note peak rate and steady-state rate.
- Enable detailed SOCKS/tunnel logging to spot pauses/backoffs.

## Proposed Logging Profile for This Debugging
Add a temporary profile (e.g., `socks_throughput_debug`) that:
- Enables SOCKS and channel debug:
  - Include: `sock.pump_stats`, `sock.relay_*`, `channel.send_buf_*`, `channel.drain`, `channel.write_wait`
- Enables ICMP transport debug:
  - Include: `icmp.send`, `icmp.recv`, `icmp.send_blocked`
- Enables tunnel pacing/window:
  - Include: `tunnel.window*`, `tunnel.send_blocked`, `tunnel.packet_*` (optionally limited to INFO to avoid overload)
- Keep blacklist minimal; focus on rate-related events.

Profile sketch (not yet added):
```
LOG_PROFILES['socks_throughput_debug'] = {
  'log_component_module_relay': True,
  'log_component_transport_icmp': True,
  'log_component_tunnel': True,
  'log_event_whitelist': (
    'sock.pump_stats',
    'sock.relay_*',
    'channel.send_buf_*',
    'channel.drain',
    'channel.write_wait',
    'icmp.send',
    'icmp.recv',
    'icmp.send_blocked',
    'tunnel.window*',
    'tunnel.send_blocked',
    'tunnel.packet_*',
  ),
  'log_event_blacklist': (),
}
```

## Quick Config Tweaks to Try (runtime)
- Relay buffer: `--relay-buffer-size 8192`
- Pump backoff max: `--relay-pump-backoff-max 0.05` (or smaller)
- Channel max send buf: `--channel-max-send-buf 65536` (if safe)
- ICMP MTU: `--icmp-packet-mtu 1400`
- Send rate/burst: `--send-rate 0 --send-burst 0`

## Action Items
- Define and apply the `socks_throughput_debug` profile for targeted logs.
- Capture timeline + log snippets for baseline vs tuned runs.
- Inspect logs for gaps between pump stats and ICMP sends to find stalls.

## Experiment Log: Higher MTU + Larger Buffers (Dec 31, 2025)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 1 --target 127.0.0.1 --timeout 120 \
    --log-profile socks_throughput_debug --verbose-cli \
    --relay-buffer-size 8192 --channel-max-send-buf 65536 \
    --icmp-packet-mtu 1400 --send-rate 0
  ```
- Outcome:
  - SOCKS path: 2MB in ~3.06s; throughput ~0.654 MB/s per client, aggregate ~0.395 MB/s; TTFB ~20 ms; peak ~0.728 MB/s.
  - Direct HTTP baseline: ~0.025s (~79 MB/s).
  - Logs: `logs/icmp_diag_client_log.db`, `logs/icmp_diag_server_log.db`.
- Log highlights:
  - `tunnel.send_blocked`: 1,156 events at `unacked=64`/`max_in_flight=64` (plus 2 at unacked=1), indicating the in-flight/window limit is hit frequently.
  - SOCKS pump stats (Alice target->channel) still show heavy backpressure even with 64 KB buffer:
    - `buffer_full` ~1700–1800 per interval, `send_buf_size` ~65536, `sleep_time` ~0.7s.
  - Bob pump stats (channel->client) are steady (~0.64–0.75 MB per interval) with no buffer_full issues.
- Takeaways:
  - Raising MTU and buffers improved throughput to ~0.65 MB/s but the main bottleneck remains Alice-side in-flight limits and channel backpressure.
  - ICMP pending/window saturation (unacked=64) is the dominant limiter; channel send buffer remains full much of the time.
- Next experiments:
  - Increase buffers further (e.g., `--relay-buffer-size 16384`, `--channel-max-send-buf 131072`).
  - Increase ICMP concurrency/window (consider bumping `max_in_flight` beyond 64) and ensure send window can grow; keep `--send-rate 0` and omit `--send-burst` to allow defaults.
  - Optionally reduce pump backoff (`non_blocking_poll_timeout`, `relay_pump_backoff_max`) if we add config overrides for them.

## Experiment Log: Smaller Backoff + Larger Buffers (Dec 31, 2025)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 1 --target 127.0.0.1 \
    --icmp-packet-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug --verbose-cli \
    --relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --relay-pump-backoff-max 0.002 --non-blocking-poll-timeout 0.00002
  ```
- Outcome:
  - SOCKS path: 2MB in 3.06s; throughput ~0.654 MB/s (client), aggregate ~0.394 MB/s; TTFB ~43 ms; peak ~0.729 MB/s.
  - Direct HTTP baseline: ~0.029s (~69 MB/s).
  - Logs: `logs/icmp_diag_client_log.db`, `logs/icmp_diag_server_log.db`.
- Log highlights:
  - `tunnel.send_blocked`: 1,270 events (1,266 at `unacked=64`, 4 at `unacked=1`), so the 64 in-flight cap is still the dominant limiter.
  - Alice pump stats (target->channel): `buffer_full` ~2.6k per interval despite 256 KB send buffer; `sleep_time` ~0.6s.
  - Bob pump stats (channel->client) remain steady (~0.63–0.74 MB per interval) with no buffer_full.
- Takeaways:
  - Tightening pump backoff/poll intervals and enlarging buffers to 32 KB / 256 KB did not materially improve throughput (~0.65 MB/s persists).
  - The channel remains backpressured and Alice keeps hitting the 64 packet in-flight limit; further gains likely require reducing backpressure or allowing more in-flight packets without exceeding MTU 1400.

## Experiment Log: Near-Zero Backoff/Poll (Dec 31, 2025)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 1 --target 127.0.0.1 \
    --icmp-packet-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug --verbose-cli \
    --relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --relay-pump-backoff-max 0.0001 --non-blocking-poll-timeout 0
  ```
- Outcome:
  - SOCKS path: 2MB in 2.93s; throughput ~0.682 MB/s (client), aggregate ~0.398 MB/s; TTFB ~41 ms; peak ~0.729 MB/s.
  - Direct HTTP baseline: ~0.035s (~57.7 MB/s).
  - Logs: `logs/icmp_diag_client_log.db`, `logs/icmp_diag_server_log.db`.
- Log highlights:
  - `tunnel.send_blocked`: 1,285 events (1,281 at `unacked=64`, 4 at `unacked=1`); 64 in-flight cap still dominant.
  - Alice pump stats (target->channel): `buffer_full` surged to ~9.3k–9.7k per interval; `send_buf_size` 262144; `sleep_time` 0.0 (busy-looping).
  - Bob pump stats steady (~0.70–0.74 MB per interval) with no buffer_full.
- Takeaways:
  - Eliminating backoff/poll delay slightly improved per-client throughput (~0.68 MB/s vs ~0.65 MB/s) but increased buffer_full count dramatically, suggesting we are spinning while stuck on the same backpressure.
  - The in-flight/window ceiling and channel backpressure remain the primary bottlenecks; further gains likely need reducing channel saturation or relaxing the 64 packet limit (while keeping MTU <= 1400).

## Experiment Log: Multiple Clients (4x) with Near-Zero Backoff (Jan 1, 2026)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 4 --target 127.0.0.1 \
    --icmp-packet-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug \
    --relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --relay-pump-backoff-max 0.0001 --non-blocking-poll-timeout 0
  ```
- Outcome:
  - SOCKS path: 4x2MB total in ~86s; per-client throughput ~0.023 MB/s; aggregate ~0.089 MB/s.
  - TTFB per client ~0.50-0.87s; durations ~85-86s.
  - Direct HTTP baseline: ~0.026s (~76.8 MB/s).
  - Logs: `logs/icmp_diag_client_log.db`, `logs/icmp_diag_server_log.db`.
- Takeaways:
  - Adding clients did not increase aggregate throughput; it significantly reduced per-client throughput.
  - The run appears globally throttled (aggregate ~0.09 MB/s) rather than scaling with more clients.
  - The timeline "peak rate" is likely an artifact of the final flush (very small delta time).

## Experiment Log: Multiple Clients (8x) with Near-Zero Backoff (Jan 1, 2026)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 8 --target 127.0.0.1 \
    --icmp-packet-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug \
    --relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --relay-pump-backoff-max 0.0001 --non-blocking-poll-timeout 0
  ```
- Outcome:
  - SOCKS path: 8x2MB total in ~175-177s; per-client throughput ~0.011-0.012 MB/s; aggregate ~0.089 MB/s.
  - TTFB per client ~0.21-0.81s.
  - Direct HTTP baseline: ~0.022s (~91.9 MB/s).
  - Logs: `logs/icmp_diag_client_log.db`, `logs/icmp_diag_server_log.db`.
- Takeaways:
  - Aggregate throughput remains ~0.09 MB/s regardless of client count; per-client rate halves when doubling clients.
  - The system appears capped at a global throughput ceiling rather than per-client limits.

## Experiment Log: Choppy Throughput Baseline (Jan 3, 2026)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Observed stalls:
  - Alice `icmp.send` and `icmp.recv` show max inter-arrival gaps near 1.0s (median ~0.0006s, p95 ~0.004s).
  - `tunnel.ack` intervals show the same ~1.0s max gap, indicating periodic poll/response stalls.
- Window/backpressure:
  - Alice `tunnel.send_blocked` includes `window_distance` (625); remaining blocks were other reasons.
  - `tunnel.send_window_distance` is constant at 128 (min/max/avg 128), matching the max-in-flight cap.
  - `tunnel.pacer_state` shows `target_inflight` capped at 128 with `target_mode` always `base`; no dynamic increase observed.
- Pump stats:
  - Alice `sock.pump_stats` (target_to_channel) shows high `buffer_full` counts and `sleep_time` around 0.94s during throughput plateaus.
  - Channel drain logs show 0.48-0.76 MB per 1.0s interval with bursty cadence.
- Retransmit gating:
  - Alice `tunnel.retransmit_skip` dominated by `ack_silence` (2,562) with max `ack_silence` ~0.49s (rto 0.5s), suggesting retransmit gating is not the primary stall source.
- Takeaways:
  - Throughput choppiness aligns with periodic ~1s gaps in poll/response cadence plus a fixed 128 in-flight cap that keeps the send window saturated.
  - Next step: test higher `max_in_flight` (if safe), or smooth the poll cadence to avoid 1s gaps; re-run with the same logging profile to confirm gap reduction.

## Experiment Log: Choppy Throughput (Jan 3, 2026, second run)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Alice SOCKS relay backpressure:
  - `sock.pump_stats` (target_to_channel) shows `send_buf_size` hitting 1,048,576 (channel max) with `buffer_full` > 0 and `wait_time` ~0.94-1.17s per interval.
  - `sock.pump_stats` (channel_to_target) shows `channel_timeouts` ~20 per interval with `wait_time` ~1.0s and no bytes, indicating idle return path during the run.
- Tunnel saturation on Alice:
  - `tunnel.pacer_state` target inflight stays at 128; `unacked_count` clusters at 116-120, indicating the in-flight cap is regularly hit.

## Experiment Log: Adaptive Pacing Baseline (Jan 4, 2026)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Pacer behavior (Alice):
  - `tunnel.pacer_summary` shows send_rate ~419 pps and recv_rate ~420 pps with srtt ~115-125 ms and ack_rate_ewma ~260-300 pps.
  - `pacer_target_inflight` ramps from ~90 to ~158 in probe mode while `pacer_unacked_count` stays near ~50.
  - `tunnel.send_blocked` is only `reason: pacer`, clustered early when target_inflight ~40-50; no blocking once probe target exceeds ~120.
- Packet sizing:
  - `tunnel.packet_send` reports `send_packet_mtu` 1312 bytes and `bytes` 1350 per packet.
  - At ~419 pps this is ~0.55 MB/s payload, matching `sock.pump_stats` per-interval bytes.
- Pump backpressure:
  - `sock.pump_stats` (target_to_channel) shows `buffer_full` ~2300 per interval with `send_buf_size` 1048576 and `sleep_time` ~0.95s.
- Takeaways:
  - Adaptive pacer is not the steady-state limiter; it allows inflight well above current unacked.
  - Throughput is governed by packet size times ~420 pps and channel backpressure; raise MTU or increase packet rate to improve.
  - `tunnel.packet_send` shows has-data bursts (300-580 packets/sec) but only across ~8 seconds of the last 60s window; the rest of the window is mostly idle/keepalive.
- Bob side shows asymmetric traffic:
  - `tunnel.packet_send` has-data = 6 of 10,411 packets; `channel.pack` totals 960 bytes over the last 10 minutes.
  - `tunnel.packet_recv` carries ~12.7 MB, indicating traffic is predominantly Alice->Bob for this run.
- Takeaways:
  - The choppiness aligns with Alice-side channel send buffer saturation and the 128 in-flight cap; bursts drain the buffer, then the relay waits for space.
  - For smoother throughput, the limiting factors remain the in-flight window and poll cadence rather than retransmits.

## Log Review: Pacing vs Polling (Feb 4, 2026)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Alice pacing vs ack rate:
  - `tunnel.pacer_state` shows `target_inflight=128` with `target_mode=base` while `feedback_target` hovers around ~33 (ack_rate_ewma ~230 pps, srtt ~147 ms).
  - `unacked_count` commonly 108-114; pacer is not reducing inflight based on feedback.
- Alice send gating:
  - `tunnel.send_window_distance` repeats with `distance=128`, `unacked=1`, `buffered=127`, indicating a single missing seq stalls new sends.
  - Retransmits occur, but subsequent sends are still blocked until the missing seq is acked.
- Bob retransmit cooldown:
  - `tunnel.retransmit_skip` on Bob uses `poll_ewma~0.0013s` and `window=128`, yielding `cooldown~0.16s` and repeated skips while `unacked` remains 80-90.
- Takeaways:
  - Alice is polling very aggressively (sub-2ms EWMA), but inflight is capped by window-distance stalls; the feedback target suggests a lower inflight may reduce gaps.
  - Bob’s cooldown is driven by Alice’s high poll rate and window size, which can defer opportunistic retransmits during loss.

## Log Review: Feedback-Driven Pacing (Jan 3, 2026, post-change)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Throughput (Alice `sock.pump_stats`, target_to_channel):
  - ~3.42 MB over ~46.16s, ~74 KB/s (~0.074 MB/s).
- Pacer adjustments (Alice):
  - `tunnel.pacer_adjust`: 140 total; 139 `window_distance`, 1 other.
  - `tunnel.pacer_target` target_inflight min/avg/max: 1 / ~16.0 / 128.
  - `tunnel.pacer_target` mode: feedback 852, probe 46, base 18.
  - `feedback_target` avg ~31 vs `base_target` avg ~128 (feedback/base ~0.24).
- Window-distance stalls (Alice):
  - `tunnel.send_blocked`: 28,712 `window_distance` events.
  - `tunnel.send_window_distance` unacked most common: 1 (16,821), 2 (3,058), 4 (1,613).
- Logging gap:
  - No `tunnel.pacer_summary` events; summary interval or profile likely disabled.
- Takeaways:
  - Feedback-driven pacing reduced inflight to low teens during heavy window-distance stalls.
  - To quantify the 10% throughput dip, enable `tunnel_pacer_summary_interval=1.0` and compare send_rate deltas alongside blocked counters.

## Log Review: Feedback-Driven Pacing (Jan 4, 2026, latest)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Pacer feedback target:
  - `tunnel.pacer_target` shows `target_mode=feedback` with `ack_rate_ewma` ~10-75 pps and `srtt_ms` ~95-105, so feedback pipe ~1-7 packets (gain 1.0).
  - Example: `ack_rate_ewma=75.17`, `srtt_ms=102.8` -> `feedback_target=7`.
- Block penalty collapse:
  - `tunnel.pacer_adjust` shows `block_penalty` rising to 6 on `window_distance`, reducing `target_inflight` from 7 to 1 (`block_target=1`).
- Distance guard:
  - `tunnel.send_window_distance` repeats with `distance=5`, `distance_limit=1`, `effective_cap=1`, `unacked=1`, `buffered=4`, so Alice is hard-stalled by the cap.
- Takeaways:
  - Feedback-driven pacing is active but collapses the effective cap to 1 due to low ack-rate feedback plus window-distance block penalties, explaining the 10% throughput.

## Log Review: Feedback-Driven Pacing (Jan 4, 2026, 04:52 run)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Pacer feedback target:
  - `tunnel.pacer_target` shows `ack_rate_ewma` ~159-209 pps and `srtt_ms` ~110, so feedback target ~17-23.
  - `block_penalty` is 26 on `window_distance`, but `block_target` equals `feedback_target` (target inflight 17-23), so the cap is not collapsing to 1.
- Distance guard:
  - `tunnel.send_window_distance` repeats with `distance` 19-21 and `effective_cap` 17-21; `unacked` tracks near the cap with `buffered` 0-1.
- Stall counts:
  - Alice `tunnel.send_blocked`: 5,876 events, all `reason=window_distance` (no `pacer`/`rate_limit` blocks).
  - Alice `tunnel.retransmit_skip`: 5,875 events, all `reason=ack_silence`; `ack_silence` p50 ~0.012s, p90 ~0.049s (rto 0.5s).
  - Bob `tunnel.retransmit_skip`: 4,981 events, mostly `reason=cooldown`; cooldown p50 ~0.485s, p90 ~1.28s, max 3.0s.
- Takeaways:
  - Latest run no longer shows the effective cap pinned at 1; window-distance stalls still happen, but at mid-teen caps instead of single digits.

## Log Review: Pacer Oscillation Check (Jan 4, 2026, latest)
- Logs: `logs/client_log.db` (recent `tunnel.pacer_target` / `tunnel.pacer_state`).
- Pacer target samples:
  - `tunnel.pacer_state` shows `target_mode=probe` with `probe_extra` ~66-67 and `feedback_target` ~17-26, yielding `target_inflight` ~83-92; `block_penalty` stays 0.
  - `tunnel.pacer_target` adjustments are typically 1-3 packets as `feedback_target` shifts; no 50% drops in the latest sample window.
- Takeaways:
  - The latest logs look like steady probe-mode pacing rather than aggressive halving; if you are seeing a half-cut, it may be a probe reset when ack rate drops (look for `probe_extra` dropping to 0).
  - The mid-teen `effective_cap` matches the observed ~1/8 throughput vs `max_in_flight=128`.

## Log Review: Feedback-Driven Pacing (Jan 3, 2026, 23:52 run)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Alice pacing:
  - `tunnel.pacer_target` target_inflight avg ~27 (median 27, max 128) with `ack_rate_ewma` ~240 pps and `srtt_ms` ~114.
  - `tunnel.pacer_state` block_reason is `window_distance` in 6964/7099 entries, so feedback pacing is dominating the send cap.
- Alice send blocking:
  - `tunnel.send_blocked`: 16336 `window_distance`; 3 `retransmit_budget`.
  - `distance_limit` avg ~26.7 (median 27); `unacked` avg ~24; `buffered` avg ~6.3 (max 65).
- Payload efficiency:
  - `channel.pack` avg payload ~1308/1312; keepalive count 0.
- Retransmit gating:
  - `tunnel.retransmit_skip`: 16537 (all `ack_silence`), with `ack_silence` spikes up to ~2.09s.
  - `rtt_rto_ms` up to 4000 (backoff count max 3); only 9 retransmits (all `reason=rto`, ages 3.8-4.4s).
- ACK anomalies and burstiness:
  - `send_ack_miss_count` median ~3335 (max 4637) despite low median `ack_silence`.
  - Bob ack progress appears in 5325/18748 `tunnel.ack_detail` rows; when progress occurs, avg `acked_count` ~3.5 (max 66).
- Takeaways:
  - Feedback-driven pacing caps inflight around ~27 packets (far below the 128 window), matching the observed throughput ceiling.
  - Stalls align with window-distance pacing plus occasional multi-second ack silence, not heavy retransmit churn.

## Log Review: Poll Pacing (latest run)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Poll pacing: `tunnel.poll_pace` interval moved from 1.0s (no SRTT) to 0.001s once SRTT was available; no further interval changes observed.
- Pacer summary (Alice): send/recv ~380-430 pps with unacked/pending ~43-57; `target_inflight` rose 58->158 in probe mode while `feedback_target` stayed ~31-34 (SRTT ~115-126 ms).
- SOCKS pump backpressure (Alice target_to_channel): `sock.pump_stats` shows `buffer_full` ~2.1k/interval and `wait_time` ~0.88-0.95s, so the pump waits almost the entire second for channel send space.
- Retransmits: no `tunnel.retransmit` events; only `tunnel.retransmit_skip` (Alice: `ack_silence`, Bob: `cooldown`), so choppiness is not driven by retransmits.
- Payload efficiency: `channel.pack` payload_bytes ~1309/1312, so packets are full.
- Takeaways:
  - Throughput appears capped by packet rate (pps) plus channel backpressure, not by retransmits.
  - Next levers to test: higher `max_in_flight`, reduced pump backoff, or tighter poll pacing to smooth stall gaps.

## Log Review: Throughput Oscillation (Jan 4, 2026, latest)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Alice channel drain: per-1s `bytes_total` climbed from ~170 KB/s to ~527-552 KB/s, then dropped to ~185 KB/s at 07:03:13 (startup low points at 32-109 B/s).
- Bob channel drain: repeated low intervals (~10-60 KB/s) interleaved with 300-465 KB/s bursts (e.g., 07:03:13 at 10,472 B/s, 07:03:14 at 60,214 B/s, then back to 278-452 KB/s).
- Alice pump backpressure during valleys: `sock.pump_stats` (target_to_channel) shows `buffer_full` and `sleep_time` ~1.15s with only ~97 KB drained in that second; `channel_to_target` shows ~1s waits with 0 bytes.
- Bob pump backpressure during valleys: `sock.pump_stats` (client_to_channel) shows `buffer_full` and `sleep_time` ~0.96-1.23s; `channel_to_client` shows ~1s waits with 0-5 KB.
- Retransmit gating: Alice `tunnel.retransmit_skip` spikes with `ack_silence` ~0.33-0.40s around the 07:03:13 valley; Bob skips due to `cooldown` with poll_ewma ~2-5 ms.
- Takeaways:
  - The peaks and valleys line up with 1s-scale pump backoff/idle waits, consistent with channel backpressure rather than loss-driven retransmits.
  - Poll pacing looks steady in this run (interval ~1.0-1.27 ms), so smoothing likely needs backpressure or pacing adjustments rather than retransmit changes.

## Log Review: SACK Hole Stalls (Jan 4, 2026, latest)
- Logs: `logs/client_log.db`.
- Send window distance stalls: 798 `tunnel.send_window_distance` events with `distance=128` and `distance_limit=128`; `buffered` avg ~120.8 while `unacked` avg ~7.2 (min 4), so the window is full mostly due to buffered, not unacked, packets.
- Missing packet is in unacked: `missing_in_unacked=true`, `missing_seq=last_cum_ack`, `missing_age` up to ~1.72s while `ack_miss_count` is very high (avg ~7.9k, max ~8.5k), indicating repeated SACKs acking ahead but a single hole blocking cumulative ACK.
- Cumulative ACK does not advance past the hole: `tunnel.ack` max `ack=2984`; RTO retransmits only cover seq 2980-2983 (no `tunnel.retransmit` for seq 2984), so the missing packet appears to persist through shutdown.
- Pacer cap is low during stalls: `pacer_cap` ranges 11-114 (avg ~17.8), but `unacked` is already below that, so the stall is not from pacer gating.
- Transport pending not saturated: `icmp.send` pending averages ~45 (max 73) with no `icmp.send_blocked`, so ICMP is not hitting the 128 in-flight cap.
- Retransmits remain gated by `ack_silence` (~0.39-0.41s vs rto 0.5s), so the missing packet can sit >1s before an RTO-based retransmit.
- Takeaways:
  - The oscillation likely comes from a single missing packet (SACK hole) that blocks cumulative ACK and stalls the window while later packets are already acked via SACK.
  - Candidate fix: add a fast retransmit path for the missing seq when SACK shows a hole (e.g., after N ack_miss hits or missing_age > rto/2), or relax the ack_silence gate when the missing seq is old and still unacked.

## Log Review: Adaptive Pacer Stalls (Jan 6, 2026, latest)
- Logs: `logs/client_log.db`, `logs/server_log.db` (ICMP; `module_loader.loaded` shows `socks_relay`).
- Log spans: Alice ~10.95s (09:52:09.910-09:52:20.861 UTC); Bob ~35.75s (09:52:02.973-09:52:38.726 UTC).
- Stall evidence:
  - Alice `tunnel.packet_send` and `icmp.send` show a max inter-arrival gap ~0.751s (09:52:10.115-09:52:10.866 UTC); `tunnel.packet_recv` max gap ~0.756s.
  - Bob shows the same ~0.755s gap in `tunnel.packet_send`/`icmp.send`/`tunnel.packet_recv` (09:52:09.512-09:52:10.267 UTC).
  - Only one gap >=0.25s in this window; p99 gaps are still sub-12ms, so stalls are rare but sharp in this slice.
- Pacer behavior (Alice): 3,453 `tunnel.pacer_state` rows; 142 `action=blocked` with `block_reason=None` and `rate_limit=0.0`. `ack_rate_ewma` min ~1.32 (p50 ~242, p95 ~314); `srtt_ms` ~91.7-94.7; `unacked_count` 1-46; `target_mode` mostly `probe` (3170) with 283 `base`. Non-send actions cluster around 09:52:11.818 with `ack_rate_ewma` ~2.09 and falling `unacked_count`.
- Retransmit gating: Alice `tunnel.retransmit_skip` 3,487 events (all `ack_silence`); Bob `tunnel.retransmit_skip` 8,069 events (no reason string). During the ~0.75s stall window there are many `retransmit_skip` events plus control/poll/ack traffic, so the stall is not total silence.
- Takeaways:
  - Short but visible ~0.75s stalls occur on both sides despite pacer activity; the largest gap coincides with heavy `ack_silence` skips.
  - `action=blocked` without a `block_reason` plus `ack_rate_ewma` collapsing to ~2 pps suggests the pacer is stalling without logging why; add logging or capture longer windows if we need to quantify how often these stalls recur.

## Log Review: Adaptive Pacer Follow-up (Jan 6, 2026, latest)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Alice (last ~58s, 50k events each): max inter-arrival gaps ~1.01s on `tunnel.packet_send`/`icmp.send`/`tunnel.packet_recv`; gaps >=0.5s occurred 11 times and >=1.0s occurred 6-10 times depending on event.
- Pacer gating (Alice, last 5k `tunnel.pacer_state`): `action=blocked` 986; `ack_rate_ewma` min ~104.9 (p50 ~274, p95 ~349); `target_inflight` min 12 (p50 49, p95 104); `target_mode` split `feedback` 1901 / `probe` 3099. `ack_rate_ewma` was never None (no idle reset).
- Send blocking (Alice, last 5k `tunnel.send_blocked`): `pacer` 3258, `window_distance` 1742; `tunnel.send_window_distance` appears 2693 times in the same window.
- Bob (last ~80.8s, 20k events each): max gaps ~0.348s on send/recv; `tunnel.send_window_distance` 37 and `tunnel.send_blocked` reason `window_distance` 37 in the last 5k entries.
- Takeaways:
  - The pacer change did not remove the stalls; Alice still sees ~1s gaps while Bob stays under ~0.35s.
  - Pacer gating dominates Alice send blocks and drives `target_inflight` down to the low teens; the idle-reset path is not firing in this window, so feedback remains active.
