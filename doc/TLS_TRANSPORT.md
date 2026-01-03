# TLS ClientHello Transport

This document specifies the TLS ClientHello transport, which encapsulates SFB
packet bytes inside minimal TLS 1.2 handshake messages carried over TCP. No
real TLS session is established; the tunnel already provides encryption.

---

## Overview

Each poll uses a fresh TCP connection:

```
Alice                                         Bob
  | TCP connect (host:port)                    |
  | ClientHello (SFB request payload)          |
  |------------------------------------------> |
  | ServerHello (SFB response payload)         |
  | <----------------------------------------- |
  | close                                      |
```

This preserves the transport asymmetry:
- Alice initiates all connections and polls for data.
- Bob only responds to incoming polls.

Connection churn can hit TIME_WAIT and ephemeral port limits at high rates.
Use `max_in_flight` and `tunnel_send_rate` to cap new connections. A rough
upper bound is:

```
max_new_conns_per_sec ~= ephemeral_port_range / time_wait_sec
```

Example: with a 16k ephemeral range and 60s TIME_WAIT, budget ~266 connects/sec.

---

## Wire Format

### TLS Record

Each connection carries exactly one TLS Handshake record:

```
struct {
    uint8  content_type = 0x16;       // Handshake
    uint16 version = 0x0303;          // TLS 1.2
    uint16 length;                    // bytes of handshake record payload
    Handshake handshake;              // exactly one handshake message
} TLSRecord;
```

Constraints:
- `length` MUST be <= 16384.
- The on-wire record size MUST be <= 16389 (5-byte header + 16384 payload).
- Only a single record is accepted; extra bytes are malformed.

### Handshake Header

```
struct {
    uint8  msg_type;                  // 0x01 ClientHello or 0x02 ServerHello
    uint24 length;                    // bytes of handshake body only
    opaque body[length];
} Handshake;
```

Constraint:
- `record.length` MUST equal `4 + handshake.length`.

---

## ClientHello (Alice -> Bob)

TLS 1.2 ClientHello body:

```
struct {
    uint16 legacy_version = 0x0303;
    opaque random[32];
    uint8  session_id_len;
    opaque session_id[session_id_len];          // 0..32 bytes
    uint16 cipher_suites_len;
    uint16 cipher_suites[cipher_suites_len/2];
    uint8  compression_methods_len;
    uint8  compression_methods[compression_methods_len];
    uint16 extensions_len;
    Extension extensions[extensions_len];
} ClientHello;
```

Constraints:
- `session_id_len` MUST be 0 in phase 1.
- `cipher_suites_len` MUST be even and >= 2.
- `compression_methods_len` MUST be 1 and value MUST be 0x00.
- Extensions are encoded as standard TLS 1.2 extensions (see below).

### Cipher Suites

The codec defines a fixed, ordered TLS 1.2 cipher suite list. Example list:

```
0xC02F  TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
0xC02B  TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
0x009C  TLS_RSA_WITH_AES_128_GCM_SHA256
```

The ClientHello MUST offer this list in order. The ServerHello selects the
first entry it supports from the client list.

---

## ServerHello (Bob -> Alice)

TLS 1.2 ServerHello body:

```
struct {
    uint16 legacy_version = 0x0303;
    opaque random[32];
    uint8  session_id_len = 0;
    uint16 cipher_suite;              // chosen from ClientHello list
    uint8  compression_method = 0x00;
    uint16 extensions_len;
    Extension extensions[extensions_len];
} ServerHello;
```

Constraints:
- `session_id_len` MUST be 0 (no resumption).
- `cipher_suite` MUST be one offered by the ClientHello.
- Only EXT_SFB_DATA is used in the response (if offered by the client).

---

## Extensions

TLS extension encoding (standard TLS 1.2):

```
struct {
    uint16 extension_type;
    uint16 extension_len;
    opaque extension_data[extension_len];
} Extension;
```

Unknown extension types are ignored after length validation.

### EXT_SFB_DATA (Private Use)

- Type: 0xFF00 (private-use range)
- Included in ClientHello and optionally in ServerHello.

Extension data format:

```
struct {
    uint8  magic[2] = "SF";           // 0x53 0x46
    uint8  version = 0x01;
    uint8  flags = 0x00;
    uint16 payload_len;
    opaque payload[payload_len];      // SFB packet bytes
} SfbData;
```

Rules:
- The payload is carried only in EXT_SFB_DATA for phase 1.
- If EXT_SFB_DATA is absent, treat as unsupported and drop.
- ServerHello includes EXT_SFB_DATA only if ClientHello offered it.

### SNI (Optional Cover)

