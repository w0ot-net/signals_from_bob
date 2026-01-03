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

## Latest findings (2026-01-03, icmp_retransmit_debug)
- Logs captured with `icmp_retransmit_debug` on both sides.
- Timeline windows: Bob ~50.2s, Alice ~15.5s (client log ended earlier).
- Alice `tunnel.packet_send`: 8844; `tunnel.retransmit`: 2787 (~31.5%),
  all `reason=fast_gap`; 2327 unique seqs, max 29 repeats on one seq.
- Alice `tunnel.send_blocked`: 3576 total; 3515 `transport_headroom`
  at `pending=120` (max_in_flight=128, headroom=8).
- Alice `icmp.send`: 87.5% of sends at `pending>=110`; 40.6% at `pending=120`.
- Bob `tunnel.packet_send`: 20404; `tunnel.retransmit`: 480 (~2.4%).
- Bob `tunnel.retransmit_skip`: 19913, mostly `ack_progress` (19753),
  with some `cooldown` (160).
- `tunnel.recv_window` ready counts:
  - Bob ready=0 in 11400/20404 (~56%); Alice ready=0 in 382/8728 (~4.4%).
- No `tunnel.send_window_distance`, `tunnel.send_window_full`, or
  `tunnel.packet_decode_failed` events observed.

## Next steps from the latest logs
- Explain the Alice-side `fast_gap` retransmits: confirm if gaps align with
  receive window drops or polling bursts.
- Check whether `transport_headroom` is too conservative for this workload
  (pending saturates at 120 with frequent send blocking).
- Re-run with a longer Alice log window to match Bob's timeframe.
