# DNS Stager Timeout Strategy Plan

Status: completed

## Summary
Add bounded timeout controls to the DNS stager so it self-terminates when it
cannot complete, while preserving retry behavior under loss. Implement:
- A fixed 600s wall-clock deadline for the full stager download.
- A no-progress timeout that resets on each newly received chunk.
- A per-chunk resend cap to avoid infinite loops on missing chunks.

## Goals
- Ensure the stager exits cleanly when progress stalls or a chunk is not
  recoverable.
- Keep retry behavior for transient loss without hanging forever.
- Preserve Python 2/3 compatibility and ASCII-only stager code.
- Keep code minimal and logging light.

## Non-Goals
- Protocol changes outside the DNS stager path.
- Automated tests.
- Changing the stager query name format or index mapping.

## Affected Components
- `sfb/stagers/dns_stager_template.py`
- `sfb/stagers/dns_stager.py` (if new constants/metadata are surfaced)
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
### Phase 1: Constants and defaults
- Add stager constants for:
  - `STAGER_TOTAL_TIMEOUT = 600.0`
  - `STAGER_NO_PROGRESS_TIMEOUT` (derived from pipeline timings; clamp to sane
    min/max)
  - `STAGER_MAX_SENDS_PER_CHUNK` and count-query retry cap
- Ensure constants are ASCII-safe and do not introduce new dependencies.

### Phase 2: Count fetch timeout
- Track `start_time` and `last_progress` in `_fetch_count`.
- Abort and return `None` when:
  - Wall-clock exceeds `STAGER_TOTAL_TIMEOUT`, or
  - No-progress exceeds `STAGER_NO_PROGRESS_TIMEOUT`, or
  - Count retry cap is exceeded.

### Phase 3: Chunk fetch timeouts
- Track `start_time` and `last_progress` in `_fetch_chunks`.
- Track resend counts per index.
- Abort and return `None` when:
  - Wall-clock exceeds `STAGER_TOTAL_TIMEOUT`, or
  - No-progress exceeds `STAGER_NO_PROGRESS_TIMEOUT`, or
  - A chunk exceeds its resend cap.

### Phase 4: Regeneration
- Regenerate `linux_dns_stager.txt` and `windows_dns_stager.txt` on the next
  `--stager` run.

## Testing
- Do not run tests.

## Execution Notes
- Added total/no-progress deadline checks plus per-chunk resend and count-query caps in the DNS stager template.
- Shared the stager wall-clock deadline across count and chunk fetches while resetting no-progress on new chunk receipt.
- Regenerated `linux_dns_stager.txt` and `windows_dns_stager.txt`.
- Tests not run (per instructions).
