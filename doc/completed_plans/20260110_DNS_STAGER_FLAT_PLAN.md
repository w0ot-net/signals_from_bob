# DNS Flat Stager Plan

Status: completed

## Summary
Add very thin, OS-specific DNS stagers that download `sfb_flat.py` over
DNS CNAME responses, assemble it in memory, and launch it in Alice mode
with pass-through flags embedded in the stager source. Bob will
automatically generate stager one-liners and serve gzipped payload
chunks in CNAME responses when `sfb.cli` is started with `--stager`.

## Goals
- Provide minimal, Python 2/3-compatible Linux and Windows stagers as
  generated one-liners (stored in `.txt` files) that depend only on the
  standard library.
- Avoid argument parsing in the stagers; values (base domain and args) are
  set in the generated one-liner to keep code size minimal. The base
  domain is taken from Bob's `--domain` value.
- Support a `--stager` flag in `sfb.cli` that packages `sfb_flat.py` into
  gzipped bytes and serves chunks via DNS CNAME responses (base32 on the
  wire via CNAME encoding).
- Add a `--passthrough` CLI flag for Bob to capture args that will be
  embedded into generated stagers and passed to `sfb_flat.py` (Alice
  role is enforced by the stager).
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
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)
- `README.md` (usage note, if needed)

## Phases
Phase 1: server packaging, stager generation, and DNS serving
- Define naming and metadata:
  - Reserve a label prefix that cannot appear in base32 (e.g., `flat0`) to
    prevent collisions with tunnel query labels.
  - Use a small, deterministic query name for the count record, e.g.
    `flat0.count.<base_domain>` (or `count.<base_domain>` if we are willing
    to special-case it ahead of tunnel decoding).
  - Use fixed-width indexes and a fixed-length count label to keep query
    name length stable for payload sizing:
    - Count query: `flat0.count.<base_domain>`
    - Piece query: `flat0.%05d.<base_domain>` (1-based indexes)
  - Include a compact binary metadata payload in the count response:
    `struct.pack('>2sBI', b'SF', 1, count)` where `count` is a uint32.
- Add server CLI support:
  - Extend `sfb/cli.py` with a new `--stager <path>` option that is valid
    for the server role; reject it for client role to avoid ambiguity.
  - Add `--passthrough ...` to capture args for the stager (must be last).
  - Read the file, gzip it, and keep the raw gzip bytes (CNAME encoding
    handles base32 on the wire).
  - Decide chunk size based on the DNS response payload cap for the fixed
    stager query name length and standard DNS size (512).
  - Store the prepared chunks and metadata on the config for the DNS server
    to serve (e.g., `config.dns_flat_chunks`, `config.dns_flat_meta`).
  - Generate `linux_dns_stager.txt` and `windows_dns_stager.txt` on every
    `--stager` invocation with the base domain and passthrough args
    embedded in the one-liner, and force Alice role in the stager args.
  - Write the one-liners into the repo root.
- Serve stager chunks from the DNS server:
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

Phase 2: stagers
- Implement Linux and Windows stager one-liners:
  - Provide minimal DNS query logic using `socket` (UDP) and a tiny DNS
    encoder/decoder (parse header, question, first CNAME answer only).
  - Resolve the first CNAME target, decode base32 to recover chunk bytes.
  - Query `count`, parse the metadata struct, then loop until all pieces
    are retrieved, retrying missing pieces as needed.
  - Assemble decoded chunks in index order, then gunzip in memory
    (`zlib.decompress(data, 16 + zlib.MAX_WBITS)`).
  - Launch `sfb_flat.py` by `exec` in a `__main__` context, replacing
    `sys.argv` with an args list that always includes Alice role plus
    the embedded passthrough args.
  - Linux resolver detection: parse `/etc/resolv.conf`.
  - Windows resolver detection: parse `nslookup` output (minimal).

Phase 3: documentation
- Add a short README note with example server/stager invocation, including
  how to pass flags through the stager and how to use the generated
  one-liner files.

## Options / Improvements
- None for this pass.

## Testing
- Do not run tests here.

## Execution Notes
- Implemented DNS stager generation in `sfb/cli.py` with `--stager` and
  `--passthrough`, including auto-flattening, gzip chunking, and config wiring.
- Added DNS flat stager server handling via `sfb/transport/dns/dns_flat_stager.py`
  and the DNS server hook, using cache-buster labels, seeded index tokens, and
  standard 512-byte responses without OPT records.
- Added the DNS stager template/renderer to produce Linux/Windows one-liners
  that fetch count/chunks over DNS, verify payload hash, and exec `sfb_flat.py`
  in Alice mode with passthrough args.
- Updated `README.md` and generated `linux_dns_stager.txt` and
  `windows_dns_stager.txt`.
- Tests not run (per instructions).
