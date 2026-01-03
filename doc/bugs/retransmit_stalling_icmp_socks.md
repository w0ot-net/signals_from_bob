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

## Latest findings (2026-01-03, icmp_retransmit_debug run 2)
- Timeline windows: Bob ~32.9s, Alice ~11.5s (client log ended earlier).
- Alice `tunnel.packet_send`: 5879; `tunnel.retransmit`: 1804 (~30.7%),
  all `reason=fast_gap`; retransmits are data packets (`seg_count` 1/2).
- Alice `tunnel.send_blocked`: 2323 total; 2266 `transport_headroom`
  at `pending=120` (max_in_flight=128, headroom=8).
- Alice `tunnel.packet_recv`: 5761 total; 2111 with non-zero SACK (~36.7%).
  Max repeated ACK run: 118 packets at the same ACK (1210).
- SACK highest-offset histogram: most gaps are small (offset 1 dominates),
  suggesting frequent out-of-order by 1 rather than deep loss.
- Bob `tunnel.packet_send`: 11910; `tunnel.retransmit`: 466 (~3.9%);
  `tunnel.retransmit_skip`: 11433 (mostly `ack_progress`).
- No `icmp.prune_stale` or `tunnel.packet_decode_failed` events observed.

## Change applied (2026-01-03)
- Removed Alice fast retransmit and fast recovery (no `fast_gap` retransmits).
- Expectation: retransmits should be RTO-only; compare rates after re-run.

## Latest findings (2026-01-03, post fast-retransmit removal)
- Timeline windows: Bob ~28.4s, Alice ~23.6s.
- Alice `tunnel.packet_send`: 2091; `tunnel.retransmit`: 169 (~8.1%),
  all `reason=rto`.
- Alice `tunnel.send_blocked`: 12101 total; 11219 `window_distance`,
  468 `transport_headroom`, 78 `retransmit_budget`.
- Alice `tunnel.send_window_distance`: 11220 events with `buffered=128`;
  typical `distance`/`distance_limit` ~236-243 and `unacked` ~108-115.
- Alice `icmp.prune_stale`: 4 (unexpected with 0% loss).
- SACK gaps persist: 560/2077 packet receives had non-zero SACK; highest
  offsets include 137/153, and max repeated ACK run was 239 packets.
- Bob retransmits: 271/2139 (~12.7%) with 1847 skips.

## Additional findings (2026-01-03, post fast-retransmit removal)
- 160/169 Alice retransmits occurred within 0.5s of the most recent response
  (most within ~50ms), indicating RTO firing while responses were active.
- Largest response gap on Alice was ~4.0s; during that window, only 2 packets
  were sent while `tunnel.send_blocked` logged ~2.6k `reason=window_distance`.
- `icmp.prune_stale` total was 9 requests, matching the remaining retransmits
  that occurred after response gaps >= 0.5s.

## Change applied (2026-01-03)
- Gate Alice RTO retransmits on response silence (no responses within RTO).
- Goal: suppress spurious retransmits while polling is active, leaving only
  retransmits tied to actual response gaps.

## Latest findings (2026-01-03, response-silence gate logs)
- Timeline windows: Bob ~28.4s, Alice ~44.0s.
- Alice `tunnel.packet_send`: 1573; `tunnel.retransmit`: 112 (~7.1%),
  all `reason=rto`.
- Alice `tunnel.send_blocked`: 21285 total; 20174 `window_distance`,
  738 `send_window_full`, 317 `transport_headroom`, 56 `retransmit_budget`.
- Alice `icmp.prune_stale`: 5 (still unexpected with 0% loss).
- Largest response gap on Alice: ~10.1s; max repeated ACK run was 243 packets.
- Bob `tunnel.retransmit`: 271; `tunnel.retransmit_skip`: 1847
  (ack_progress 1611, cooldown 236).

## Change applied (2026-01-03)
- Clamp send-window distance to `max_in_flight` (not `max_in_flight + unacked`).
- Goal: prevent senders from getting ahead of the receiver's reorder buffer
  and reduce out-of-window drops that trigger retransmits/stalls.

## Latest findings (2026-01-03, default logs snapshot)
- Sources: `logs/client_log.db` (Alice) had 107486 rows (~09:23:45-09:24:21 UTC);
  `logs/server_log.db` (Bob) had 273722 rows (~09:23:33-09:25:07 UTC).
