# Retransmit Local Cap + ACK-Silence Gating Plan

## Goal
- Reduce ICMP stall bursts by (1) gating Alice retransmits on cumulative-ACK
  silence and (2) using a pacer-driven local effective inflight cap without
  renegotiating window sizes.

## Plan
1. Update monotonic ACK tracking in `BaseTunnel._process_incoming_packet` so
   `_last_cum_ack` and `_last_cum_ack_time` advance only when ACK moves forward
   (use `seq_gt`).
2. Gate Alice RTO retransmits on cumulative-ACK silence (time since last ACK
   advance) rather than response silence; keep response-silence logic only for
   connection timeout.
3. Implement a pacer-driven local effective cap in Alice:
   - Compute `effective_cap = min(send_window._max_in_flight,
     pacer.target_inflight(...))`.
   - Use `effective_cap` in the send-window distance guard to block sends before
     exceeding SACK coverage when pacing tightens.
4. Add unit tests:
   - ACK regression does not update `_last_cum_ack`.
   - Retransmit fires when ACK is stalled despite receiving responses.
   - Distance guard respects the local effective cap.
5. Update `doc/TUNNEL.md` to describe ACK-silence gating and local effective cap.
6. After user reruns ICMP repro, summarize new findings in
   `doc/bugs/retransmit_stalling_icmp_socks.md`.

## Notes / Issues
- Define behavior when `_last_cum_ack_time` is None so RTO retransmits still
  fire before any ACK progress (keep response-silence fallback or treat None
  as "silence").
- Apply the local effective cap only when pacing is enabled; otherwise pacing
  disabled configs would still shrink the distance guard.
- If `_send_window_distance_exceeded` signature or tuple fields change, update
  Bob's call site and the base tunnel test expectations.
- The ACK regression test should assert `_last_cum_ack_time` remains unchanged
  along with `_last_cum_ack`.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py` (if signature changes are needed)
- `tests/test_tunnel.py`
- `tests/test_alice_tunnel.py`
- `doc/TUNNEL.md`
- `doc/bugs/retransmit_stalling_icmp_socks.md`
