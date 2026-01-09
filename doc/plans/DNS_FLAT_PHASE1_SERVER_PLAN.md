# DNS Flat Phase 1: Server Packaging + Serving

Status: draft

## Summary
Add server-side support for `--stager`, package `sfb_flat.py` into gzipped
chunks, auto-generate OS-specific stager one-liners, and serve stager
metadata/chunks via CNAME responses in DNS.

## Goals
- Add `--stager <path>` for the server role and package the file into
  gzipped chunks.
- Add `--passthrough ...` to embed args in generated stagers so they pass
  through to `sfb_flat.py` (stagers enforce Alice role).
- Serve `flat0.count.<base_domain>` and `flat0.%05d.<base_domain>` CNAME
  responses without affecting normal tunnel queries.
- Keep stager responses at standard DNS size (512) and omit OPT.

## Non-Goals
- Stager implementation.
- Documentation updates.
- Tests or e2e validation.

## Affected Components
- `sfb/cli.py`
- `sfb/config.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_codec.py` (if helpers are needed)
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Add a server-only `--stager <path>` CLI option in `sfb/cli.py`.
   - Validate the file exists; error on client role.
   - Gzip the file (bytes) and split into fixed-size chunks.
   - Store the chunk list and metadata on the config instance
     (e.g., `config.dns_flat_chunks`, `config.dns_flat_count`).
   - Add a `--passthrough` arg that captures all remaining tokens and store
     them on the config for stager generation.

2. Compute stager chunk size deterministically.
   - Use a fixed query name length based on `flat0.%05d.<base_domain>`.
   - Compute response payload cap with `dns_codec.calc_cname_response_payload_cap`
     using `DNS_STANDARD_SIZE` and `opt_record_len=0`.
   - Use that payload cap as the chunk size for gzip bytes.

3. Auto-generate OS-specific stager one-liners on every `--stager` invocation.
   - Generate `linux_dns_stager.txt` and `windows_dns_stager.txt` with the
     base domain (from Bob's `--domain`) and passthrough args embedded.
   - Write one-liners to the repo root so they can be copied and run
     directly.

4. Add stager query handling in `sfb/transport/dns/dns_server.py`.
   - Detect `flat0.count.<base_domain>` and `flat0.%05d.<base_domain>` before
     calling `decode_query_name()`.
   - For count queries, respond with CNAME payload:
     `struct.pack('>2sBI', b'SF', 1, count)`.
   - For piece queries, parse the 1-based index and respond with the chunk.
   - Return empty NOERROR+SOA for missing chunks or invalid indexes.
   - Ensure stager responses omit OPT and are capped to 512 bytes.

5. Add minimal logging for stager paths.
   - `dns.flat_count`, `dns.flat_piece`, `dns.flat_invalid`.

## Testing
- Do not run tests here.
