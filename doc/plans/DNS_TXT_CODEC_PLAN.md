# DNS TXT Codec Plan

Status: draft

## Summary

Create `sfb/transport/dns_txt/dns_txt_codec.py` to handle TXT-only DNS query and
response encoding/decoding for the new `dns_txt` transport. This plan is a
focused slice of `doc/plans/DNS_TXT_TRANSPORT_PLAN.md`.

## Goals

- Provide TXT-specific DNS wire helpers for the `dns_txt` transport.
- Keep Python 2/3 compatibility and ASCII-only source.
- Reuse existing DNS helpers where possible to minimize duplication.

## Non-Goals

- Implement client/server transport logic.
- Modify existing `dns` transport modules.
- Add or run tests.

## Affected Components

- `sfb/transport/dns_txt/dns_txt_codec.py`
- `doc/plans/DNS_TXT_TRANSPORT_PLAN.md` (reference only)

## Plan

1. Define core constants and imports.
   - Import shared DNS constants/flags and name helpers from
     `sfb.transport.dns.dns_codec`.
   - Keep minimal local constants only if needed for clarity.
2. Query name helpers.
   - Wrap `encode_query_name` and `decode_query_name` to mirror the TXT
     transport naming and nonce behavior.
3. TXT RDATA helpers.
   - Reuse `encode_txt_rdata` and `decode_txt_rdata` from `dns_codec`.
4. Packet helpers.
   - Build TXT query packets (header + question + optional OPT record).
   - Parse TXT responses (answer matching qname/rtype/QCLASS and decode
     TXT payload).
5. MTU helpers.
   - Expose thin wrappers for `calc_query_mtu` and `calc_response_mtu` for
     TXT response sizing.

## Cross-References

- Parent plan: `doc/plans/DNS_TXT_TRANSPORT_PLAN.md`.
