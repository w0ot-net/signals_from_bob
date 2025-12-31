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

## Hypotheses
- High poll rate + `icmp_max_pending` causes many requests with the same ack value,
  and Bob retransmits opportunistically on each request while unacked remains.

## Next Steps
- Run a session with `icmp_max_pending=1` or `icmp_send_interval` set to slow polling
  and compare retransmit counts to confirm this pattern.

## Notes
- Keep entries short and include log event names when possible.
