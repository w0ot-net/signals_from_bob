# DNS TXT Codec Plan

Status: draft

## Summary

Create `sfb/transport/dns_txt/dns_txt_codec.py` to handle TXT-only DNS query and
response encoding/decoding for the new `dns_txt` transport. This plan is a
focused slice of `doc/plans/DNS_TXT_TRANSPORT_PLAN.md`.

## Goals

- Provide TXT-specific DNS wire helpers for the `dns_txt` transport.
- Keep Python 2/3 compatibility and ASCII-only source.
- Keep `dns_txt` self-contained without imports from `sfb.transport.dns`.

## Non-Goals

- Implement client/server transport logic.
- Modify existing `dns` transport modules.
- Add or run tests.

## Affected Components

- `sfb/transport/dns_txt/dns_txt_codec.py`
- `doc/plans/DNS_TXT_TRANSPORT_PLAN.md` (reference only)

## Plan

1. Define core constants and imports.
   - Define TXT/QCLASS/flag constants locally.
   - Avoid importing from `sfb.transport.dns`; duplicate minimal helpers as
     needed inside `dns_txt_codec.py`.
2. Query name helpers.
   - Implement `encode_query_name` and `decode_query_name` equivalents in
     `dns_txt_codec.py` to mirror the TXT transport naming and nonce behavior.
3. TXT RDATA helpers.
   - Implement `encode_txt_rdata` and `decode_txt_rdata` locally to handle
     base64 TXT strings and 255-byte chunking.
4. Packet helpers.
   - Build TXT query packets (header + question + optional OPT record).
   - Parse TXT responses (answer matching qname/rtype/QCLASS and decode
     TXT payload).
5. MTU helpers.
   - Implement `calc_query_mtu` and `calc_response_mtu` locally for TXT
     response sizing.

## Cross-References

- Parent plan: `doc/plans/DNS_TXT_TRANSPORT_PLAN.md`.
