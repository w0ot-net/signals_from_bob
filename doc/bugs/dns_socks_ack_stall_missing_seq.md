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

### New occurrence (2026-01-12 08:03:23 to 08:03:37)
- Bob logs `sock.server_connect` for rid 1 at 08:03:23 (104.16.185.241:80) and
  `channel.open` for ch2, but no `sock.server_connected` or
  `sock.server_channel_failed` before shutdown at 08:03:37.
- Bob `tunnel.ack_detail` keeps cumulative ACK pinned at 43 with
  `send_keepalive_unacked=115`, `send_unacked=115`, and `send_oldest_seq=42-45`
  while `recv_ack` advances to 1063-1065.
- Alice `tunnel.ack_detail` shows `recv_ack` stuck at 38-43 with
  `recv_buffered=138-141` and `recv_recv_delivered=36-41` while Alice's ACK to
  Bob advances to 1058-1065.
- Bob `tunnel.response_cap` reports `response_payload_cap=137` while `dns.send`
  responses in this window are `payload_bytes=38` with `oversize=false`, so the
  response cap is not blocking retransmits.
- Alice logs repeated `tunnel.retransmit_skip` due to `ack_silence` around
  08:03:38.
- Bob sends a data packet (`content_flag=has_segments`, `seq=276`) at 08:03:24;
  Alice receives `seq=276` at 08:03:25 but `tunnel.recv_window` buffers it with
  `recv_offset=254` (recv_ack in the low 20s), so module delivery never occurs.
- Alice emits no non-keepalive `tunnel.packet_send` after 08:03:15, so there is
  no module response traffic in the 08:03:23-08:03:37 window.
- Bob `tunnel.send_window_distance` shows the first-missing seq walking forward
  (20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 39, 42, 43) with `missing_flags=4`
  (keepalive) and `missing_in_unacked=true`; the last missing seq is 43.
- Bob retransmits those missing seqs with `reason=window_distance`; for seq 42,
  `tunnel.send_window_distance` reports `missing_seq=42` while
  `oldest_unacked_seq=43`, yet retransmits are for seq 42 (indicating targeted
  retransmit selection is firing).
- Alice logs `tunnel.packet_recv` for seq 43 at 08:03:37 and then sends a
  keepalive with `ack=45`, but Bob never logs `tunnel.packet_recv` with
  `ack>=44` before shutdown, so the ACK update never reaches Bob.
- Bob retransmits seq 43 at 08:03:35.482, 08:03:36.474, and 08:03:36.491; each
  retransmit has nearby server `dns.send` responses (payload 38 bytes).
- On Alice, the only missing `dns.send` corr_ids are 1072/1073 at
  08:03:37-08:03:38 (no matching `dns.recv`), and Bob logs no `dns.recv` after
  08:03:36, so the query carrying `ack=45` likely never reaches Bob before
  shutdown.

### New occurrence (2026-01-12 17:58:23 to 17:59:02)
- Bob logs `sock.server_connect` for rid 1 at 17:58:23 and rid 2 at 17:58:38
  (104.16.185.241:80/104.16.184.241:80), both ending in
  `sock.server_channel_failed` after `channel_wait_time=20.0`.
- Bob `tunnel.send_window_distance` shows `missing_seq=118` (keepalive) with
  `missing_in_unacked=true` and `send_keepalive_unacked=105-106`; cumulative
  ACK is pinned at 118.
- Bob retransmits seq 118 three times with `reason=window_distance`
  (`first_send_age` ~40s), then shuts down at 17:58:59.
- Alice `tunnel.recv_window` delivers seq 118 at 17:59:00 (`recv_ack=121`) and
  sends keepalives with `ack=121` at 17:59:01-17:59:02, but Bob logs no
  `tunnel.packet_recv` with `ack>=119` (ACK update never reaches Bob).

## Code path notes
- `sfb/modules/socks/socks_server.py` starts `channel_open_timeout` as soon as
  `open_channel()` returns, then waits on `channel.wait_open()` without any
  awareness of tunnel stalls (the timeout is fixed at 20s in `sfb/config.py`).
- `sfb/channel/channel_manager.py` sends `ch_open` immediately, but the control
  message still sits behind any missing seq in the tunnel; `sfb/channel/channel.py`
  only waits on `_open_event`, so a stalled tunnel looks identical to a lost
  open message.
- `sfb/tunnel/bob_tunnel.py` retransmits the oldest unacked packet by
  `first_send_time` when polled, but only one per response and gated by cooldown;
  missing seqs are only reported when `send_window.distance_exceeded()` fires
  (so a long-lived gap can remain invisible until the window fills).
- `sfb/reliability/send_window.py` treats the cumulative `last_cum_ack` as the
  missing seq and keeps keepalives in the reliable stream; Bob never drops
  keepalives, so a single lost keepalive blocks all later control/data.

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

## Decision
- Keepalive-only gaps are not skippable; reliability is strict and control/data
  must wait for the missing seq to be recovered.

## Next steps
- Add targeted retransmit when cumulative ACK is stalled and SACK shows packets
  beyond the gap (retransmit the missing seq even if not oldest-by-send-time).
- Add logging to correlate tunnel seq with DNS corr_id/transport send for
  end-to-end loss attribution.
