# DNS Flat Phase 2: Stagers

Status: draft

## Summary
Implement minimal, OS-specific DNS stagers that fetch the gzipped payload
via CNAME responses, assemble it in memory, and exec `sfb_flat.py` with
hardcoded args.

## Goals
- Add `linux_dns_stager.py` and `windows_dns_stager.py` (Python 2/3, ASCII).
- No argument parsing; base domain, CNAME label, and args are constants in
  each stager.
- Resolver detection is OS-specific only (Linux: `/etc/resolv.conf`,
  Windows: `nslookup` output).

## Non-Goals
- Resolver overrides or CLI flags.
- Integrity checking beyond count validation.
- Retry backoff or jitter.

## Affected Components
- `linux_dns_stager.py`
- `windows_dns_stager.py`

## Plan
1. Implement minimal DNS query/response handling.
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
     hardcoded args list.

## Testing
- Do not run tests here.
