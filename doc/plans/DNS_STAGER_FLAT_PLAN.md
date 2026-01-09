# DNS Flat Stager Plan

Status: draft

## Summary
Add a very thin DNS stager that downloads `sfb_flat.py` over DNS CNAME
responses, assembles it in memory, and launches it with pass-through
flags. Bob will optionally serve a gzipped+base64 payload chunked into
CNAME responses when `sfb.cli` is started with `--sfb-flat`.

## Goals
- Provide a minimal, Python 2/3-compatible `dns_stager.py` that can be run
  as a one-liner and depends only on the standard library.
- Support a `--sfb-flat` flag in `sfb.cli` that packages `sfb_flat.py` into
  chunks served via DNS CNAME responses.
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
- `dns_stager.py` (new)
- `README.md` (usage note, if needed)

## Plan
1. Define DNS stager naming and metadata.
   - Reserve a label prefix that cannot appear in base32 (e.g., `flat0`)
     to prevent collisions with tunnel query labels.
   - Use a small, deterministic query name for the count record, e.g.
     `flat0.count.<base_domain>` (or `count.<base_domain>` if we are willing
     to special-case it ahead of tunnel decoding).
   - Use fixed-width indexes to keep query name length stable for payload
     sizing, e.g. `flat0.<index_padded>.<base_domain>`.
   - Include a minimal metadata payload in the count response, such as:
     `count`, `chunk_size`, and `sha256` of the gzipped bytes (or of the
     base64 text) so the stager can verify integrity and avoid stale caches.

2. Add `--sfb-flat` CLI support (server only).
   - Extend `sfb/cli.py` with a new `--sfb-flat <path>` option that is valid
     for the server role; reject it for client role to avoid ambiguity.
   - Read the file, gzip it, then base64 encode it (ASCII text).
   - Decide chunk size based on the DNS response payload cap for the stager
     query name length and EDNS size.
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
   - Add a small log event set (e.g., `dns.flat_count`, `dns.flat_piece`,
     `dns.flat_invalid`) to aid debugging.

4. Implement `dns_stager.py`.
   - Provide minimal DNS query logic using `socket` (UDP) and a tiny DNS
     encoder/decoder (parse header, question, first CNAME answer).
   - Resolve the CNAME target, decode base32 to recover chunk bytes.
   - Query `count`, then loop until all pieces are retrieved, retrying with
     a short sleep/backoff when a piece is missing or decode fails.
   - Assemble all chunks in index order, base64-decode, then gunzip in
     memory.
   - Launch `sfb_flat.py` by `exec` in a `__main__` context, replacing
     `sys.argv` with pass-through args (e.g., `dns_stager.py ... -- <args>`).

5. Document usage.
   - Add a short README note with example server/stager invocation, including
     how to pass flags through the stager.

## Options / Improvements
- Skip base64 entirely and encode gzip bytes directly into CNAME labels to
  reduce overhead (smaller payload and fewer DNS queries).
- Use TXT responses instead of CNAME to avoid follow-up lookups and simplify
  parsing, if resolver behavior allows.
- Include a version label (hash prefix) in the query names to avoid stale
  resolver caches between deployments.
- Provide a tiny `--resolver` override in the stager; otherwise use a
  lightweight system-resolver lookup similar to `dns_utils`.

## Testing
- Do not run tests here.
