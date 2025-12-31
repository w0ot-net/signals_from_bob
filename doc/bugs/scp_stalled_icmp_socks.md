# SCP shows "stalled" but transfer continues (ICMP + SOCKS)

## When it happens
- ICMP transport with the SOCKS module
- Bob is scp-ing to Alice via the proxy
- The scp progress bar stays near 0% and prints "stalled"
- The file on Alice is still growing

Example output:
```
root@vultr:~# proxychains scp 500MB-CZIPtestfile.org.zip 127.0.0.1:/tmp
ProxyChains-3.1 (http://proxychains.sf.net)
|S-chain|-<>-127.0.0.1:1080-<><>-127.0.0.1:22-<><>-OK
root@127.0.0.1's password:
500MB-CZIPtestfile.org.zip                                                                                                          0%  255KB   0.4KB/s - stalled -R
```

## Why it looks stalled
SCP's progress meter is driven by short-term throughput and the time since the
last visible update. With the ICMP transport, Alice polls and Bob only responds
(see doc/ASYMMETRY.md). That makes Bob's throughput bursty and bounded by Alice's
poll cadence. Proxychains plus SOCKS adds buffering and jitter, so SCP sees long
quiet gaps between bursts and prints "stalled" even though bytes are still moving.

## How to confirm progress
- On Alice, check file size growth: `ls -l /tmp/<file>` or `stat /tmp/<file>`.
- On Linux, `watch -n 1 ls -l /tmp/<file>` will show periodic increases.
- `scp -v` will still show traffic when data is moving.

## What to do
- Treat the "stalled" label as cosmetic if the file size is increasing.
- Use `scp -q` to suppress the progress meter.
- If the file size stops increasing for long periods, check for SOCKS channel
  starvation: doc/troubleshooting_socks_channel_starvation.md.

## Findings and analysis (2025-12-31)
- The tunnel is not idle; it is throughput-limited and bursty.
- Alice repeatedly hit `tunnel.send_blocked` with `pending=32` (ICMP max_pending),
  so new polls could not be sent until replies arrived. This bounds throughput by
  polling cadence.
- Alice also hit `tunnel.send_window_distance` with `distance=64` (max_in_flight),
  which blocks new sends when `next_seq` runs ahead of the cumulative ACK.
- Bob's SOCKS pump read far faster than the tunnel could drain: multi-MB/s in,
  ~0.28-0.33 MB/s out. Channel 2 hit `channel.send_buf_full` thousands of times.
- ICMP replies were mostly full-size (1200 bytes) and retransmits were low, so the
  primary constraint is poll rate + backpressure, not packet loss.

## Potential smoothing changes (not yet applied)
- Read only what fits in the channel buffer in the SOCKS pump to avoid single
  reads filling the send buffer.
- Cap the SOCKS pump backoff to avoid long sleep gaps when the buffer drains.
- Reduce `socks_relay_buffer_size` and increase `channel_max_send_buf` to favor
  smaller writes and steadier pacing.
