# Excess retransmits and stall bursts (ICMP + SOCKS)

## Summary
- Reported symptom: frequent retransmits and stalls under ICMP tunnel with SOCKS.
- Latest default logs do not include retransmit/recv-window events, so we need a
  debug profile to confirm the rate and causes.

## Latest logs reviewed (2026-01-03)
- Sources: `logs/server_log.db` (Bob) and `logs/client_log.db` (Alice).
- Transport: ICMP, SOCKS module loaded.
- Session timeline: handshake, MTU/window negotiation (1312/128), two SOCKS
  connects (`172.67.177.210:443` and `127.0.0.1:22`), clean shutdown ~56s later.
- Missing signals: no `tunnel.retransmit`, `tunnel.send_blocked`,
  `tunnel.recv_window`, or transport-loss events were logged.

## Logging profile to use
Use log profile `icmp_retransmit_debug` on both sides; it captures:
- `tunnel.packet_*`, `tunnel.ack`, `tunnel.retransmit*`,
  `tunnel.send_blocked`, `tunnel.recv_window`
- `icmp.*`, channel, and SOCKS connection events

## Data to collect
- Fresh DB logs with the profile above while reproducing the stall.
- Clear old logs before the run so the timeline is clean:
  - delete `logs/server_log.db` and `logs/client_log.db`
- Include workload details (command, target, duration).

## Next steps once logs arrive
- Measure retransmit rate vs `tunnel.packet_recv` to quantify loss or ack churn.
- Check `tunnel.recv_window` drops and `tunnel.send_window_distance` to confirm
  whether we are overrunning the SACK window.
- Compare `tunnel.send_blocked` bursts with `icmp.send_blocked` to see if
  transport backpressure is the primary limiter.
