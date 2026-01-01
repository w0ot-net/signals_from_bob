# Plan: Remove tun_cap_need

## Goals
- Remove the tun.cap_need control message and all related state/logic.
- Preserve asymmetry rules (Alice initiates, Bob responds to polls) and keepalive
  suppression when data is pending.
- Keep Python 2.7/3 compatibility, stdlib only, and Windows/Linux support.

## Inventory (current references)
- sfb/tunnel/tunnel_control_messages.py (tun_cap_need, tun_cap_clear)
- sfb/tunnel/base_tunnel.py (cap_need/cap_clear dispatch and handlers)
- sfb/tunnel/alice_tunnel.py (_cap_need_active, _handle_cap_need/_handle_cap_clear,
  and the minimal-poll send path)
- sfb/tunnel/bob_tunnel.py (_cap_need_seq/_cap_need_cap and cap_need/cap_clear
  response logic)
- tests/dns_cap_need_sim.py and scripts/dns_cap_need_sim.py
- doc/cap_clear_delivery.md and doc/ADAPTIVE_PACING_PLAN.md
- Any additional hits from: rg -n "cap_need|cap_clear" -S .

## Behavior decisions to lock in
- When Bob cannot retransmit under the current response cap, he should respond
  with an ACK-only packet (no tun_pong) and wait for a larger poll instead of
  emitting cap_need.
- Since cap_need is removed, cap_clear becomes unused and should be removed too
  unless there is a new, independent use case.
- Keep logging of retransmit skips (reason=cap) for visibility.

## Implementation steps
1) Control messages
   - Delete tun_cap_need and tun_cap_clear from
     sfb/tunnel/tunnel_control_messages.py.
   - Remove cap_need/cap_clear dispatch branches and handlers from
     sfb/tunnel/base_tunnel.py.

2) Alice behavior
   - Remove _cap_need_active and _cap_need_info state in __init__.
   - Remove _handle_cap_need and _handle_cap_clear overrides.
   - Remove the "send minimal poll" branch in the send loop and ensure normal
     polling/keepalive logic still respects "no pong when data pending".

3) Bob behavior
   - Remove _cap_need_seq/_cap_need_cap state.
   - In _send_response, when a retransmit exceeds response_payload_cap:
     log the skip, send an ACK-only packet, and return without any tun_pong.
   - Remove cap_clear send paths and any cap_need state cleanup.

4) Tests and scripts
   - Remove or rewrite tests/dns_cap_need_sim.py to reflect the new behavior.
   - Remove scripts/dns_cap_need_sim.py if it only exists for cap_need.
   - Update any unit tests that assert cap_need/cap_clear events.

5) Documentation
   - Delete or rewrite doc/cap_clear_delivery.md.
   - Update doc/ADAPTIVE_PACING_PLAN.md and any other docs mentioning cap_need.

6) Verification
   - rg -n "cap_need|cap_clear" -S . to ensure no remaining references.
   - Run relevant unit tests with python3 (avoid tests/e2e/).

7) Commit and push
   - Commit the changes and push (per project rule).

## Open questions
- Do you want to keep any explicit "cap pressure" signal after removing
  tun_cap_need, or should Bob silently wait for larger polls?
