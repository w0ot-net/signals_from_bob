# TLS Handshake Bump In-Memory Cert Plan (Fixed CN Template)

## Summary
Remove all runtime certificate file I/O from the TLS handshake bump transport by
using a single in-memory DER cert template with a fixed-length CN placeholder.
Each response patches the CN bytes and pads with base32 "a" characters so
arbitrary payload lengths (up to the fixed capacity) decode correctly. This
remains compatible with the response extraction modes and sentinel framing
defined in `TLS_HANDSHAKE_BUMP_AUTODETECT_PLAN.md`.

## Goals
- Eliminate disk activity for bump certificates (no cert dir, no helper).
- Keep Python 2.7/3 compatibility and standard library only.
- Preserve Alice-initiated polling and Bob response asymmetry.
- Stay compatible with response extraction modes (regex/scan).
- Make response decoding tolerant of fixed-length padding.
- Derive the CN length from the template and maximize it for broad hardware
  compatibility.

## Non-Goals
- Variable-length ASN.1 length patching for CN fields.
- Symbol-stream response encoding or multi-response reassembly.
- Persisting any certificate state across runs.
- Changing the response extraction semantics defined by autodetect.

## Affected Components
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_cert_template.py` (new)
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py`
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py`
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_server.py`
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py`
- `sfb/config.py`
- `sfb/cli.py`
- `doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md`
- `doc/TRANSPORTS.md`
- `tests/test_tls_handshake_bump_codec.py`
- `tests/test_tls_handshake_bump_client_server.py`

## Plan
1) Add a fixed-length in-memory cert template.
   - Add a new module that stores a base64 DER template with a CN placeholder
     of fixed length (CN_LEN), plus the CN byte offset (CN_OFFSET).
   - Choose CN_LEN as the largest ASCII length that still parses on common
     TLS stacks and embedded hardware; document the chosen limit.
   - Provide a helper to build cert DER by padding CN text with base32 "a"
     to CN_LEN and splicing into the template.
   - Remove `tls_bump_cert_dir` and `tls_bump_cert_helper` from config/CLI
     (breaking change is acceptable).

2) Update server response generation.
   - Replace the disk-backed cert provider with the template helper.
   - Use the response token encoder from the autodetect plan (header + payload,
     base32 without padding), then pad to CN_LEN and patch.
   - Continue building the TLS record from the patched DER.

3) Relax CN decode to allow fixed-length padding.
   - Add a response token decode helper that accepts trailing zero bytes after
     the header/payload and then validates the sentinel header/checksum.
   - Use this padded decode for CN responses only; keep SNI decode strict.
   - Ensure both extraction modes (regex/scan) can return the full padded token.

4) MTU and validation updates.
   - Remove `tls_bump_max_cn_len` from config/CLI and derive the CN length
     directly from the template CN_LEN.
   - Compute the send MTU from the fixed CN length and validate it normally.
   - Preserve asymmetric MTU rules (independent send/recv caps).

5) Docs and tests.
   - Document the fixed-length CN template, padding behavior, and compatibility
     with response extraction modes in `doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md`
     and `doc/TRANSPORTS.md`.
   - Update tests to cover padded decode, fixed length enforcement, and
     template patching.

## Validation
- Run `python3 -m unittest tests.test_tls_handshake_bump_codec`
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`
- Do not run tests/e2e (user-run only).
