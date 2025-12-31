# SOCKS channel starvation during busy download

Goal: investigate the case where multiple SOCKS clients are active and a single
heavy download appears to block other channels from making progress.

## Symptom

- With one channel doing a large download, new SOCKS channels connect but data
  does not flow for those channels.

## Evidence from logs (client side)

- `logs/client_log.db` shows `sock.connect` for ch2 (HTTP) followed by ch4/ch6
  (SSH to 127.0.0.1:22).
- For 1767158380 to 1767158465, `channel.drain` totals:
  - ch2: 19492390 bytes
  - ch4: 6051 bytes
  - ch6: 15723 bytes
  - ch0: 6852 bytes
- `channel.send_buf_full` and `channel.send_buf_high` spam for ch2 with
  `size=8192` (send buffer full).
- `tunnel.send_blocked` repeats with `pending=32` and `max_pending=32`
  (Alice side).
- `tunnel.send_window_distance` repeats with `distance=64` and
  `max_in_flight=64`.

Interpretation: the tunnel outbound queue/window is saturated by the busy
channel, leaving little or no capacity for other channels to enqueue data.

## Evidence from logs (server side)

- `logs/server_log.db` shows `tunnel.send_blocked` on Bob with
  `reason=window_distance` and `max_in_flight=64`.
- For 1767158380 to 1767158535, `channel.drain` totals:
  - ch4: 3288554 bytes
  - ch6: 58793 bytes
  - ch2: 156 bytes
  - ch0: 492142 bytes

Channel mapping across sides and sessions needs confirmation, but both logs show
single-channel dominance with other channels barely moving.

## Hypotheses

- The global pending queue and send window are shared across channels with
  FIFO scheduling, so a single busy channel can monopolize the in-flight budget.
- The packer drains one channel until empty instead of round-robin fairness.
- Asymmetry: Bob throughput is bounded by Alice poll rate, so one heavy channel
  may consume the available poll budget and starve others.

## Logging profile

Use the `socks_starvation` profile to reduce log volume while preserving
per-channel flow signals:

```
python3 -m sfb.cli --log-profile socks_starvation ...
```

Whitelist:
- `cli.*`
- `dns.*`
- `sock.connect*`
- `sock.server_*`
- `channel.open*`
- `channel.close*`
- `channel.drain`
- `tunnel.send_blocked`
- `tunnel.send_window_distance`
- `tunnel.send_window_full`
- `tunnel.send_window_inconsistent`
- `tunnel.retransmit*`
- `tunnel.state`
- `tunnel.window_*`

Blacklist (keep suppressed unless needed):
- `tunnel.packet_*`
- `channel.pack`
- `channel.send_buf_*`
- `sock.pump_stats`

If we need socket-level flow details, temporarily enable `sock.pump_stats` for
short runs only.

## Next data to collect

- Reproduce with two channels (one large download, one interactive) using the
  focused profile above.
- Capture time-synced client/server logs to confirm channel ID mapping and flow
  direction.
- Track per-channel `channel.drain` bytes per second for starvation patterns.

## Latest results (socks_starvation default)

In the newest logs, multiple channels are flowing concurrently:

- Client side last ~10 minutes: `channel.drain` shows ch2 ~64.9MB and ch22
  ~8.0MB, with additional non-zero bytes on ch12/ch20/ch26 and others.
- Client side last ~60 seconds: ch2 ~8.27MB and ch22 ~7.77MB, indicating
  concurrent throughput.
- No recent `sock.connect` channels show zero bytes drained.
- `tunnel.send_blocked` remains frequent under load, but no longer corresponds
  to total starvation of other channels.

## Candidate fixes (later)

- Fair scheduling across channels when packing segments.
- Per-channel caps on pending segments so a single channel cannot fill the
  global pending queue.
- Reserve a small portion of the send window for low-volume or control traffic.
