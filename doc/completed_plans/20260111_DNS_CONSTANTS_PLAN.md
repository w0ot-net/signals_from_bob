# DNS Constants Plan

Status: completed

## Summary
Introduce shared DNS offset constants in a tiny module and update DNS client
and server to use them for parsing/building offsets.

## Goals
- Add `dns_constants.py` with shared DNS header/question offset constants.
- Update `dns_client.py` to use the shared constants in `_parse_response`.
- Update `dns_server.py` to use the shared constants in `_parse_query` and
  response building where offsets are referenced.

## Non-Goals
- Change parsing logic, validation, or error handling.
- Add new dependencies or change performance characteristics.
- Update or run tests.

## Affected Components
- `sfb/transport/dns/dns_constants.py`
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/dns/dns_server.py`

## Plan
1) Create `sfb/transport/dns/dns_constants.py` with ASCII-only constants such
   as DNS header length, question footer length, and fixed RR header length.
2) Replace hard-coded offsets in `dns_client.py` `_parse_response` with the
   shared constants.
3) Replace hard-coded offsets in `dns_server.py` `_parse_query` and response
   building with the shared constants.
4) Ensure imports are local and do not introduce new dependencies.

## Testing
- Do not run tests.

## Execution Notes
- 2026-01-11: Added shared DNS offset constants and updated DNS client/server
  to use them.
