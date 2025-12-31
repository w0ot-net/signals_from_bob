# ICMP Retransmit Debugging

## Goal
- Determine why retransmits occur on ICMP transport with a stable link.

## Current Setup
- Default log profile: `icmp_retransmit_debug`
- Command: `python3 -m sfb.cli --role alice --transport icmp --icmp_target <ip> --db-log`

## Observations
- 23s window: server log shows 1210 `tunnel.packet_recv` and 1160 `tunnel.retransmit`.
- Retransmit seqs repeat about 31 times each (42 unique seqs).
- Bob's `tunnel.packet_recv` ack values change 49 times; each ack value repeats on the
  order of `icmp_max_pending` (default 32), suggesting pipelined polls.
- Client log in same window: 330 `tunnel.send_blocked` (258 transport blocked),
  1 `icmp.prune_stale`, 1 `tunnel.retransmit`.
- Latest run (wget + SSH + 2 SOCKS clients):
  - Simulated recv_window from logs shows `recv_out_of_window` spikes:
    - Alice receiving from Bob: 3903 out-of-window drops, 2867 delivered.
    - Bob receiving from Alice: 702 out-of-window drops, 9279 delivered.
  - Many incoming packets land >64 ahead of cumulative ACK, so recv_window drops them.
  - This matches `tunnel.recv_window` showing high `ready=0` counts on Alice.
- After adding send-window distance guard, Bob logged
  `tunnel.send_window_distance` with `distance=65535` while `last_cum_ack=282`
  and `next_seq=281`. That blocked responses and caused module load timeouts.
- Latest run shows only channel 2 in `channel.drain`, so it does not exercise
  multiple simultaneous clients. Added `sock.connect*` and `channel.open*`/`close*`
  to the debug log profile to map channels to client connections.
- Latest run with multiple clients (channels 2/4/6):
  - Alice `channel.drain` totals: ch2 ~4.5MB, ch4 ~7.7KB, ch6 ~2.8KB.
  - Alice `channel.send_buf_full` is only ch2 (10k+ events).
  - Bob `channel.drain` totals: ch0 ~137KB, ch4 ~9KB, ch6 ~663B, ch2 ~156B.
  - Both sides show `tunnel.send_window_distance` (Alice 889, Bob 1144).
  - Bob logged 2 `tunnel.packet_decode_failed`.

## Hypotheses
- High poll rate + `icmp_max_pending` causes many requests with the same ack value,
  and Bob retransmits opportunistically on each request while unacked remains.
- Send window only gates by unacked count. With SACK freeing slots while cumulative
  ACK stalls, the sender can advance `next_seq` more than 64 ahead of peer ACK,
  exceeding the receiver SACK window and causing out-of-window drops.
- The distance guard must treat `next_seq` behind the cumulative ACK as no block;
  otherwise wraparound math yields a huge distance and stalls the tunnel.

## Next Steps
- Apply Bob retransmit cooldown + ACK-stagnation gate and compare
  `tunnel.retransmit` vs `tunnel.retransmit_skip` counts.
- Run a session with `icmp_max_pending=1` or `icmp_send_interval` set to slow polling
  and compare retransmit counts to confirm the poll-cadence hypothesis.
- Add send-window distance guard: block new sends when
  `(next_seq - last_cum_ack) >= max_in_flight` to prevent out-of-window drops.
- Re-run the same workload and check for `tunnel.send_window_distance` events and
  reduced `recv_out_of_window` counts.
- Fix guard to use signed sequence distance and skip when `next_seq` is behind ACK.

## Notes
- Keep entries short and include log event names when possible.
