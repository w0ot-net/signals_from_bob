# DNS TXT Transport Plan

Status: draft

## Goal

Add a new `dns_txt` transport with dedicated client/server/codec modules that
carry tunnel data exclusively via TXT records while preserving the existing
Alice-initiated, request/response asymmetry.

## Non-Goals

- Replace or modify the existing CNAME-based `dns` transport.
- Add a TXT stager or other DNS recursion features.
- Add IPv6 support or non-UDP DNS transports.
- Add or run automated tests.

## Affected Components

- `sfb/transport/dns_txt/__init__.py`
- `sfb/transport/dns_txt/dns_txt_codec.py`
- `sfb/transport/dns_txt/dns_txt_client.py`
- `sfb/transport/dns_txt/dns_txt_server.py`
- `sfb/transport/__init__.py`
- `sfb/transport/mtu_limits.py`
- `sfb/cli.py`
- `sfb/logging_util.py`
- `doc/flatten_manifest.txt`
- `doc/architecture/DNS_TXT_TRANSPORT.md`
- `doc/architecture/TRANSPORTS.md`
- `doc/architecture/ARCHITECTURE.md`
- `doc/architecture/FLATTENER.md`
- `README.md`

## Design Notes

- Transport name: `dns_txt`.
- Query type and response type are fixed to `TXT`; TXT is the only RR type used
  for tunnel data.
- Alice embeds request payloads in the query name using base32 labels with the
  existing nonce label pattern; Bob replies with TXT RDATA encoded as base64
  strings (255-byte chunks) to match DNS TXT constraints.
- Reuse existing DNS config fields (`dns_base_domain`, `dns_resolver`,
  `dns_listen_addr`, `dns_edns_size`, `dns_recv_bufsize_min`,
  `dns_pending_timeout`, `dns_label_max_len`, `dns_response_ttl`); CNAME-specific
  fields are ignored by `dns_txt`.
- MTU: query MTU comes from `calc_query_mtu(base_domain, label_max_len)`;
  response MTU comes from `calc_response_mtu(QTYPE_TXT, edns_size)`. No fixed
  CNAME response cap or compression logic.
- EDNS0 OPT record is included when `dns_edns_size > 512`; response size is
  bounded by the configured EDNS size.
- Logging uses a `dns_txt.*` event prefix but is gated by the existing DNS
  component toggle (`log_component_transport_dns`).
- Use only the standard library; keep ASCII-only source and avoid list/dict/set
  comprehensions in `sfb/` modules for Python 2 minification safety.
- `sfb/transport/dns_txt` modules must not import from `sfb/transport/dns`;
  copy `dns_utils.py` verbatim into `sfb/transport/dns_txt` and implement
  remaining helpers locally.

## Implementation Steps

1. Codec module.
   - Create `dns_txt_codec.py` with TXT query/response helpers:
     - encode/decode query names implemented locally.
     - encode/decode TXT RDATA implemented locally.
     - build/parse query/response packets with TXT answers and EDNS0 OPT.
   - Define TXT/QCLASS/flags/constants locally; do not import from
     `sfb.transport.dns`.
2. Shared DNS utilities.
   - Copy `sfb/transport/dns/dns_utils.py` verbatim to
     `sfb/transport/dns_txt/dns_utils.py` and use it for resolver discovery.
3. Client transport.
   - Implement `DnsTxtClient` with pipelined queries, `PendingTracker`, and
     DNS-ID correlation.
   - Build TXT queries, parse TXT answers, and decode TXT payloads.
   - Enforce send MTU and derive recv MTU via `resolve_mtu_limits('dns_txt', ...)`.
4. Server transport.
   - Implement `DnsTxtServer` to parse TXT queries, decode query payloads, and
     respond with TXT answers or empty NOERROR responses for mismatches.
   - Use `dns_response_ttl` for answer TTL and include EDNS0 OPT when configured.
5. Transport plumbing.
   - Register `dns_txt` in `sfb/transport/__init__.py`.
   - Add MTU resolution for `dns_txt` in `sfb/transport/mtu_limits.py`.
   - Update CLI to accept `--transport dns_txt`, require `--domain`, reuse DNS
     target/listen args, and wire `create_config` to the DNS builder.
   - Extend logging filter to recognize `dns_txt` logger/event prefixes under
     the DNS component.
6. Documentation and flattener updates.
   - Add `doc/architecture/DNS_TXT_TRANSPORT.md` describing framing, MTUs, and
     configuration.
   - Update `doc/architecture/TRANSPORTS.md`, `doc/architecture/ARCHITECTURE.md`,
     `doc/architecture/FLATTENER.md`, and `README.md` to list `dns_txt`.
   - Add dns_txt modules to `doc/flatten_manifest.txt`.

## Validation

- Manual localhost check with python3 using port 5353 for direct mode and port
  53 for authoritative mode; confirm send/recv, MTU logs, and retry behavior.
- Do not run tests in `tests/e2e/`.
