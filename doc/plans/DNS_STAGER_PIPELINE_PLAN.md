# DNS Stager Pipeline Plan

Status: draft

## Summary
Speed up DNS stager download by pipelining chunk requests while keeping
the existing stager query names.

## Goals
- Pipeline stager chunk fetches with a small in-flight window using a
  single UDP socket and select-based receive loop.
- Keep stager count/piece query names unchanged.
- Keep Python 2/3 compatibility and ASCII-only code/scripts.
- Preserve case-insensitive matching for stager queries.

## Non-Goals
- Shortening or renaming stager query labels or formats.
- Changing non-stager DNS transport behavior.
- Adding non-stdlib dependencies.
- Tests or e2e validation.

## Affected Components
- `sfb/stagers/dns_stager_template.py`
- `sfb/stagers/dns_stager.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Add a pipelined chunk fetch loop in the stager template.
   - Keep a single UDP socket and send up to a fixed window of missing
     indices before receiving responses (default window: 8, cap at count).
   - Track outstanding indices with last-sent timestamps and re-send
     after a short timeout, avoiding tight loops.
   - Use `select` for a bounded receive wait and parse any CNAME
     responses that match the expected piece query names.
   - Leave the count/piece query format unchanged.
   - Keep logic minimal and readable; avoid new helpers unless needed.

2. Wire generation and outputs.
   - Ensure `sfb/stagers/dns_stager.py` renders the updated template.
   - Regenerate `linux_dns_stager.txt` and `windows_dns_stager.txt` on
     `--stager` runs to keep outputs aligned.

## Testing
- Do not run tests here.
