# Proxy CONNECT HTTP Response Plan

Status: abandoned

## Goal
- Replace manual status parsing in `parse_connect_response` with standard library
  HTTP response parsing while keeping the current return contract, buffer scan
  behavior, and header size limit.

## Non-Goals
- Change proxy handshake flow, timeouts, or state machine behavior.
- Expand supported proxy formats or add IPv6 parsing.
- Adjust header size limits or response acceptance beyond the current rules.

## Affected Components
- sfb/transport/proxy_helpers.py
- tests/test_tls_proxy_helpers.py

## Plan
1. Add a version-safe import for HTTP response parsing.
   - Use `http.client.HTTPResponse` on Python 3 and `httplib.HTTPResponse` on
     Python 2.
   - Add a small socket wrapper that exposes `makefile()` backed by `io.BytesIO`
     so `HTTPResponse` can parse from in-memory header bytes.

2. Update `parse_connect_response` to use `HTTPResponse`.
   - Keep the existing `\r\n\r\n` scan with `start_offset` to detect completeness.
   - Preserve `PROXY_HEADER_LIMIT` handling before attempting to parse.
   - Feed only the header bytes to `HTTPResponse`, call `begin()`, and use the
     parsed `.status` as the return status.
   - Treat parsing failures as `status=None` while still returning `header_end`.

3. Remove `_parse_status_line` and any now-unused logic.

4. Update unit tests to cover parsing outcomes.
   - Confirm valid responses still return status 200 and unchanged `header_end`.
   - Confirm malformed status lines return `status=None` with `header_end`.
   - Keep coverage for `start_offset` and `PROXY_HEADER_LIMIT` behavior.
   - Add a regression case for odd-but-valid spacing to ensure stdlib parsing
     stays tolerant.

5. Verify behavior remains compatible across Python 2.7 and 3.

## Abandonment notes
- 2026-01-05: Abandoned per request; no implementation work recorded.
