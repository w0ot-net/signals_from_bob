# TLS Handshake Bump In-Memory Cert Plan

## Summary
Remove all runtime certificate file I/O from the TLS handshake bump transport by
using an in-memory certificate catalog and a symbol-stream response encoding,
so Bob can respond without disk reads/writes or external helpers.

## Goals
- Eliminate disk activity for bump certificates (no cert dir, no helper).
- Keep Python 2.7/3 compatibility and standard library only.
- Preserve Alice-initiated polling and Bob response asymmetry.
- Make response decoding robust without proxy-specific regex changes.

## Non-Goals
- High throughput (symbol-stream responses are intentionally slow).
- Maintaining compatibility with the current per-payload CN encoding.
- Persisting any certificate state across runs.

## Affected Components
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
1) Define an in-memory certificate catalog.
   - Add a new module that contains a fixed mapping of CN tokens to DER bytes,
     stored as base64 strings in code.
   - Build a small, power-of-two symbol alphabet (e.g., base32 characters),
     one cert per symbol, plus an optional "idle" cert for empty responses.
   - Remove `tls_bump_cert_dir` and `tls_bump_cert_helper` from config/CLI
     (breaking change is acceptable).

2) Replace CN payload encoding with a symbol stream.
   - Keep the existing payload header (version + length) and base32 encoding,
     but send it one symbol per response instead of as a full CN string.
   - Each response selects the cert whose CN equals the next symbol.
   - When no data is queued, respond with the "idle" cert (CN not in the symbol
     alphabet) so the client can ignore it.

3) Server changes for streaming responses.
   - When the tunnel invokes responder with a payload, base32-encode the full
     message and enqueue its symbols.
   - On each incoming poll, pop exactly one symbol (or idle) and send the
     corresponding cert record.
   - Maintain a bounded queue per server to avoid unbounded memory growth.

4) Client changes for reassembly.
   - Extract CN as today, then map CN to a symbol if it matches the catalog.
   - Accumulate symbols in a reassembly buffer; once enough symbols exist to
     decode the header and full payload length, decode and deliver the payload.
   - Ignore idle CNs and reset the buffer on decode errors.

5) MTU and validation updates.
   - Define a new logical response MTU for bump transport (max payload length
     that can be reassembled) and enforce it in validation.
   - Keep per-response CN length caps, but treat them as symbol capacity.

6) Docs and tests.
   - Document the in-memory cert catalog, symbol streaming, and new config
     defaults in `doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md` and `doc/TRANSPORTS.md`.
   - Update tests to cover symbol mapping, idle handling, and reassembly.

## Validation
- Run `python3 -m unittest tests.test_tls_handshake_bump_codec`
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`
- Do not run tests/e2e (user-run only).
