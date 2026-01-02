# ICMP Client Ident Check Plan

## Goal

Remove the redundant ident check in `IcmpClient._try_recv()` since
`parse_icmp_echo()` already enforces `expect_ident`.

## Current Behavior

- `_try_recv()` calls `parse_icmp_echo(..., expect_ident=self._icmp_id)`.
- The function then unpacks `ident` from the result and compares it to
  `self._icmp_id` again.
- That branch is unreachable because `parse_icmp_echo()` returns `None` on a
  mismatched ident, so the extra comparison is wasted per packet.

## Plan

1. Delete the redundant `ident != self._icmp_id` branch in
   `sfb/transport/icmp/icmp_client.py`.
2. Keep `expect_ident` passed to `parse_icmp_echo()` so mismatched packets are
   still rejected early.
3. Add a unit test in `tests/test_icmp_packet.py` to assert that
   `parse_icmp_echo(..., expect_ident=...)` rejects a packet with a different
   ident. This locks in the assumption that makes the branch redundant.

## Implementation Sketch

- In `sfb/transport/icmp/icmp_client.py`, remove the ident comparison after
  `parse_icmp_echo()` returns a result.
- In `tests/test_icmp_packet.py`, add a case that builds a packet with one
  ident and parses it with a different `expect_ident`, asserting `None`.

## Tests

- `python3 -m unittest tests/test_icmp_packet.py`

## Notes

- No behavior change expected; mismatched ident packets are already rejected by
  `parse_icmp_echo()`.
- ICMP remains Linux-only; no Windows-specific work needed.
