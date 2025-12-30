# SCP Stall Analysis

## Summary
Large scp uploads via the SOCKS proxy stall while other proxy sessions (wget,
interactive SSH) continue to work. This is a per-channel stall, not a full
tunnel outage.

## Observed Behavior
- Small transfers (e.g., /etc/passwd) complete.
- Larger transfers (22KB, 44KB) stall.
- During stall, other proxy sessions remain functional.
- tcpdump shows DNS responses still reaching Alice during stalls.

## Log Evidence
- Client shows repeated `tunnel.send_blocked` with `pending: 32` and
  `max_pending: 32` (DNS inflight saturated).
- Client shows `channel.send_buf_full` and `channel.write_wait` for the scp
  channel (example: `ch: 4`), indicating backpressure at the channel buffer.
- Server continues to send responses (`dns.send`, `tunnel.packet_send`) while
  the scp channel is blocked.
- The tunnel is closed by operator when the stall is confirmed.

## Interpretation
This appears to be a per-channel backpressure and pacing issue under sustained
bulk transfer. The scp channel fills its send buffer and blocks, while other
channels keep draining.

## Next Investigation Steps
- Add per-channel drain rate metrics (bytes dequeued per second) to correlate
  stall duration with actual throughput.
- Improve SOCKS channel write pacing (smaller chunking, shorter backoff).
- Review DNS inflight limits and polling cadence, since `pending` saturates at
  32 on Alice during stalls.
