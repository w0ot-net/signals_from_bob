# ICMP Retransmit Debugging

## Goal
- Determine why retransmits occur on ICMP transport with a stable link.

## Current Setup
- Default log profile: `icmp_retransmit_debug`
- Command: `python3 -m sfb.cli --role alice --transport icmp --icmp_target <ip> --db-log`

## Observations
- (record timestamps and key events here)

## Hypotheses
- (e.g., request/response correlation mismatch, timing/backpressure, encoding issues)

## Next Steps
- (add experiments or code changes to validate hypotheses)

## Notes
- Keep entries short and include log event names when possible.
