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
1) **Relay buffer/poll throttling:** `socks_relay_buffer_size` (2048) and `non_blocking_poll_timeout` backoffs could be capping throughput.
2) **Channel send window/backpressure:** Tunnel window/MTU negotiation may be small; send window growth may be slow.
3) **ICMP payload MTU:** Default 1200 payload MTU; small payloads increase per-packet overhead.
4) **Polling cadence (Alice/Bob):** `tunnel_send_rate` and retransmit/poll intervals may be conservative for local links.
5) **Pending/queue limits:** `max_in_flight`/`icmp_pending_timeout` could be constraining in-flight requests.

## Next Diagnostic Steps
- Run `scripts/icmp_socks_diag.py` with tunables to see rate impact:
  - Increase ICMP MTU: `--icmp-mtu 1400` (or larger if path allows).
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
  'log_component_module_socks': True,
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
- Relay buffer: `--socks-relay-buffer-size 8192`
- Pump backoff max: `--socks-pump-backoff-max 0.05` (or smaller)
- Channel max send buf: `--channel-max-send-buf 65536` (if safe)
- ICMP MTU: `--icmp-mtu 1400`
- Send rate/burst: `--send-rate 0 --send-burst 0`

## Action Items
- Define and apply the `socks_throughput_debug` profile for targeted logs.
- Capture timeline + log snippets for baseline vs tuned runs.
- Inspect logs for gaps between pump stats and ICMP sends to find stalls.

## Experiment Log: Higher MTU + Larger Buffers (Dec 31, 2025)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 1 --icmp-target 127.0.0.1 --timeout 120 \
    --log-profile socks_throughput_debug --verbose-cli \
    --socks-relay-buffer-size 8192 --channel-max-send-buf 65536 \
    --icmp-mtu 1400 --send-rate 0
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
  - Increase buffers further (e.g., `--socks-relay-buffer-size 16384`, `--channel-max-send-buf 131072`).
  - Increase ICMP concurrency/window (consider bumping `max_in_flight` beyond 64) and ensure send window can grow; keep `--send-rate 0` and omit `--send-burst` to allow defaults.
  - Optionally reduce pump backoff (`non_blocking_poll_timeout`, `socks_pump_backoff_max`) if we add config overrides for them.

## Experiment Log: Smaller Backoff + Larger Buffers (Dec 31, 2025)
- Command:
  ```
  python3 scripts/icmp_socks_diag.py --clients 1 --icmp-target 127.0.0.1 \
    --icmp-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug --verbose-cli \
    --socks-relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --socks-pump-backoff-max 0.002 --non-blocking-poll-timeout 0.00002
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
  python3 scripts/icmp_socks_diag.py --clients 1 --icmp-target 127.0.0.1 \
    --icmp-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug --verbose-cli \
    --socks-relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --socks-pump-backoff-max 0.0001 --non-blocking-poll-timeout 0
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
  python3 scripts/icmp_socks_diag.py --clients 4 --icmp-target 127.0.0.1 \
    --icmp-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug \
    --socks-relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --socks-pump-backoff-max 0.0001 --non-blocking-poll-timeout 0
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
  python3 scripts/icmp_socks_diag.py --clients 8 --icmp-target 127.0.0.1 \
    --icmp-mtu 1400 --send-rate 0 --log-profile socks_throughput_debug \
    --socks-relay-buffer-size 32768 --channel-max-send-buf 262144 \
    --socks-pump-backoff-max 0.0001 --non-blocking-poll-timeout 0
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
  - Alice `tunnel.send_blocked` reasons split between `transport_headroom` (1,343) and `window_distance` (625).
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
  - `tunnel.send_blocked` frequently reports `transport_headroom` at `pending=120` of `max_in_flight=128`.
  - `tunnel.send_window_distance` repeats with `distance=128`, `unacked=1`, `buffered=127`, indicating a single missing seq stalls new sends.
  - Retransmits occur, but subsequent sends are still blocked until the missing seq is acked.
- Bob retransmit cooldown:
  - `tunnel.retransmit_skip` on Bob uses `poll_ewma~0.0013s` and `window=128`, yielding `cooldown~0.16s` and repeated skips while `unacked` remains 80-90.
- Takeaways:
  - Alice is polling very aggressively (sub-2ms EWMA), but inflight is capped by transport headroom and window-distance stalls; the feedback target suggests a lower inflight may reduce gaps.
  - Bob’s cooldown is driven by Alice’s high poll rate and window size, which can defer opportunistic retransmits during loss.

## Log Review: Feedback-Driven Pacing (Jan 3, 2026, post-change)
- Logs: `logs/client_log.db`, `logs/server_log.db`.
- Throughput (Alice `sock.pump_stats`, target_to_channel):
  - ~3.42 MB over ~46.16s, ~74 KB/s (~0.074 MB/s).
- Pacer adjustments (Alice):
  - `tunnel.pacer_adjust`: 140 total; 139 `window_distance`, 1 `transport_headroom`.
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
