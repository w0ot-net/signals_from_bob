# Bob Retransmit Cooldown

This document describes how Bob computes and applies the retransmit cooldown
period today. It is a focused view of the cooldown gate inside Bob's
opportunistic retransmit path.

## Scope And Entry Points

Primary implementation locations:
- `sfb/tunnel/bob_tunnel.py`: poll EWMA updates, cooldown computation, and
  cooldown/ACK-progress gating during response selection.
- `sfb/reliability/send_window.py`: last cumulative ACK timing used by the
  ACK-progress gate.
- `sfb/config.py`: cooldown-related configuration defaults.
- `doc/architecture/ASYMMETRY.md`: Bob is poll-driven, not timer-driven.

## Time Source And Units

- All protocol timing uses `time_provider.now()` (monotonic seconds).
- Cooldown comparisons use seconds.

## Poll Interval EWMA (Opportunity Rate)

Bob tracks the cadence of Alice's polls and uses it as the base signal for
cooldown:
- On each request, `_update_poll_ewma()` runs before packet decode.
- It computes `interval = now - last_request_time` and clamps negative values
  to 0.
- The EWMA is updated as:
  `ewma = alpha * interval + (1 - alpha) * ewma`.
- `alpha` comes from `tunnel_bob_poll_ewma_alpha`.
- The first request only seeds `last_request_time`; the EWMA is `None` until
  the second request.

This EWMA reflects how often Bob *can* respond, which is the limiting factor
for Bob's throughput and retransmit opportunity.

## Cooldown Computation

`_retransmit_cooldown()` calculates the cooldown per request:

1. Start with the baseline:
   - `cooldown = tunnel_bob_retransmit_min_interval`
2. If a poll EWMA exists and is positive:
   - If `tunnel_bob_retransmit_poll_factor > 0`,
     `cooldown = max(cooldown, poll_ewma * factor)`.
   - If the send window has a positive cap, also floor by one window of polls:
     `cooldown = max(cooldown, poll_ewma * max_in_flight)`.
3. If `tunnel_bob_retransmit_max_interval` is set and > 0,
   `cooldown = min(cooldown, max_interval)`.

The send-window cap used here is the current `_send_window._max_in_flight`.

## Cooldown And ACK-Progress Gates

During `_select_response_action()`:
- Bob selects the oldest unacked packet by `send_time`.
- `age = now - send_time` (if `send_time` is set).
- `since_cum_ack = send_window.ack_silence(now)` measures time since the last
  cumulative ACK advance (not the last time *any* packet was acked).

Retransmit is skipped if either of these gates fires:
- `age < cooldown` (reason `cooldown`), or
- `since_cum_ack < cooldown` (reason `ack_progress`).

Notes:
- The age gate is checked first; if it fires, the ACK-progress gate is not
  evaluated.
- If `send_time` or `since_cum_ack` is unavailable, the corresponding gate is
  skipped.
- On a successful retransmit, `send_time` is updated, which resets the age
  gate for that packet.

If the retransmit is skipped, Bob may still send new data or a keepalive in
the same response.

## Overrides (Cooldown Bypassed)

The cooldown gates are bypassed for window enforcement cases:
- Send window full (`window_full`).
- Send window distance exceeded (`window_distance`).

In these cases, Bob retransmits the oldest unacked packet even if the cooldown
or ACK-progress gate would have skipped it.

## Logging

When a retransmit is skipped due to cooldown or ACK progress, Bob logs
`tunnel.retransmit_skip` with:
- `reason`: `cooldown` or `ack_progress`
- `age`, `cooldown`, `since_cum_ack`
- `poll_ewma`, `unacked`, `max_in_flight`

## Configuration Defaults

From `sfb/config.py`:
- `tunnel_bob_retransmit_min_interval = 0.02`
- `tunnel_bob_retransmit_max_interval = 3.0`
- `tunnel_bob_retransmit_poll_factor = 2.0`
- `tunnel_bob_poll_ewma_alpha = 0.2`

These defaults are overridden by CLI flags or config injection as usual.
