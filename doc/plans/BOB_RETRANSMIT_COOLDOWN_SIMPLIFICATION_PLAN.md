# Bob Retransmit Cooldown Simplification Plan

## Goal
Respect Bob retransmit cooldown even when the send window is blocked, while
removing duplicated decision/logging code in the Bob response path.

## Non-goals
- Change Alice pacing or RTT logic.
- Add new configuration knobs.
- Modify packet formats or reliability invariants.
- Touch tests or add new test coverage unless explicitly asked.

## Affected Components
- sfb/tunnel/bob_tunnel.py

## Plan
1) Review the current Bob response flow in `_select_response_action`,
   `_log_send_blocked`, and `_send_response` to confirm that window blocking
   bypasses the retransmit cooldown.
2) Refactor `_select_response_action` to compute `oldest_info`, `cooldown`,
   `oldest_age`, and `retransmit_due` once, then return a single `blocked`
   action with:
   - `block_reason` (`window_full` or `window_distance`)
   - `block_details` (distance info/details for window_distance only)
   - `oldest_info`, `retransmit_due`, and cooldown/age values for logging
   This collapses `window_blocked` and `distance_blocked` into one path.
3) Update `_send_response` so the `blocked` action:
   - Always logs the blocked state once.
   - Retransmits the oldest packet only when `retransmit_due` is True.
   - Otherwise returns without sending a response, enforcing the cooldown and
     avoiding keepalive-on-blocked growth in the send window.
4) Simplify `_log_send_blocked` to:
   - Build common fields once and reuse them.
   - Preserve existing event names (`tunnel.send_window_full`,
     `tunnel.send_window_distance`, `tunnel.send_blocked`,
     `tunnel.reliability_state`) so log parsing stays stable.
   - Remove duplicated branch logic and any now-unused variables.
5) Remove any leftover conditionals or fields made redundant by the refactor,
   keeping the code ASCII-only and avoiding new comprehensions in `sfb/`.

## Validation
- Run a manual tunnel session and confirm `tunnel.retransmit` no longer fires
  on every poll during `tunnel.send_window_distance` stalls.
- Check that retransmit counts per sequence drop to cooldown cadence and that
  duplicate spikes in the pcap shrink.
- Confirm no new `tunnel.send_window_inconsistent` errors are logged.

## Risks
- Bob may skip responding to some polls while blocked and within cooldown,
  which can reduce throughput and increase apparent loss at Alice.
- Consolidated logging could drop fields used by analysis if not careful; keep
  field keys stable where they are consumed.