- Transport: ICMP (`icmp.send`/`icmp.recv` present).
- Alice `tunnel.send_window_distance`: 5515; `tunnel.send_blocked`: 11680
  (5515 `window_distance`, 6094 `transport_headroom`); `tunnel.retransmit`: 25.
- `distance` and `distance_limit` pinned at 128 (max_in_flight/effective_cap).
- `unacked` low while `buffered` high (median `unacked` 4, p90 10, max 117;
  median `buffered` 124, p90 127, max 127), consistent with cumulative ACK
  stalling on a gap while later packets are SACKed.
- `last_cum_ack` stalls in 25 runs at ~0.58-0.61s each (~380-410 events/run);
  max repeated ACK run in `tunnel.packet_recv` was 128 packets.
- SACK non-zero in 6157/12910 packet receives (~47.7%), highest offset 127.
- Bob `tunnel.send_window_distance`: 37; `tunnel.send_blocked`: 37 (all
  `window_distance`); `tunnel.retransmit`: 1965 and `tunnel.retransmit_skip`:
  32294 (ack_progress 31684, cooldown 610).
- Bob `distance` and `distance_limit` pinned at 128; `unacked` median 108
  (max 116) while `buffered` median 20 (max 127).
- Bob `tunnel.recv_window` ready=0 in 12832/34273 (37.4%).
- Bob `tunnel.packet_recv` SACK non-zero in 385/34273 (~1.1%), highest offset 127.

## Change applied (2026-01-03)
- Bob retransmit cooldown now floors to `poll_ewma * max_in_flight` (one window
  of polls) and remains capped by `tunnel_bob_retransmit_max_interval`.
- Goal: reduce opportunistic retransmit spam under fast polling with high
  Bob->Alice reordering while keeping the opportunity-driven model intact.

## Latest findings (2026-01-03, default logs snapshot after 5s cap)
- Sources: `logs/client_log.db` (Alice) had 100107 rows (~09:47:31-09:48:02 UTC);
  `logs/server_log.db` (Bob) had 284809 rows (~09:47:32-09:48:54 UTC).
- Alice had no `tunnel.send_window_distance` events in the DB logs; her
  `tunnel.send_blocked` was dominated by `transport_headroom` (6482/6537).
- Bob `tunnel.send_window_distance`: 17 and `tunnel.send_blocked`: 17
  (`window_distance` only).
- Bob `tunnel.retransmit`: 39; `tunnel.retransmit_skip`: 34499
  (`cooldown` 23949, `ack_progress` 10550).
- Bob `distance` and `distance_limit` pinned at 128; `unacked` median 103
  (max 112), `buffered` median 25 (max 37).

## Latest findings (2026-01-03, default logs snapshot after profile update)
- Sources: `logs/client_log.db` (Alice) had 32300 rows (~09:58:19-09:58:31 UTC);
  `logs/server_log.db` (Bob) had 86307 rows (~09:58:16-09:58:45 UTC).
- Both sides show zero `tunnel.send_window_distance` events in the DB logs.
- Alice `tunnel.send_blocked`: 2156, mostly `transport_headroom` (2096/2156).
- Bob `tunnel.retransmit`: 28; `tunnel.retransmit_skip`: 10805.

## Latest findings (2026-01-03, default logs snapshot with distance spam)
- Sources: `logs/client_log.db` (Alice) had 144500 rows (~10:20:36-10:21:15 UTC);
  `logs/server_log.db` (Bob) had 420277 rows (~10:20:31-10:22:34 UTC).
- `cli.log_startup` confirms `icmp_retransmit_debug` on both sides with
  `tunnel.send_window_distance` whitelisted and empty blacklist.
- Alice `tunnel.send_window_distance`: 2002; `tunnel.send_blocked`: 12202
  (`transport_headroom` 10144, `window_distance` 2002).
- Alice distance metrics pinned at 128 with low `unacked` (median 6) and high
  `buffered` (median 122, p90 126), indicating cumulative ACK stalls with
  SACKed gaps.
- Alice `tunnel.send_window_distance` stalls cluster into 10 runs, each
  lasting ~0.59s with ~380-407 events (per-run ack stays fixed).
