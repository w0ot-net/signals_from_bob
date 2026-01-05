# TLS Handshake Bump Performance Plan

Status: draft

## Summary

Reduce CPU and memory overhead in the TLS handshake bump transport by making
response extraction incremental, trimming redundant parsing/copies, and
improving socket readiness scaling while preserving wire format and Python
2.7/3 compatibility.

## Goals

- Avoid repeated full-buffer regex scans in response parsing.
- Reduce worst-case work in scan mode when large base32-like runs appear.
- Remove redundant record parsing/copying in the server hot path.
- Avoid repeated proxy header scans on incremental CONNECT responses.
- Improve readiness handling scalability while keeping Windows/Linux support.

## Non-Goals

- Change the TLS bump wire format or handshake semantics.
- Add non-standard-library dependencies.
- Rework the transport into a threaded or async architecture.
- Run E2E tests (user will run those).

## Affected Components

- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_server.py
- sfb/transport/proxy_helpers.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- doc/completed_plans/TLS_HANDSHAKE_BUMP_TRANSPORT.md
- doc/TRANSPORTS.md
- tests/test_tls_handshake_bump_codec.py
- tests/test_tls_handshake_bump_client_server.py
- tests/test_tls_proxy_helpers.py

## Plan

1. Incremental regex response scanning (client)
   - Add a regex scan offset to the pending connection state.
   - Introduce a bounded lookback for regex mode:
     - New config value (for example `tls_bump_response_regex_lookback`) or a
       computed default based on `cn_max_len`.
     - Document that regex matches must be found within the configured
       lookback window (otherwise use scan mode).
   - In regex mode, search from `scan_offset` and update the offset after each
     unsuccessful read to the tail lookback window.
   - Preserve current behavior for successful matches and error logging.

2. Faster scan mode token detection (codec)
   - Add a fast path for decoding the fixed-size response header:
     - Precompute a base32 character lookup table.
     - Decode the 8-character header window directly without calling the full
       base32 decoder.
   - Replace the nested decode loop with a sliding window:
     - For each base32 run, step one character at a time and decode only the
       header window.
     - When the header yields a plausible payload length, compute the expected
       encoded length and only then decode the full token.
   - Keep the existing bounds (`max_payload_len`, `max_token_len`) and return
     semantics identical to the current scanner.

3. Server-side ClientHello parse fast path
   - Add a codec helper that parses the ClientHello body directly from an
     existing buffer plus the known record length.
   - Avoid calling `parse_record_header` a second time and avoid converting the
     full record into a new bytes object.
   - Update the server read path to use the new helper once the record is
     fully buffered.

4. Incremental proxy CONNECT parsing (client)
   - Track a scan offset for the CONNECT response buffer.
   - Update `parse_connect_response` (or add a new helper) to accept an
     optional `start_offset` and use `buffer.find(..., start_offset)`.
   - Update the client to only rescan a small lookback window (3 bytes is
     enough to catch the `\r\n\r\n` boundary).

5. Scalable readiness polling (client/server)
   - Introduce a small selector abstraction:
     - Use `select.poll` on platforms that support it.
     - Fall back to `select.select` on Windows and when poll is unavailable.
   - Keep the existing state machine and timeout calculation semantics.
   - Add a guard for select/poll limits and document expected scaling.

6. Docs and config updates
   - Document regex lookback behavior and any new config field.
   - Note the new scan/token parsing behavior in the TLS bump transport docs.
   - Update any transport overview docs that list config knobs.

## Validation

- Run targeted unit tests with `python3 -m unittest`:
  - tests.test_tls_handshake_bump_codec
  - tests.test_tls_handshake_bump_client_server
  - tests.test_tls_proxy_helpers
- Do not run `tests/e2e/`.
