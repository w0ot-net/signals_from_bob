# TLS Handshake Bump Autodetect Plan

## Summary
Add response extraction modes to the TLS handshake bump transport so Alice can
either use a user-supplied regex or automatically locate Bob's response token
via a sentinel-based token scan.

## Goals
- Support two response extraction modes:
  - Explicit regex with a single capture group for the base32 token.
  - Automatic extraction that does not require proxy-specific regexes.
- Keep Python 2.7/3 compatibility and standard library use.
- Preserve tunnel asymmetry (Alice initiates; Bob only responds).
- Keep Windows and Linux support.

## Non-Goals
- Perfect extraction for every proxy template (still best-effort).
- High throughput; this is a low-bandwidth channel.
- Persisting learned regexes across runs (session-only is enough).

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
1) Define extraction modes and configuration.
   - Add config fields:
     - `tls_bump_response_regex` (optional; capture group for base32 token).
     - `tls_bump_response_mode` with values like `regex`, `scan`.
   - CLI flags to set these values.
   - Default to `scan` if no regex is supplied.

2) Add a sentinel-framed token format for robust scanning.
   - Wrap response payload bytes with a small header:
     - 1 byte version, 2 bytes payload length, 2 bytes checksum (e.g., CRC32
       truncated), then payload.
   - Base32 encode the header+payload without padding; keep lowercase.
   - Decoder scans for base32 tokens, tries to decode, then validates header,
     length, and checksum to avoid false positives.
   - This allows proxy-agnostic extraction without a regex.

3) Integrate extraction into the client.
   - If `tls_bump_response_mode == regex`, use the configured regex directly.
   - If `scan`, extract by token scanning and validation.

4) Documentation and tests.
   - Document the new modes, config fields, and sentinel framing in
     `doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md`.
   - Add unit tests for token scan decoding, checksum rejection, and regex
     extraction with a captured error page snippet.

## Validation
- Run `python3 -m unittest tests.test_tls_handshake_bump_codec`
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`
- Do not run tests/e2e (user-run only).