- The `last_cum_ack` values in those runs map to data packets
  (`flags=0`, `seg_count=1`), not keepalive-only packets. Most of those
  sequences show two `tunnel.packet_send` entries (initial + retransmit),
  except seq 17118 which appears once.
- Bob had zero `tunnel.send_window_distance` events in this window.

## Change applied (2026-01-03)
- When Alice hits the distance cap with low `unacked` and high `buffered`,
  she now retransmits the packet at `last_cum_ack` once per ACK stall if
  ACK silence exceeds 0.25 * RTO (min 50ms).
- Goal: recover the missing gap earlier than the full RTO and reduce
  prolonged distance-cap stalls.

## Latest findings (2026-01-03, post gap retransmit)
- Sources: `logs/client_log.db` (Alice) had 94600 rows (~10:31:49-10:32:18 UTC).
-  `logs/server_log.db` (Bob) had 232506 rows (~10:31:44-10:32:58 UTC).
- Alice `tunnel.send_window_distance`: 6407; `tunnel.send_blocked`: 11135
  (`window_distance` 6407, `transport_headroom` 4670).
- Alice distance metrics pinned at 128 with low `unacked` (median 5, p90 12)
  and high `buffered` (median 123, p90 126).
- Alice `tunnel.retransmit`: 44, all `reason=gap` (new gap retransmits firing).
- `tunnel.send_window_distance` stalls split into 50 runs; longest runs are
  ~0.24-0.26s with ~156-177 events (shorter than the pre-change ~0.59s runs).
- Bob `tunnel.send_window_distance`: 336; `tunnel.send_blocked`: 336
  (`window_distance` only).
- Bob `tunnel.retransmit`: 344 (`window_distance` 336), `tunnel.retransmit_skip`:
  28908.
- Bob distance metrics pinned at 128; `unacked` median 57 (p90 101), `buffered`
  median 71 (p90 116).
- Bob `tunnel.send_window_distance` stalls split into 3 runs with max duration
  ~0.28s (105-118 events).

## Change applied (2026-01-03)
- Alice now attempts a gap retransmit before the distance cap when the window
  is within ~12.5% of the limit, `buffered` is high, and `unacked` is low.
- Goal: clear gaps before the hard distance block to keep throughput smoother.

## Latest findings (2026-01-03, instrumented send-window distance fields)
- Sources: `logs/client_log.db` (Alice) had 117000 rows (~10:40:18 UTC);
  `logs/server_log.db` (Bob) had 318053 rows (~10:41:19 UTC).
- Alice `tunnel.send_window_distance`: 1905; Bob: 339 (now present).
- `missing_in_unacked` is always True on both sides, so the blocking seq is
  still tracked in the send window (no dropped entry).
- Alice `missing_age` median ~0.24s (max ~1.20s), `missing_retransmit_count`
  median 0 (p90 1); Bob `missing_age` median ~0.086s (max ~0.28s),
  `missing_retransmit_count` median 1 (p99 34, max 37).
- `missing_seq == oldest_unacked_seq` in 65.6% of Alice events (1249/1905) and
  13.6% of Bob events (46/339), indicating the cumulative-ACK blocker is often
  not the oldest-by-send-time packet, especially on Bob.

## Next steps from the latest logs
- If console output still shows `tunnel.send_window_distance` spam, capture DB
  logs with a profile that includes it on both sides; the latest Alice DB had
  zero `tunnel.send_window_distance` events.
- Re-run with the same profile to confirm retransmit rate drops with the
  response-silence gate and to quantify any remaining stalls.
- The dominant block is now `tunnel.send_window_distance`; investigate why
  cumulative ACK stalls while `next_seq` advances.
- Re-run with a longer Alice log window to match Bob's timeframe.

## Latest findings (2026-01-03, newest logs copied locally)
- Sources: `logs/client_log.db` (Alice) had 73200 rows (~10:56:22-10:56:36 UTC).
- `cli.log_startup` shows `log_profile` "all_events" with `db_log_path`
  "./logs/client_log.db".
- Alice `tunnel.send_window_distance`: 4 (all at startup). Latest shows
  `distance` 128 (`distance_limit` 128, `effective_cap` 128) with `unacked` 113,
  `buffered` 15, and `missing_seq` == `last_cum_ack` 105
  (`missing_in_unacked` True).
