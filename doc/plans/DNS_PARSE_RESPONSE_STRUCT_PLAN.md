# DNS Parse Response Struct Plan

Status: draft

## Summary
Make `dns_client._parse_response` return a lightweight structured result so
call sites read more clearly without adding per-packet overhead.

## Goals
- Introduce a lightweight result container (namedtuple or __slots__ class).
- Update `_parse_response` to return the container on all non-malformed paths.
- Update `_try_recv` to use named fields with the same behavior and logging.

## Non-Goals
- Change parsing logic, validation, or error handling.
- Add new dependencies or change performance characteristics.
- Update or run tests.

## Affected Components
- `sfb/transport/dns/dns_client.py`

## Plan
1) Define a `ParseResult` container at module scope.
   - Prefer `collections.namedtuple` for zero dict overhead.
   - If needed, use a small class with `__slots__`.
2) Update `_parse_response` to return `ParseResult` values.
   - Keep returning `None` for malformed packets to preserve current checks.
   - Preserve the same field values for every existing return path.
3) Update `_try_recv` to read named fields instead of tuple indexes.
   - Keep all logging fields and error paths unchanged.
4) Update the `_parse_response` docstring to reflect the structured return.

## Testing
- Do not run tests.
