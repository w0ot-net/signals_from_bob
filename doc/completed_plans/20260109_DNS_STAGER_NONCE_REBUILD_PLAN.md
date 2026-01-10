# DNS Stager Nonce + Rebuild Plan

Status: completed

## Summary
Make DNS stager generation fully fresh on every run by introducing a per-run
nonce in stager query names, rebuilding count metadata after clamping, and
forcing all stager artifacts to be regenerated rather than reused. This
eliminates reliance on cached DNS responses or stale local files.

## Goals
- Generate a per-run stager nonce and include it in the count/piece query
  names used by the stager and Bob's DNS responder.
- Rebuild `flat_meta` whenever the chunk count is clamped to avoid count
  mismatches between stager and server.
- Ensure `--stager` always regenerates `sfb_flat.py` and one-liner stager
  outputs in the same run.
- Keep Python 2/3 compatibility and ASCII-only code/scripts.

## Non-Goals
- Adding non-stdlib dependencies.
- Changing DNS transport behavior outside stager count/piece naming.
- Tests or e2e validation.

## Affected Components
- `sfb/cli.py`
- `sfb/config.py`
- `sfb/stagers/dns_stager.py`
- `sfb/stagers/dns_stager_template.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `sfb/transport/dns/dns_server.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Add a per-run stager nonce.
   - Generate a short ASCII nonce label that includes a non-base32
     character (e.g., `n-<8hex>`) to avoid collisions with data labels.
   - Store it in config (e.g., `dns_stager_nonce`) so Bob and the stager
     share the same value for a given run.

2. Wire the nonce into stager query names.
   - Add a `{{STAGER_NONCE}}` placeholder in `dns_stager_template.py`.
   - Update `COUNT_NAME` and `PIECE_FMT` to include the nonce label
     (e.g., `flat0.<nonce>.count.<base>` and `flat0.<nonce>.%05d.<base>`).
   - Update the stager generator to render the nonce into the template.
   - Update `_calc_flat_payload_cap` in `sfb/cli.py` to use the nonce-aware
     query name so the chunk sizing matches the new label length.

3. Rebuild metadata after clamping.
   - In `DnsFlatStager.__init__`, after clamping `flat_count`, regenerate
     `flat_meta` if its count does not match `_flat_count`.
   - Log a debug event when meta is rebuilt due to a clamp/mismatch.

4. Ensure Bob serves the nonce-aware names.
   - In `dns_flat_stager.py`, match count/piece names using the nonce
     (prefix/suffix updated to include the nonce label).
   - Ensure the same nonce is used in the DNS server config for the run.

5. Always regenerate stager artifacts on `--stager`.
   - Make `--stager` always rebuild `sfb_flat.py` (ignore existing file or
     remove the path override to prevent reuse).
   - Overwrite `linux_dns_stager.txt` and `windows_dns_stager.txt` on every
     run (current behavior, but keep it explicit in the flow).

## Testing
- Do not run tests here.

## Execution Notes
- Added per-run DNS stager nonce wiring across config, CLI, template, and server.
- Rebuilt flat stager metadata after count clamp/mismatch with debug logging.
- `--stager` now always rebuilds `sfb_flat.py` (path override ignored).
- Regenerated `linux_dns_stager.txt` and `windows_dns_stager.txt`.
- Tests not run (per instructions).
