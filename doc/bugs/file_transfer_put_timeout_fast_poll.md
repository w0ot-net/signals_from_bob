# File transfer put timeout despite fast polling

## When it happens
- Bob runs file_transfer `put` and the transfer fails with `send timeout`.
- Alice appears to be polling quickly (no long keepalive gaps).
- Observed most often on ICMP transport (Linux-only), but can apply to DNS too.

## Symptoms
- Bob logs `channel.send_buf_full` followed by `channel.write_wait` and
  `ChannelError: Write timeout`.
- File transfer aborts with `FileTransferError: send timeout`.
- Tunnel remains connected or closes shortly after the timeout.

## Why this can still happen
- Bob can only send when Alice polls (see `doc/ASYMMETRY.md`).
- Alice polls "fast" only when the last response contained real data. If Bob
  replies with ack-only packets (no segments) or keepalive-only packets, Alice
  falls back to keepalive cadence after the grace polls.
- Alice can also be blocked by send window limits, adaptive pacing, send-rate
  limits, or transport pending caps, even if her loop is running.
- Keepalive-only responses are suppressed when any channel has pending data, so
  seeing keepalive-only responses while data is queued points to window or
  response-cap limits rather than idle behavior.

## Logging profile
Use the dedicated profile `file_transfer_put_debug` on both sides. It enables
file transfer module logs, channel buffer events, packet send/recv events, and
transport details for ICMP/DNS.

## How to capture logs
- Alice (client role):
  `python3 -m sfb.cli -v --role alice --transport icmp --log-profile file_transfer_put_debug ...`
- Bob (server role):
  `python3 -m sfb.cli -v --role bob --transport icmp --log-profile file_transfer_put_debug --module file_transfer put ...`
- Optional SQLite logs:
  `--db-log ./logs/alice_log.db` and `--db-log ./logs/bob_log.db`

## What to check in the logs
- Alice poll cadence:
  - `tunnel.packet_send` cadence (fast vs keepalive cadence).
  - `tunnel.send_blocked` reasons (pacer, send window, rate limit, transport).
- Bob backpressure:
  - `channel.send_buf_full` / `channel.write_wait` shows buffer saturation.
  - `tunnel.send_window_full` or `tunnel.send_window_distance` indicates ACKs
    are not advancing.
  - `tunnel.packet_recv` and `tunnel.ack` confirm ACK progress.
- Response classification:
  - If Bob sends ack-only responses, Alice will not treat them as "real data"
    and will reduce polling after grace polls.
- Payload constraints:
  - `tunnel.mtu_*` and `tunnel.response_cap` show response size limits that can
    reduce segments per poll.

## Next steps
- If Alice is pacing/blocked, adjust `max_in_flight`, pacing settings, or
  `tunnel_send_rate` and retry.
- If Bob is send-window blocked, look for ACK stagnation or transport pending
  limits on Alice.
- If responses are ack-only, confirm Bob is producing data segments and that
  the file transfer control messages (`module.send`/`module.recv`) are flowing.

## Latest log findings
- Alice log shows sustained `icmp.send_blocked`/`tunnel.send_blocked` with
  `pending=128` (transport max_in_flight), indicating the ICMP pending queue is
  saturated. This causes bursts then stalls while waiting for replies.
- Fast-gap retransmits are firing (e.g. `tunnel.retransmit` with
  `reason=fast_gap`) when SACK indicates a hole, but the overall cadence is
  still gated by the pending cap.
- Bob log shows `channel.send_buf_full` continuously and one segment per poll;
  he is responding, but throughput remains bounded by Alice's poll capacity.
- In the latest 5s window on Alice, there were 1482
  `icmp.send_blocked`/`tunnel.send_blocked` events and 379 fast-gap
  retransmits, reinforcing that transport pending saturation is the primary
  limiter rather than packet loss.
- After headroom gating, the latest 5s window on Alice shows 0
  `icmp.send_blocked` events and only 5 `tunnel.send_blocked` entries with
  `reason=transport_headroom` (pending=120, headroom=8). The remaining
  `tunnel.send_blocked` entries in that window are `Send window full` with
  `max_in_flight=1` immediately before the `tunnel.window_ok` update to 128.
- Fast-gap retransmits still appear (50 in the latest 5s window) with no
  `icmp.prune_stale`, so remaining chop is likely driven by SACK gaps or
  out-of-order responses rather than pending saturation.
