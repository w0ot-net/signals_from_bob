# DNS Stager Integrity Hash Plan

Status: draft

## Summary
Embed an expected payload hash directly in the generated DNS stager
one-liners so the stager can verify integrity after assembling all
pieces, without querying Bob for the hash.

## Goals
- Compute a deterministic hash of the payload during stager generation.
- Embed the expected hash in the rendered stager template.
- Validate the assembled payload before decompress/exec.
- Keep Python 2/3 compatibility and ASCII-only output.

## Non-Goals
- Adding non-stdlib dependencies.
- Adding extra DNS queries for integrity metadata.
- Changing DNS transport behavior beyond stager validation.

## Affected Components
- `sfb/stagers/dns_stager.py`
- `sfb/stagers/dns_stager_template.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Choose the hash input and encoding.
   - Hash the rendered `sfb_flat.py` bytes before gzip compression so
     integrity is checked against the final payload after decompression.
   - Use `hashlib.sha256` and embed the expected hex digest as ASCII.

2. Add a template placeholder for the expected hash.
   - Introduce `{{PAYLOAD_HASH}}` in `dns_stager_template.py` and render
     it as a string literal.

3. Compute and embed the hash during stager generation.
   - In `dns_stager.py`, compute the SHA-256 of the rendered `sfb_flat.py`
     payload bytes before gzip compression.
   - Pass the hex digest into the template renderer so the stager can
     compare against it.

4. Validate integrity in the stager before exec.
   - After assembling and decompressing `data`, compute the SHA-256 hex
     digest of the resulting `sfb_flat.py` bytes and compare to
     `PAYLOAD_HASH`.
   - If the hash mismatches, abort cleanly (return None) without exec.

## Testing
- Do not run tests here.