If configured, include a standard `server_name` extension (type 0x0000) with
exactly one `host_name` entry. Validation rules:
- ASCII only, 1-253 characters total.
- Labels 1-63 characters, dot-separated.
- Allowed characters: A-Z, a-z, 0-9, hyphen, dot.
- No empty labels, no leading/trailing dot.

### ALPN (Optional Cover)

If configured, include a standard `application_layer_protocol_negotiation`
extension (type 0x0010). Validation rules:
- Comma-separated list of ASCII tokens.
- Each token length 1-255 bytes.
- No empty tokens.
- Total extension length MUST fit within the configured max record size.

---

## Framing and I/O

Each connection carries exactly one TLS record and one handshake message:
- Read exactly 5 bytes for the record header.
- Read exactly `record.length` bytes for the payload.
- Do not parse until the full record is available.

Malformed framing:
- EOF before header or full payload completes is malformed.
- Extra bytes after the single record are malformed.

---

## Validation and Error Handling

Validation checks (non-exhaustive):
- Record content type MUST be 0x16.
- Record version MUST be 0x0303.
- Handshake type MUST be ClientHello (0x01) or ServerHello (0x02).
- `record.length` MUST match `4 + handshake.length`.
- Extension lengths MUST fit within the handshake body.
- Session ID length MUST be <= 32.
- Compression list MUST be exactly [0x00].
- ServerHello cipher_suite MUST appear in the ClientHello list.

Error handling matches other transports:
- Malformed input: close the socket, drop pending state, log, return `(None, None)`.
- Only socket I/O failures raise `TransportError`.

---

## MTU and Limits

Configuration uses on-wire record sizes:
- `tls_max_clienthello_bytes`: maximum ClientHello record size on the wire
  (includes 5-byte record header).
- `tls_max_serverhello_bytes`: maximum ServerHello record size on the wire
  (includes 5-byte record header).

Derived transport MTUs:
- `send_mtu`: max SFB payload bytes Alice can send.
- `recv_mtu`: max SFB payload bytes Alice can receive.

MTU calculation:
- Build a minimal ClientHello/ServerHello with configured SNI/ALPN and
  EXT_SFB_DATA header, then subtract overhead from the max on-wire size.
- EXT_SFB_DATA overhead is 10 bytes (extension header + SFB header) before
  payload bytes.
- Clamp on-wire limits to 16389 bytes max.
- Reject configs where `send_mtu < PACKET_HEADER_SIZE + 1`.

---

## Transport Behavior

### Client (Alice)

- Non-blocking TCP sockets and `select`.
- `_send_impl()` starts a non-blocking connect and queues the ClientHello.
- If connect completes immediately, send immediately; otherwise return a
  correlation ID and finish the connect/send in `recv()`.
- `recv()` pumps pending sockets: finish connects, flush pending sends, read
  a full record, decode, then close.
- Use `PendingTracker` with `tls_pending_timeout`, plus per-connection
  `tls_connect_timeout` and `tls_handshake_timeout` tracked via `time_provider.now()`.

### Server (Bob)

- Non-blocking listener; accept and track active sockets.
- `recv()` polls both the listen socket and active sockets.
- Once a full ClientHello record is available, decode payload and return
  `(payload, responder)`.
- Responder sends ServerHello (handling partial sends) and closes the socket.
- Malformed input: close and continue (no exception to caller).

---

## Configuration

Required fields:
- `tls_target`: Alice target host:port (default port 8443).
- `tls_listen_addr`: Bob listen host:port (default 0.0.0.0:8443).
- `tls_pending_timeout`: pending request timeout (seconds).
- `tls_connect_timeout`: connect deadline (seconds).
- `tls_handshake_timeout`: handshake deadline (seconds).
- `tls_max_clienthello_bytes`: ClientHello on-wire size cap.
- `tls_max_serverhello_bytes`: ServerHello on-wire size cap.
- `tls_sni`: optional cover host name.
- `tls_alpn`: optional cover protocol list (comma-separated).

Defaults:
- `tls_target`: `127.0.0.1:8443`
- `tls_listen_addr`: `0.0.0.0:8443`
- `tls_pending_timeout`: `5.0`
- `tls_connect_timeout`: `3.0`
- `tls_handshake_timeout`: `5.0`
- `tls_max_clienthello_bytes`: `1400`
- `tls_max_serverhello_bytes`: `1400`
- `tls_sni`: `None`
- `tls_alpn`: `None`

Validation:
- Timeouts MUST be positive.
- Max sizes MUST be positive and <= 16389.
- SNI and ALPN values must pass the ASCII constraints above.

---

## Logging

Recommended structured events:
- `tls.send` / `tls.recv` for successful send/receive.
- `tls.parse_error` for malformed records.
- `tls.prune_stale` for dropped pending sockets.

Transport logging is gated by `log_component_transport_tls`.
