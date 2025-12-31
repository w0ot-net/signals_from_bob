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
