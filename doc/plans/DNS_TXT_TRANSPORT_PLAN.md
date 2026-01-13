# DNS TXT Transport Plan

Status: draft

## Summary
Add a new `dns_txt` transport implemented in its own package
(`sfb/transport/dns_txt`) with client/server/codec modules that use DNS TXT
queries/responses for tunnel payloads while preserving MTU negotiation and
Alice-initiated asymmetry.

## Goals
- Provide `dns_txt` as a selectable transport with its own implementation.
- Encode Bob -> Alice payloads in TXT RDATA and decode them on the client.
- Maintain per-query response caps and clamp behavior for TXT responses.
- Keep the existing `dns` CNAME transport behavior unchanged.

## Non-Goals
- Replace or modify the existing CNAME-based DNS transport.
- Add TXT-based DNS stagers (`--stager` remains `--transport dns` only).
- Change tunnel reliability, retransmit, or keepalive behavior.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns_txt/__init__.py`
- `sfb/transport/dns_txt/dns_txt_codec.py`
- `sfb/transport/dns_txt/dns_txt_client.py`
- `sfb/transport/dns_txt/dns_txt_server.py`
- `sfb/transport/dns_txt/dns_txt_utils.py` (if helper split is needed)
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
- Response encoding uses TXT RDATA with base64 chunks handled by
  `dns_txt_codec`.
- Per-query response caps must account for QNAME wire length and EDNS size.
- Alice initiates all polls; Bob only responds (no protocol asymmetry changes).
- Standard library only; Python 2.7/3 compatible; ASCII-only code; avoid
  comprehensions in `sfb/` to keep flat builds safe.

## Plan
1. New `dns_txt` package scaffolding.
   - Create `sfb/transport/dns_txt/` with `__init__.py`,
     `dns_txt_codec.py`, `dns_txt_client.py`, and `dns_txt_server.py`.
   - Start by cloning the DNS transport structure, then strip CNAME-specific
     paths (CNAME follow-ups, cname label handling) in favor of TXT.
   - Keep DNS resolver discovery and base-domain normalization logic local to
     `dns_txt` (avoid cross-importing `sfb/transport/dns`).
2. TXT codec utilities in `sfb/transport/dns_txt/dns_txt_codec.py`.
   - Implement TXT RDATA encode/decode plus MTU helpers for TXT responses
     (based on QNAME wire length, EDNS size, and OPT record length).
   - Keep constants/QNAME encoding local to the new codec module.
3. Client transport in `sfb/transport/dns_txt/dns_txt_client.py`.
   - Implement send/recv using TXT queries and TXT response parsing.
   - Build response-cap lookup tables with the TXT MTU helper.
   - Preserve pipelining, pending tracking, and polling behavior.
4. Server transport in `sfb/transport/dns_txt/dns_txt_server.py`.
   - Implement TXT response building and per-query payload caps.
   - Drop CNAME follow-up handling; TXT queries/responses are terminal.
5. Configuration and CLI wiring.
   - Register `dns_txt` in `sfb/transport/__init__.py`.
   - Treat `dns_txt` like `dns` for CLI args and required `--domain`.
   - In `create_config`, set `dns_query_type="TXT"` and
     `dns_response_type="TXT"` when `transport == "dns_txt"`.
   - Update `sfb/config.py` validation to allow TXT types (gated by transport)
     and only enforce `dns_cname_label` rules when response type is CNAME.
6. MTU limits and documentation.
   - Add a `dns_txt` branch in `sfb/transport/mtu_limits.py` pointing to TXT
     MTU helpers in the new codec.
   - Update `doc/architecture/DNS_TRANSPORT.md` with a `dns_txt` section
     covering TXT encoding, payload caps, and tradeoffs vs CNAME.
   - Update `doc/architecture/TRANSPORTS.md`, `doc/architecture/FLATTENER.md`,
     `doc/flatten_manifest.txt`, and `README.md` to list `dns_txt`.

## Testing
- Do not run tests.
