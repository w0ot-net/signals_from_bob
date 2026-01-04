# TLS Handshake Bump Autodetect Plan

## Summary
Add response extraction modes to the TLS handshake bump transport so Alice can
either use a user-supplied regex or automatically locate Bob's response token
via a probe-based regex builder and/or a sentinel-based token scan.

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
     - `tls_bump_response_mode` with values like `regex`, `autodetect`, `scan`.
   - CLI flags to set these values.
   - Default to `autodetect`, falling back to `scan` if regex learning fails.

2) Add a sentinel-framed token format for robust scanning.
   - Wrap response payload bytes with a small header:
     - 1 byte version, 2 bytes payload length, 2 bytes checksum (e.g., CRC32
       truncated), then payload.
   - Base32 encode the header+payload without padding; keep lowercase.
   - Decoder scans for base32 tokens, tries to decode, then validates header,
     length, and checksum to avoid false positives.
   - This allows proxy-agnostic extraction without a regex.

3) Implement autodetect regex learning (optional).
   - Add a probe mode where Alice sends a reserved probe request and Bob
     responds with a fixed probe payload (encoded with the same framing).
   - Alice scans the proxy response for the probe token and, if found, builds
     a regex by anchoring to nearby literal text.
   - Store the learned regex in client state for the session only.
   - If probe fails, fallback to `scan` mode.

4) Integrate extraction into the client.
   - If `tls_bump_response_mode == regex`, use the configured regex directly.
   - If `autodetect`, run probe once; on success use learned regex; otherwise
     use `scan` for subsequent responses.
   - Ensure the probe does not reach the tunnel (transport-level handling).

5) Server changes for probe handling.
   - Recognize a reserved probe request (distinct header/version) and return a
     fixed probe payload without forwarding to the tunnel.
   - Keep normal request handling unchanged for real payloads.

6) Documentation and tests.
   - Document the new modes, config fields, and sentinel framing in
     `doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md`.
   - Add unit tests for token scan decoding, checksum rejection, and regex
     extraction with a captured error page snippet.
   - Add a probe-mode test that ensures probe requests are consumed by the
     transport and do not reach the tunnel.

## Validation
- Run `python3 -m unittest tests.test_tls_handshake_bump_codec`
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`
- Do not run tests/e2e (user-run only).
