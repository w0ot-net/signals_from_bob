# DNS Flat Phase 2: Stagers

## Summary
Implement minimal, OS-specific DNS stager one-liners that fetch the gzipped
payload via CNAME responses, assemble it in memory, and exec `sfb_flat.py`
in Alice mode with args embedded by the server during stager generation.

## Goals
- Add `linux_dns_stager.txt` and `windows_dns_stager.txt` (Python 2/3, ASCII).
- No argument parsing; base domain, CNAME label, and args are constants in
  each generated stager.
- Resolver detection is OS-specific only (Linux: `/etc/resolv.conf`,
  Windows: `nslookup` output).

## Non-Goals
- Resolver overrides or CLI flags.
- Integrity checking beyond count validation.
- Retry backoff or jitter.

## Affected Components
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Implement minimal DNS query/response handling in the one-liner payload.
   - Build A queries with a tiny encoder.
   - Parse the header, question, and the first CNAME answer only.
   - Decode the CNAME target name, strip the configured suffix, and base32
     decode the remaining labels into bytes.

2. Implement resolver detection per OS.
   - Linux: parse `/etc/resolv.conf` for the first `nameserver` entry.
   - Windows: run `nslookup` and parse the `Server` block for an IP.

3. Implement the download loop.
   - Query `flat0.count.<base_domain>` to get count metadata.
   - Query each `flat0.%05d.<base_domain>` until all chunks are present.
   - Assemble chunks in index order and gunzip in memory using
     `zlib.decompress(data, 16 + zlib.MAX_WBITS)`.

4. Launch `sfb_flat.py`.
   - `exec` the payload in a `__main__` context and set `sys.argv` to the
     args list that always includes Alice role plus embedded args.

## Testing
- Do not run tests here.

## Execution Notes
- Extended the shared stager payload in `sfb/stagers/dns_stager_template.py` with a CNAME suffix constant.
- Updated CNAME decoding to strip the configured CNAME label + base domain suffix.
- Updated stager rendering/one-liner helpers in `sfb/stagers/dns_stager.py` for the new suffix placeholder.
- Tests not run (not requested).
