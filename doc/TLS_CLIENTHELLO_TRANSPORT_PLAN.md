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
Connection-per-poll can hit TIME_WAIT/ephemeral port limits at high rates; rely
on `max_in_flight`, `tunnel_send_rate`, and `tunnel_keepalive_interval` to cap
connect churn and document recommended polling rates.

## Encoding Plan

### TLS Record and Handshake
- Use a single TLS Handshake record (content type 0x16).
- Record layer version: 0x0303 (TLS 1.2) for simplicity.
- Handshake types:
  - ClientHello (0x01) for Alice -> Bob
  - ServerHello (0x02) for Bob -> Alice
- One record carries exactly one handshake message.
- TLS record length = 4 (handshake header) + handshake body length.
- Handshake length is the body length only (3-byte length field).
- Keep the handshake minimal but syntactically valid and deterministic.
- ClientHello body (TLS 1.2):
  - legacy_version = 0x0303
  - random (32 bytes)
  - session_id_len (1 byte) + session_id (0-32 bytes)
  - cipher_suites_len (2 bytes, even) + cipher_suites list (>= 1 entry)
  - compression_methods_len (1 byte) + compression_methods (single 0x00)
  - extensions_len (2 bytes) + extensions (SNI, ALPN, EXT_SFB_DATA)
- ServerHello body (TLS 1.2):
  - legacy_version = 0x0303
  - random (32 bytes)
  - session_id_len = 0 (server does not resume)
  - cipher_suite (2 bytes) selected from ClientHello list
  - compression_method = 0x00
  - extensions_len (2 bytes) + extensions (EXT_SFB_DATA only if offered)
- Cipher suites: define a fixed ordered list in the codec (e.g., a small
  TLS 1.2 list). ServerHello selects the first supported entry from the
  client list; if none match, treat as malformed and close.
- Extension type for `EXT_SFB_DATA` should use the private-use range
  (0xFF00-0xFFFE), with a fixed constant in the codec.
- ServerHello should only include `EXT_SFB_DATA` if the ClientHello offered it.

### Framing and I/O
- TCP is a stream: read exactly the 5-byte TLS record header, then read the
  record payload length.
- Do not parse until the full record is available; partial reads are normal
  and should be treated as incomplete.
- Each connection carries exactly one record; after parsing, close the socket.
- If EOF occurs before header or body completes, treat as malformed and close.
- If extra bytes remain after the single record, treat as malformed and close.
- Track write progress separately; only attempt to read after the full
  ClientHello has been sent.

### SFB Payload Carrier
- Primary carrier: a dedicated extension, `EXT_SFB_DATA` (type 0xFF00).
  - Extension data format:
    - 2 bytes: magic "SF" (0x53 0x46)
    - 1 byte: version (0x01)
    - 1 byte: flags (0 for now)
    - 2 bytes: payload length (big-endian)
    - N bytes: payload (SFB packet bytes)
- Optional secondary carriers (phase 2 only, not in initial implementation):
  - ClientHello random (fixed 32 bytes)
  - session_id (0-32 bytes)
  - session_ticket extension data
  - padding extension (only if zero-filled is not required for acceptance)
- Parsing should accept payload in `EXT_SFB_DATA` first; if absent, treat as
  unsupported unless secondary carriers are explicitly enabled by config.

### Response Payload
- ServerHello includes `EXT_SFB_DATA` with the response payload.
- ServerHello random/session_id can be used for extra bytes if configured
  (phase 2 only).

### Validation
- Enforce maximum handshake and extension lengths to avoid large allocations.
- Reject malformed records (bad lengths, unsupported handshake types).
- Treat incomplete reads as pending, not errors; return `(None, None)` on
  timeout.
- Enforce TLS record payload length <= 16384 and configured max sizes.
- Enforce `record_length == 4 + handshake_body_length` and drop otherwise.
- On parse errors, behave like other transports: close the socket, drop the
  pending entry, log, and return `(None, None)` (no `TransportError` unless a
  socket operation itself fails).

## MTU Strategy
- Define `max_clienthello_bytes` and `max_serverhello_bytes` as the on-wire TLS
  record size including the 5-byte record header.
- Compute `send_mtu` and `recv_mtu` as payload caps for SFB packet bytes, using
  codec helpers that build a minimal Hello and subtract the record, handshake,
  and extension overhead (including the `EXT_SFB_DATA` header).
- `send_mtu` and `recv_mtu` are transport payload MTUs (SFB packet bytes), not
  TLS record sizes.
- Default `max_clienthello_bytes` and `max_serverhello_bytes` should be small
  enough to avoid fragmentation (e.g., 1200-1400 bytes) but configurable.
- Expose independent limits so MTU negotiation can clamp each direction
  separately.
- Clamp configured max sizes to the TLS record limit
  (record payload <= 16384, on-wire <= 16389).
- MTU calculation must account for configured SNI/ALPN lengths and any enabled
  secondary carriers so computed caps remain valid.
- Reject configurations that cannot fit the minimum handshake + extension
  overhead or that yield a transport `send_mtu` smaller than
  `PACKET_HEADER_SIZE + 1`.

