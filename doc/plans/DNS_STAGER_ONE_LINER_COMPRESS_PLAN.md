# DNS Stager One-Liner Compress Plan

Status: draft

## Summary
Replace the previous minify/flatten approach for the stager one-liners with
a compression-only wrapper. The one-liners will embed the stager template
as a base64-encoded, max-compressed blob and unpack it immediately before
`exec`. No identifier renaming, minification, or flattening is performed.

## Goals
- Keep stager payload logic unchanged (no minify or rename passes).
- Wrap the payload in `base64.b64encode(zlib.compress(..., 9))`.
- Unpack right before `exec` in the one-liner.
- Preserve Python 2/3 compatibility and ASCII-only output.

## Non-Goals
- Minifying stager code or shortening identifiers.
- Flattening or rewriting the stager template.
- Modifying DNS protocol behavior.
- Tests or e2e validation.

## Affected Components
- `sfb/stagers/dns_stager.py`
- `sfb/stagers/dns_stager_template.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Keep the template readable.
   - Leave `dns_stager_template.py` as-is (no minify/rename passes).
   - Ensure it remains ASCII-only.

2. Compress + base64 the payload before emission.
   - In `sfb/stagers/dns_stager.py`, compress the rendered template with
     `zlib.compress(payload, 9)` and base64-encode it.
   - Embed the encoded blob in the one-liner, using only ASCII characters.

3. Decode/decompress immediately before exec.
   - Generate one-liners that do:
     `exec(zlib.decompress(base64.b64decode(BLOB)))`.
   - Keep the one-liner to a single line for each platform.

4. Verify outputs.
   - Ensure `linux_dns_stager.txt` and `windows_dns_stager.txt` are single
     lines and contain no comments or extra whitespace outside the wrapper.

## Testing
- Do not run tests here.
