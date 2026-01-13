# Alice Pacer Debug Guard Plan

Status: completed

## Summary
Avoid building pacer debug fields when neither DEBUG logging nor pacer summary
collection is enabled by early returning in pacer logging helpers.

## Goals
- Skip pacer state construction when DEBUG is disabled and summary interval is 0.
- Preserve existing debug log outputs and summary data collection when enabled.
- Keep pacer behavior unchanged.

## Non-Goals
- Change pacer algorithms or retransmit decisions.
- Modify pacer summary cadence or content.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/alice_tunnel.py`

## Plan
1. Add a guard helper or inline check for pacer debug work.
   - In `AliceTunnel`, add a small helper (for example,
     `_pacer_debug_fields_enabled`) that returns True when
     `self._logger.isEnabledFor(logging.DEBUG)` or
     `self._pacer_logger.summary_interval > 0`.
   - Keep it ASCII and avoid comprehensions.

2. Apply the guard to debug-only pacer logging.
   - At the top of `_maybe_log_pacer_target_change` and `_log_pacer_state`,
     return early when the guard is False.
   - Ensure this skips calls to `PacerLoggingHelper` field builders and avoids
     `AdaptivePacer._baseline_target` work.

3. Validate summary data flow remains intact.
   - Confirm `_log_pacer_state` still runs when summary interval > 0 so target
     sums are tracked even if DEBUG is off.

## Testing
- Do not run tests.

## Execution Notes
- Added inline early-return guards in pacer debug logging helpers so field
  construction is skipped when DEBUG is off and summaries are disabled.
