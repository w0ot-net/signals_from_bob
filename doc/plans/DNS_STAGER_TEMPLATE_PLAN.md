# DNS Stager Template Plan

Status: draft

## Summary
Create a `dns_stager_template.py` that contains the shared DNS stager logic,
with placeholders for base domain, passthrough args, and OS-specific
resolver discovery. The server-side generator will fill placeholders,
then emit Linux/Windows one-liner `.txt` stagers from the template.

## Goals
- Centralize stager logic in a single template file to avoid drift between
  Linux and Windows one-liners.
- Keep template and generated one-liners ASCII-only and Python 2/3 compatible.
- Minimize generator complexity: simple string substitution and one-liner
  wrapping.

## Non-Goals
- Runtime argument parsing inside the stager.
- Integrity checks beyond count metadata.
- Template engine dependencies.

## Affected Components
- `dns_stager_template.py` (new)
- Server-side stager generator (Phase 1 code)
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Template Structure
- Placeholders:
  - `{{BASE_DOMAIN}}` (string literal)
  - `{{SFB_ARGS}}` (Python list literal)
  - `{{RESOLVER_SNIPPET}}` (indented function body lines)
- Constants in template:
  - `COUNT_NAME = 'flat0.count.%s' % BASE_DOMAIN`
  - `PIECE_FMT = 'flat0.%05d.%s' % BASE_DOMAIN`
  - `TIMEOUT = 2.0`
- Functions in template:
  - `_byte_at`, `_b32decode`, `_encode_name`, `_read_name`
  - `_parse_cname` (first CNAME only)
  - `_decode_cname` (strip base domain suffix)
  - `_build_query`, `_resolver` (snippet injected), `_query`
  - `_fetch_count`, `_fetch_chunks`, `main`

## Plan
1. Add `dns_stager_template.py` with placeholders and shared logic.
   - Keep it ASCII-only; avoid platform-specific imports by default.
   - Use `zlib.decompress(data, 16 + zlib.MAX_WBITS)` for gzip.
   - Enforce Alice role in `sys.argv` before exec.

2. Define OS-specific resolver snippets.
   - Linux: parse `/etc/resolv.conf` for the first `nameserver` entry.
   - Windows: run `nslookup` and parse the `Server` block for an IP.
   - Snippets must be pure ASCII and rely only on stdlib.

3. Update the stager generator to render from the template.
   - Substitute `{{BASE_DOMAIN}}` and `{{SFB_ARGS}}`.
   - Inject the resolver snippet into `_resolver()`.
   - Ensure all generated content remains ASCII.

4. Emit one-liner `.txt` outputs from the rendered template.
   - Wrap the rendered template in a one-liner `python -c` invocation.
   - Use OS-appropriate quoting while keeping the payload identical.
   - Write `linux_dns_stager.txt` and `windows_dns_stager.txt` to repo root.

## Testing
- Do not run tests here.
