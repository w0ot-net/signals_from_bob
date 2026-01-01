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
5) **Pending/queue limits:** `icmp_max_pending`/`icmp_pending_timeout` could be constraining in-flight requests.

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
- Relay buffer: `--socks_relay_buffer_size 8192`
- Pump backoff max: `--socks_pump_backoff_max 0.05` (or smaller)
- Channel max send buf: `--channel_max_send_buf 65536` (if safe)
- ICMP MTU: `--icmp-mtu 1400`
- Send rate/burst: `--send-rate 0 --send-burst 0`

## Action Items
- Define and apply the `socks_throughput_debug` profile for targeted logs.
- Capture timeline + log snippets for baseline vs tuned runs.
- Inspect logs for gaps between pump stats and ICMP sends to find stalls.
