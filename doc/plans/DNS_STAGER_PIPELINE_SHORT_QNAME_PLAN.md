# DNS Stager Pipeline + Short Qname Plan

Status: draft

## Summary
Speed up DNS stager download by pipelining chunk requests and shortening
stager query names to increase payload capacity per response.

## Goals
- Pipeline stager chunk fetches with a small in-flight window using a
  single UDP socket and select-based receive loop.
- Shorten stager count/piece query names to reduce qname length and
  slightly increase payload capacity.
- Ensure the stager never writes to disk.
- Keep Python 2/3 compatibility and ASCII-only code/scripts.
- Preserve case-insensitive matching for stager queries.

## Non-Goals
- Changing non-stager DNS transport behavior.
- Adding non-stdlib dependencies.
- Tests or e2e validation.

## Affected Components
- `sfb/stagers/dns_stager_template.py`
- `sfb/stagers/dns_stager.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `sfb/cli.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Shorten stager query labels and update server matching.
   - Replace `flat0` with a shorter label (e.g., `f0`).
   - Replace the count label `count` with a shorter label (e.g., `c`).
   - Update `sfb/stagers/dns_stager_template.py` constants to use the
     shorter labels and preserve nonce placement.
   - Update `sfb/transport/dns/dns_flat_stager.py` to match the new
     count/piece names and to emit the shorter names in responses.
   - Update `_calc_flat_payload_cap` in `sfb/cli.py` to use the new
     qname format so the chunk sizing matches the shorter labels.

2. Add a pipelined chunk fetch loop in the stager template.
   - Keep a single UDP socket and send up to a fixed window of missing
     indices before receiving responses.
   - Track outstanding indices with last-sent timestamps and re-send
     after a short timeout, avoiding tight loops.
   - Use `select` for a bounded receive wait and parse any CNAME
     responses that match the expected piece query names.
   - Keep logic minimal and readable; avoid new helpers unless needed.

3. Enforce no-disk writes in the stager runtime.
   - Set `sys.dont_write_bytecode = True` and
     `PYTHONDONTWRITEBYTECODE=1` at the top of the stager template before
     any imports to suppress `.pyc` creation.
   - Add a small write-guard (override `open`/`io.open`/`os.open`) during
     stager download that rejects write/append/create modes, and keep it
     enabled through `exec(payload)`.

4. Wire generation and outputs.
   - Ensure `sfb/stagers/dns_stager.py` renders the updated template.
   - Regenerate `linux_dns_stager.txt` and `windows_dns_stager.txt` on
     `--stager` runs to keep outputs aligned.

## Testing
- Do not run tests here.