- Alice `tunnel.send_blocked`: 3476, all `reason=transport_headroom` with
  `headroom` 8, `pending` 120, `limit` 120, `max_in_flight` 128.
- Sources: `logs/server_log.db` (Bob) sampled the most recent 200000 rows
  (ids 33384-233383, ~10:56:28-10:57:14 UTC).
- `cli.log_startup` shows `log_profile` "all_events" with `db_log_path`
  "/var/www/html/server_log.db".
- Bob `tunnel.retransmit`: 1; `tunnel.retransmit_skip`: 22359, mostly
  `reason=cooldown` with `cooldown` ~0.23-0.25s.
- No `tunnel.send_window_distance` events appear in the sampled Bob window.

## Latest findings (2026-01-03, max-in-flight 256 stall)
- Sources: `logs/client_log.db` (Alice) had 30200 rows (~11:02:57-11:03:12 UTC).
- `cli.log_startup` shows `log_profile` "all_events" with `db_log_path`
  "./logs/client_log.db".
- Alice `tunnel.send_window_distance`: 7457; `tunnel.send_blocked`: 7962;
  `tunnel.retransmit`: 23; `tunnel.packet_recv`: 2049.
- Alice `tunnel.send_blocked` reasons: `window_distance` 7456,
  `transport_headroom` 426 (`headroom` 16, `pending` 240, `limit` 240,
  `max_in_flight` 256), `retransmit_budget` 11.
- Alice distance metrics pinned at 256 (`distance_limit` 256) with
  `buffered` median 224 (max 256) and `unacked` median 32 (min 0). Latest
  event shows `missing_in_unacked` False and `missing_age` null while
  `missing_seq` equals `last_cum_ack` 1822 and `unacked` is 0.
- Sources: `logs/server_log.db` (Bob) had 14501 rows (~11:02:53-11:03:13 UTC).
- `cli.log_startup` shows `log_profile` "all_events" with `db_log_path`
  "/var/www/html/server_log.db".
- Bob `tunnel.retransmit`: 8; `tunnel.retransmit_skip`: 2028
  (`reason=cooldown`, `cooldown` 3.0s); `tunnel.packet_recv`: 2049.
- No `tunnel.send_window_distance` events appear in the sampled Bob window.

## Latest findings (2026-01-03, post keepalive-drop instrumentation)
- Sources: `logs/client_log.db` (Alice) had 18300 rows (~11:32:53-11:32:58 UTC).
- `cli.log_startup` shows `log_profile` "all_events" with `db_log_path`
  "./logs/client_log.db".
- Alice `tunnel.send_window_distance`: 833; `tunnel.send_blocked`: 1400;
  `tunnel.retransmit`: 4; `tunnel.packet_recv`: 2178.
- Alice distance metrics pinned at 256 (`distance_limit` 256) with
  `buffered` 177 and `unacked` 79 in the latest event; `missing_in_unacked`
  remains True with `missing_age` ~1.54s and `missing_seq` == `last_cum_ack`
  2004. Keepalive-drop fields were not present in these events.
- Alice `tunnel.packet_recv` shows a very large SACK bitmap while `ack` stays
  pinned, consistent with Bob holding most of the 256-window beyond the
  missing seq. The gap-retransmit gate does not trigger here because
  `unacked` (79) exceeds the current threshold (distance_limit / 4 = 64),
  so the missing packet waits for RTO instead of an early gap retransmit.
- Alice `tunnel.send_blocked` reasons: `window_distance` 833,
  `transport_headroom` 499, `retransmit_budget` 2.
- Sources: `logs/server_log.db` (Bob) had 16579 rows (~11:32:46-11:33:03 UTC).
- `cli.log_startup` shows `log_profile` "all_events" with `db_log_path`
  "/var/www/html/server_log.db".
- Bob `tunnel.send_window_distance`: 208; `tunnel.send_blocked`: 208;
  `tunnel.retransmit`: 263; `tunnel.retransmit_skip`: 2147.
- Bob latest distance event shows `distance` 256, `buffered` 255, `unacked` 1,
  `missing_in_unacked` True with `missing_age` ~0.0025s and
  `missing_retransmit_count` 4. Keepalive-drop fields were not present.