## Client Transport Design
- Implement `reserve_send()` with `PendingTracker` and `time_provider.now()`;
  implement `_send_impl()` only (do not override `send()`).
- Non-blocking TCP sockets with `select` for connect/send/recv.
- For each `send()`:
  - Build ClientHello with payload via codec.
  - Open socket in non-blocking mode, start `connect_ex`.
  - If connect completes immediately, send record and track as pending.
  - If connect is in progress, store state (socket, send buffer, offset,
    connect deadline, handshake deadline) and return a correlation ID
    immediately; send occurs when the socket becomes writable in `recv()`.
  - If `connect_ex` returns an immediate error (not in-progress), close and
    raise `TransportError`.
  - Return a monotonic correlation ID.
- For `recv()`:
  - Use `select` over pending sockets for writable and readable events.
  - For writable sockets: finish connects with `getsockopt(SO_ERROR)`, then
    flush pending sends (partial sends advance the offset).
  - Only after a full ClientHello is sent, poll for readable data.
  - Read and buffer until a full TLS record is available.
  - Parse ServerHello and extract payload.
  - Close the socket and return `(corr_id, payload)`.
- Prune stale sockets via `PendingTracker` using per-connection deadlines
  derived from `tls_connect_timeout` and `tls_handshake_timeout`. Use
  `tls_pending_timeout` as a safety net only; require it to be >= both
  timeouts (or default it to the larger of the two).
- Track per-connection deadlines using `time_provider.now()`; do not rely on
  wall-clock timeouts.

## Server Transport Design
- Listen on a TCP socket with configurable host/port.
- Accept connections and track active sockets with per-connection buffers and
  deadlines.
- Enforce a hard cap on active sockets using `max_in_flight`. If at capacity,
  accept then immediately close new connections to avoid busy-looping.
- `recv()` should poll both the listening socket and active sockets so one slow
  client does not block others (preserves `max_in_flight` behavior).
- When a full ClientHello record is available, decode payload.
- `recv()` returns `(payload, responder)` where responder:
  - Builds ServerHello with response payload.
  - Sends it (handling partial sends) and closes the connection.
- Use non-blocking sockets and `select` to avoid blocking on partial reads.
- Use `select` with non-blocking sockets for accept + recv; avoid mixing
  `socket.settimeout()` with a `select` loop.
- Track per-connection deadlines with `time_provider.now()` and drop stale or
  malformed connections.
- On malformed input, close the socket, log, and continue without raising to
  the caller.

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
- Validate max record sizes and timeouts in config (positive values and within
  TLS record limits).
- Require `tls_pending_timeout` to be >= `tls_connect_timeout` and
  `tls_handshake_timeout`, or default it to the larger value when unset.
- Validate configured max sizes against minimum handshake overhead (including
  SNI/ALPN if enabled) and reject configurations that cannot carry a packet.
- Validate `tls_sni` as ASCII, 1-253 chars, labels 1-63 with only
  letters/digits/hyphen/dot. Reject invalid characters.
- Validate `tls_alpn` entries as ASCII, 1-255 bytes each, no empty tokens, and
  total extension length within limits; reject invalid values.

CLI:
- `--transport tls`
- Alice: `--tls-target`, `--tls-sni`, `--tls-alpn`
- Bob: `--tls-listen-addr`

## Logging
- Add `log_component_transport_tls` toggle.
- Emit structured events for send/recv, parse errors, and pruning.
- Add a log profile to enable TLS transport logs.
- Add `tls.send`/`tls.recv` to the default event blacklist to control volume.

## Tests
- Codec round-trip: build ClientHello/ServerHello, parse, payload match.
- Handshake length and bounds enforcement.
- Fragmented read handling (header then body across multiple reads).
- Oversize record length rejection.
- Early EOF and extra-bytes handling (drop and close).
- Connect failure and timeout handling (including `connect_ex` immediate
  failures and deadline expiry).
- Partial send progress with non-blocking sockets (send advances offset).
- Client/server loopback test with real sockets (no e2e tests).
- Multiple concurrent in-flight connections to confirm no serialization.
- Pending timeout and pruning behavior.
- MTU calculation for configured max handshake sizes.
- Config validation for invalid SNI/ALPN values.
- Use `unittest` and ephemeral ports (bind 127.0.0.1:0) to avoid privileged
  ports and reduce flakiness.

## Implementation Order
1. Review/update `doc/TLS_TRANSPORT.md` with the final wire format and constraints.
2. Implement `sfb/transport/tls/codec.py` and unit tests.
3. Implement client transport (`tls_client.py`).
4. Implement server transport (`tls_server.py`).
5. Register transport and add config/CLI/logging updates.
6. Add transport tests and update `doc/TRANSPORTS.md`.

## Success Criteria
- Alice and Bob can exchange SFB packets over the TLS handshake transport.
- Transport respects asymmetric MTU negotiation and max_in_flight limits.
- Unit tests validate codec correctness and basic client/server exchange.
