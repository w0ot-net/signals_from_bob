# DNS TXT Transport Plan

Status: draft

## Summary
Add a new `dns_txt` transport that uses DNS TXT queries/responses for tunnel
payloads while preserving the existing DNS query-name encoding, MTU
negotiation, and Alice-initiated asymmetry.

## Goals
- Provide `dns_txt` as a selectable transport with TXT query/response defaults.
- Encode Bob -> Alice payloads in TXT RDATA and decode them on the client.
- Maintain per-query response caps and clamp behavior for TXT responses.
- Keep the existing `dns` CNAME transport behavior unchanged.

## Non-Goals
- Replace or modify the existing CNAME-based DNS transport.
- Add TXT-based DNS stagers (`--stager` remains `--transport dns` only).
- Change tunnel reliability, retransmit, or keepalive behavior.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns/dns_codec.py`
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/mtu_limits.py`
- `sfb/transport/__init__.py`
- `sfb/config.py`
- `sfb/cli.py`
- `doc/architecture/DNS_TRANSPORT.md`
- `doc/architecture/TRANSPORTS.md`
- `doc/architecture/FLATTENER.md`
- `doc/flatten_manifest.txt`
- `README.md`

## Design Notes
- Transport name: `dns_txt`.
- Query encoding remains base32 in QNAME with nonce; query type is `TXT`.
- Response encoding uses TXT RDATA with base64 chunks
  (`dns_codec.encode_txt_rdata`/`decode_txt_rdata`).
- Per-query response caps must account for QNAME wire length and EDNS size.
- Alice initiates all polls; Bob only responds (no protocol asymmetry changes).
- Standard library only; Python 2.7/3 compatible; ASCII-only code; avoid
  comprehensions in `sfb/` to keep flat builds safe.

## Plan
1. Configuration and CLI wiring.
   - Register `dns_txt` in `sfb/transport/__init__.py`.
   - Treat `dns_txt` like `dns` for CLI args and required `--domain`.
   - In `create_config`, set `dns_query_type="TXT"` and
     `dns_response_type="TXT"` when `transport == "dns_txt"`.
   - Update `sfb/config.py` validation to allow TXT types (gated by transport)
     and only enforce `dns_cname_label` rules when response type is CNAME.
   - Ensure DNS log metadata uses the configured transport name.
2. TXT response-cap helpers in `sfb/transport/dns/dns_codec.py`.
   - Add a helper to compute TXT response payload caps using
     `qname_wire_len`, `edns_size`, and `opt_record_len`.
   - Add a small helper to compute the largest payload that fits in a given
     TXT RDATA byte budget (base64 expansion + per-chunk length bytes).
3. Client TXT support in `sfb/transport/dns/dns_client.py`.
   - Parse TXT answers and decode RDATA when `rtype == QTYPE_TXT`.
   - Extend clamp/response-cap tables to use the TXT helper when configured.
   - Keep polling, retransmit, and keepalive logic unchanged.
4. Server TXT support in `sfb/transport/dns/dns_server.py`.
   - Build TXT responses when `rtype == QTYPE_TXT` and skip CNAME follow-ups.
   - Use the TXT response-cap helper for per-query caps and max send MTU.
   - Keep CNAME behavior unchanged when `rtype == QTYPE_CNAME`.
5. MTU limits and documentation.
   - Add a `dns_txt` branch in `sfb/transport/mtu_limits.py` (alias to DNS).
   - Update `doc/architecture/DNS_TRANSPORT.md` with a `dns_txt` section
     covering TXT encoding, payload caps, and tradeoffs vs CNAME.
   - Update `doc/architecture/TRANSPORTS.md`, `doc/architecture/FLATTENER.md`,
     `doc/flatten_manifest.txt`, and `README.md` to list `dns_txt`.

## Testing
- Do not run tests.
