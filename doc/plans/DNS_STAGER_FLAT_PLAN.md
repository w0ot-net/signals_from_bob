# DNS Flat Stager Plan

Status: draft

## Summary
Add very thin, OS-specific DNS stagers that download `sfb_flat.py` over
DNS CNAME responses, assemble it in memory, and launch it with
pass-through flags set in the stager source. Bob will optionally serve
gzipped payload chunks in CNAME responses when `sfb.cli` is started with
`--sfb-flat`.

## Goals
- Provide minimal, Python 2/3-compatible `linux_dns_stager.py` and
  `windows_dns_stager.py` that can be run as a one-liner and depend only
  on the standard library.
- Avoid argument parsing in the stagers; values (domain, args) are set in
  the source to keep code size minimal.
- Support a `--sfb-flat` flag in `sfb.cli` that packages `sfb_flat.py` into
  gzipped bytes and serves chunks via DNS CNAME responses (base32 on the
  wire via CNAME encoding).
- Define a reliable, retry-until-complete download loop that fetches all
  pieces and verifies assembly before launching `sfb_flat.py`.
- Keep the DNS behavior aligned with Alice-initiated polling (stager queries
  initiate all data transfers).

## Non-Goals
- End-to-end or automated test coverage.
- Obfuscation, crypto, or transport changes beyond DNS responses for the
  stager payload.
- ICMP, TLS, or UDP changes.

## Affected Components
- `sfb/cli.py`
- `sfb/config.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_codec.py` (if helper(s) are needed)
- `linux_dns_stager.py` (new)
- `windows_dns_stager.py` (new)
- `README.md` (usage note, if needed)

## Plan
1. Define DNS stager naming and metadata.
   - Reserve a label prefix that cannot appear in base32 (e.g., `flat0`)
     to prevent collisions with tunnel query labels.
   - Use a small, deterministic query name for the count record, e.g.
     `flat0.count.<base_domain>` (or `count.<base_domain>` if we are willing
     to special-case it ahead of tunnel decoding).
   - Use fixed-width indexes and a fixed-length count label to keep query
     name length stable for payload sizing:
     - Count query: `flat0.count.<base_domain>`
     - Piece query: `flat0.%05d.<base_domain>` (1-based indexes)
   - Include a compact binary metadata payload in the count response:
     `struct.pack('>2sBI', b'SF', 1, count)` where `count` is a uint32.

2. Add `--sfb-flat` CLI support (server only).
   - Extend `sfb/cli.py` with a new `--sfb-flat <path>` option that is valid
     for the server role; reject it for client role to avoid ambiguity.
   - Read the file, gzip it, and keep the raw gzip bytes (CNAME encoding
     handles base32 on the wire).
   - Decide chunk size based on the DNS response payload cap for the fixed
     stager query name length and standard DNS size (512).
   - Store the prepared chunks and metadata on the config for the DNS server
     to serve (e.g., `config.dns_flat_chunks`, `config.dns_flat_meta`).

3. Serve stager chunks from the DNS server.
   - In `sfb/transport/dns/dns_server.py`, intercept stager query names
     before `decode_query_name()` to avoid base32 parsing of stager labels.
   - For `count` queries, respond with a CNAME whose target carries the
     metadata payload (base32-encoded by the existing CNAME encoder).
   - For `piece` queries, parse the index and respond with the corresponding
     chunk bytes, using the existing CNAME response path.
   - Return an empty NOERROR+SOA response for invalid indexes or missing
     metadata to keep resolver behavior predictable.
   - For stager responses, omit OPT and cap response sizing to standard DNS
     (512) regardless of `dns_edns_size`.
   - Add a small log event set (e.g., `dns.flat_count`, `dns.flat_piece`,
     `dns.flat_invalid`) to aid debugging.

4. Implement `linux_dns_stager.py` and `windows_dns_stager.py`.
   - Provide minimal DNS query logic using `socket` (UDP) and a tiny DNS
     encoder/decoder (parse header, question, first CNAME answer only).
   - Resolve the first CNAME target, decode base32 to recover chunk bytes.
   - Query `count`, parse the metadata struct, then loop until all pieces
     are retrieved, retrying missing pieces as needed.
   - Assemble decoded chunks in index order, then gunzip in memory
     (`zlib.decompress(data, 16 + zlib.MAX_WBITS)`).
   - Launch `sfb_flat.py` by `exec` in a `__main__` context, replacing
     `sys.argv` with a hardcoded args list (e.g., `SFB_ARGS = [...]`).
   - Linux resolver detection: parse `/etc/resolv.conf`.
   - Windows resolver detection: parse `nslookup` output (minimal).

5. Document usage.
   - Add a short README note with example server/stager invocation, including
     how to pass flags through the stager.

## Options / Improvements
- None for this pass.

## Testing
- Do not run tests here.
