# TLS ClientHello Transport Plan

## Summary
Add a TCP-based transport that wraps SFB packets inside TLS handshake messages.
Alice sends a ClientHello carrying the request payload; Bob responds with a
ServerHello carrying the response payload. No real TLS session is established;
the tunnel already handles encryption.

## Goals
- Provide a TLS-looking request/response transport using ClientHello and
  ServerHello as the carrier.
- Keep Python 2.7/3 compatibility and use only the standard library.
- Support Windows and Linux (TCP sockets only, no raw socket requirements).
- Preserve tunnel asymmetry rules: Alice initiates; Bob only responds to polls.
- Support asymmetric MTU: independent send and receive capacities per side.

## Non-Goals
- Implement a full TLS stack or complete the TLS state machine.
- Perform certificate validation, ALPN negotiation, or encrypted extensions.
- Guarantee stealth against middleboxes; initial version targets correctness.

## Affected Components
- `sfb/transport/tls/codec.py` (new TLS handshake encoder/decoder)
- `sfb/transport/tls/tls_client.py` (new client transport)
- `sfb/transport/tls/tls_server.py` (new server transport)
- `sfb/transport/tls/__init__.py` (new transport package)
- `sfb/transport/__init__.py` (transport registry)
- `sfb/config.py` (TLS config defaults and validation)
- `sfb/cli.py` (transport selection and TLS args)
- `sfb/logging_util.py` (TLS component filtering)
- `sfb/log_profiles.py` (TLS log profiles)
- `doc/TRANSPORTS.md` (transport overview update)
- `doc/TLS_TRANSPORT.md` (new spec doc)
- `tests/test_tls_codec.py` (new codec tests)
- `tests/test_tls_client_server.py` (new transport tests)

## Transport Overview

Each poll is a new TCP connection:

```
Alice                                         Bob
  | TCP connect (host:port)                    |
  | ClientHello (SFB request payload)          |
  |------------------------------------------> |
  | ServerHello (SFB response payload)         |
  | <----------------------------------------- |
  | close                                      |
```

Pipelining is supported by opening multiple concurrent connections up to
`max_in_flight`. Correlation IDs are internal; each in-flight socket maps to
one request.

## Encoding Plan

### TLS Record and Handshake
- Use a single TLS Handshake record (content type 0x16).
- Record layer version: 0x0303 (TLS 1.2) for simplicity.
- Handshake types:
  - ClientHello (0x01) for Alice -> Bob
  - ServerHello (0x02) for Bob -> Alice
- Keep the handshake minimal but syntactically valid:
  - legacy_version 0x0303
  - random (32 bytes)
  - session_id (0-32 bytes)
  - cipher_suites list (at least one entry)
  - compression_methods = [0]
  - extensions list (may include SNI/ALPN for cover)

### SFB Payload Carrier
- Primary carrier: a dedicated extension, `EXT_SFB_DATA`.
  - Extension data format:
    - 2 bytes: magic "SF" (0x53 0x46)
    - 1 byte: version (0x01)
    - 1 byte: flags (0 for now)
    - 2 bytes: payload length (big-endian)
    - N bytes: payload (SFB packet bytes)
- Optional secondary carriers (phase 2, if needed for capacity or cover):
  - ClientHello random (fixed 32 bytes)
  - session_id (0-32 bytes)
  - session_ticket extension data
  - padding extension (only if zero-filled is not required for acceptance)
- Parsing should accept payload in `EXT_SFB_DATA` first; if absent, it may fall
  back to secondary carriers if enabled by config.

### Response Payload
- ServerHello includes `EXT_SFB_DATA` with the response payload.
- ServerHello random/session_id can be used for extra bytes if configured.

### Validation
- Enforce maximum handshake and extension lengths to avoid large allocations.
- Reject malformed records (bad lengths, unsupported handshake types).
- Return `(None, None)` on timeout; raise `TransportError` on hard parse errors.

## MTU Strategy
- Compute `send_mtu` from the maximum ClientHello payload capacity:
  `send_mtu = max(1, max_clienthello_bytes - tls_overhead_bytes)`.
- Compute `recv_mtu` from the maximum ServerHello payload capacity:
  `recv_mtu = max(1, max_serverhello_bytes - tls_overhead_bytes)`.
- Default `max_clienthello_bytes` and `max_serverhello_bytes` should be small
  enough to avoid fragmentation (e.g., 1200-1400 bytes) but configurable.
- Expose independent limits so MTU negotiation can clamp each direction
  separately.

## Client Transport Design
- Non-blocking TCP sockets with `select` for connect/send/recv.
- For each `send()`:
  - Build ClientHello with payload via codec.
  - Open socket, connect to target, send record, track as pending.
  - Return a monotonic correlation ID.
- For `recv()`:
  - Poll pending sockets for readable data.
  - Parse ServerHello and extract payload.
  - Close the socket and return `(corr_id, payload)`.
- Prune stale sockets using `PendingTracker` and `tls_pending_timeout`.

## Server Transport Design
- Listen on a TCP socket with configurable host/port.
- Accept a connection, read the ClientHello record, decode payload.
- `recv()` returns `(payload, responder)` where responder:
  - Builds ServerHello with response payload.
  - Sends it and closes the connection.
- Use socket timeouts to avoid blocking on partial reads.

## Configuration and CLI

Proposed config fields:
- `tls_target` (Alice host:port, default port 8443 to avoid root)
- `tls_listen_addr` (Bob listen host:port, default 0.0.0.0:8443)
- `tls_pending_timeout` (seconds)
- `tls_connect_timeout` (seconds)
- `tls_handshake_timeout` (seconds)
- `tls_max_clienthello_bytes`
- `tls_max_serverhello_bytes`
- `tls_sni` (optional cover name)
- `tls_alpn` (optional comma-separated list for cover)

CLI:
- `--transport tls`
- Alice: `--tls-target`, `--tls-sni`, `--tls-alpn`
- Bob: `--tls-listen-addr`

## Logging
- Add `log_component_transport_tls` toggle.
- Emit structured events for send/recv, parse errors, and pruning.
- Add a log profile to enable TLS transport logs.

## Tests
- Codec round-trip: build ClientHello/ServerHello, parse, payload match.
- Handshake length and bounds enforcement.
- Client/server loopback test with real sockets (no e2e tests).
- Pending timeout and pruning behavior.
- MTU calculation for configured max handshake sizes.

## Implementation Order
1. Write `doc/TLS_TRANSPORT.md` with the final wire format and constraints.
2. Implement `sfb/transport/tls/codec.py` and unit tests.
3. Implement client transport (`tls_client.py`).
4. Implement server transport (`tls_server.py`).
5. Register transport and add config/CLI/logging updates.
6. Add transport tests and update `doc/TRANSPORTS.md`.

## Success Criteria
- Alice and Bob can exchange SFB packets over the TLS handshake transport.
- Transport respects asymmetric MTU negotiation and max_in_flight limits.
- Unit tests validate codec correctness and basic client/server exchange.
