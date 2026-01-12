# DNS SOCKS: channel open stalls on missing seq 595 keepalive

## Summary
- SOCKS connect requests on Bob fail with `channel_open_failed` after a successful
  initial session.
- The tunnel stalls with cumulative ACK pinned at 595 on both sides.
- Alice receives seq 596 and later packets, but never receives seq 595 (keepalive),
  so the receive window buffers everything beyond the gap.

## Impact
- New SOCKS sessions cannot be established (channel opens time out).
- Tunnel continues to exchange keepalives, but control/data beyond the gap is not
  delivered.

## Environment
- Transport: DNS (recursive resolver at `192.168.1.1:53`).
- Logs: `logs/server_log.db`, `logs/client_log.db`.
- Window: 2026-01-11 23:20:44 to 23:21:40.

## Evidence
### SOCKS failures
- Bob logs `sock.server_handshake` with `connect_result=channel_open_failed`
  and `channel_wait_time=10.0` (rids 2-4, 23:21:24-23:21:34).
- Bob logs `channel.open` for ch4/ch6/ch8, but no `channel.open_ok` for those
  channels.
- Alice logs no `channel.open_in` for ch4/ch6/ch8 (only ch2 earlier).

### ACK stall
- Bob: cumulative ACK advances to 595 at 23:20:46 and never changes afterward.
- Alice: cumulative ACK advances to 595 at 23:20:46 and never changes afterward.

### Missing seq 595
- Bob sends seq 595 (keepalive) at 23:20:44.620 and retransmits at
  23:20:45.211 and 23:21:03.700.
- Alice never logs `tunnel.packet_recv` or `tunnel.recv_window` for seq 595.
- Alice logs seq 596 buffered at 23:20:44.870, then seq 594 delivered at
  23:20:45.522 (`recv_ack=595`), followed by seq 602/603 buffered with
  `recv_ack=595`.

### Transport correlation
- Bob emits DNS responses at the seq 595 send times (payload 38 bytes, not
  oversize).
- On Alice, one DNS query response is missing in the same window:
  `dns.send` corr_id 897 at 23:20:44.492 has no matching `dns.recv`.
- Server-side DNS ids for the seq 595 responses (e.g., 48627/45850/16082)
  do not appear in Alice logs; resolver rewriting prevents direct id matching,
  so timing is the only correlation.

### Retransmit behavior (Bob)
- `tunnel.retransmit` for seq 595 occurs twice (23:20:45.211 and 23:21:03.699).
- After the first retransmit, the "oldest by send_time" rotates to later seqs
  (601/602/603, etc.), so seq 595 is deprioritized until it becomes oldest
  again at 23:21:03. This matches Bob's opportunistic retransmit policy.

### New occurrence (2026-01-12 04:21:20 to 04:21:34)
- Bob logs `sock.server_channel_failed` for ch4/ch6/ch8 (rids 2-4) at
  04:21:24-04:21:34.
- Bob `tunnel.ack_detail` keeps cumulative ACK at 595 with `ack_silence`
  ~34-38s while `recv_ack` advances to 1036-1041; `send_keepalive_unacked`
  remains 66-70.
- Bob retransmits keepalives seq 711/712/725/729 with `retransmit_count=2`
  while cumulative ACK remains 595.
- Alice logs repeated `tunnel.retransmit_skip` due to `ack_silence` with
  `unacked=0` around 04:21:20.

## Hypothesis
- A single DNS response carrying seq 595 was dropped on the path (likely at the
  recursive resolver or on-path), creating a cumulative ACK hole.
- Bob's retransmit strategy (oldest-by-send-time only) does not prioritize the
  missing seq once newer packets become older by send_time, so the hole persists.
- The stuck cumulative ACK blocks channel-open control messages, so SOCKS
  handshakes fail.

## Open questions
- Can we correlate the missing response to `dns.prune_stale` or other DNS
  client warnings in the same window?
- Is the missing keepalive seq sufficient to block the control plane, or should
  keepalive-only gaps be tolerated?

## Next steps
- Add targeted retransmit when cumulative ACK is stalled and SACK shows packets
  beyond the gap (retransmit the missing seq even if not oldest-by-send-time).
- Add logging to correlate tunnel seq with DNS corr_id/transport send for
  end-to-end loss attribution.
