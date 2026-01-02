# SSH disconnects quickly over SOCKS proxy (DNS/ICMP tunnel)

## Summary
- SSH through Bob's socks_server dies shortly after connect.
- SCP is not working at all in the same setup.
- Wget downloads through the same proxy are stable and can run long-lived.
- While a long-lived download is active, a separate SSH session still dies.

## When it happens
- SOCKS proxy is Bob's socks_server module with Alice running socks_relay.
- Transport: DNS and ICMP both reproduced (user report).
- Happens as the only session and while another session is actively downloading.
- SSH via proxychains to 127.0.0.1:22 (target is on Alice side).
- Connects and authenticates; can run a command or two; then disconnects within
  a few seconds to ~20 seconds.

Example SSH outcomes:
- "Connection to 127.0.0.1 closed by remote host."
- With `ServerAliveInterval=10` / `ServerAliveCountMax=3`:
  "Timeout, server 127.0.0.1 not responding."

## What we know so far
- Log profile used: `dns_socks_stall_debug`.
- `logs/client_log.db` shows repeated `dns.error_response` from `8.8.8.8:53`
  with `rcode=2` (SERVFAIL) for `*.ebaysso.com` queries.
- `logs/client_log.db` shows `dns.send_blocked` when pending reaches
  `max_in_flight=128`, plus `dns.prune_stale` events.
- Alice side (socks_relay) logs `sock.relay_eof` for the target, followed by
  `sock.pump_stop` with `socket_eof` and `socket_send_error`.
- Bob side (socks_server) logs `channel.close_in`, then `sock.pump_stop` with
  `channel_eof` and `channel_closed`.
- No tunnel aborts observed in these sessions; the close appears to originate
  at the target side after data stalls.

## What we've tried
- Added SOCKS pump instrumentation (`sock.pump_start`, `sock.pump_stop`,
  `sock.pump_stats`) in `sfb/modules/socks/data_pump.py`.
- Enabled verbose logging automatically when `--log-profile` is passed.
- Used `channel_close_debug` and `dns_socks_stall_debug` profiles to capture
  tunnel, channel, SOCKS, and DNS events.
- Collected logs for DNS sessions; observed SERVFAILs and send-window
  saturation as above.
