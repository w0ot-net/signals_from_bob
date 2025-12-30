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
- Bob runs the SOCKS server; Alice runs the SOCKS relay.
- The stall occurs while proxying scp from Bob to Alice over SOCKS.
- Concurrent proxying (wget) from Bob to the public Internet remains fast.
- scp prompts for password, then stalls at 0% and eventually disconnects.
- The same scp command succeeds with a small file like `/etc/passwd`.
- Normal SSH (interactive, same path through SOCKS) works; only scp stalls.
- Transport is DNS with defaults from `sfb/config.py` (polling and timeouts).
- Simultaneous SOCKS downloads remain fast, so the stall does not look like
  a global polling slowdown.

Command context:
```
proxychains scp /root/100MB.zip.5 muffin@127.0.0.1:/tmp
proxychains wget http://ipv4.download.thinkbroadband.com/100MB.zip
```

Observed scp output (trimmed):
```
ProxyChains-3.1 (http://proxychains.sf.net)
|S-chain|-<>-127.0.0.1:1080-<><>-127.0.0.1:22-<><>-OK
muffin@127.0.0.1's password:
Permission denied, please try again.
muffin@127.0.0.1's password:
100MB.zip.5 0% 0 0.0KB/s - stalled -
Connection to 127.0.0.1 closed by remote host.
```

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

## Attempted Fixes (Did Not Solve)

The following changes were tried but did not resolve the stall:

1. **Reduce `dns_pending_timeout` from 10s to 1s**
   - Rationale: Faster recovery from lost/delayed DNS queries
   - Result: No improvement, suggesting responses ARE arriving

2. **Reduce `channel_max_send_buf` from 64KB to 1KB**
   - Rationale: Faster TCP backpressure to SCP client
   - Result: No improvement

3. **Add blocking recv when pending > 75%**
   - When pending >= 24 and no responses ready, wait up to 50ms
   - Rationale: Avoid busy-polling, give responses time to arrive
   - Result: No improvement

4. **SOCKS pump refactor: write() instead of write_all()**
   - Use incremental writes with 5ms sleep on buffer_full
   - Rationale: Prevent indefinite blocking, propagate backpressure
   - Result: No improvement to stall, but cleaner code

## Next Investigation Steps
- Add per-channel drain rate metrics (bytes dequeued per second) to correlate
  stall duration with actual throughput.
- Improve SOCKS channel write pacing (smaller chunking, shorter backoff).
- Review DNS inflight limits and polling cadence, since `pending` saturates at
  32 on Alice during stalls.
- Investigate whether the issue is on Alice (relay) or Bob (server) side.
- Check if the tunnel tick loop is being starved by other threads.
