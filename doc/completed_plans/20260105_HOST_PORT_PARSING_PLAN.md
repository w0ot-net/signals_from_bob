# Host:Port Parsing Consolidation Plan

## Summary
Consolidate duplicated host:port parsing into a shared utility with consistent
validation and centralized error classification/mapping, then update all call
sites to use it without per-module string matching.

## Constraints and Non-Goals
- Keep Python 2.7/3 compatibility and use only the Python standard library.
- Support Windows and Linux.
- Preserve current IPv4-only behavior; do not add IPv6 parsing.
- Avoid behavioral changes outside of validation/parsing and error handling.

## Goals
- One shared parser for host:port with optional default port handling.
- Consistent validation (text type, empty host, port range) across modules.
- Centralize error classification so callers do not replicate mapping logic.
- Remove duplicate helper functions and inline parsing.
- minimize code duplication!

## Affected Components
- `sfb/utils.py` (new shared parser)
- `sfb/cli.py` (replace `_split_host_port`)
- `sfb/transport/proxy_helpers.py` (proxy host:port validation)
- `sfb/transport/udp_ephemeral/udp_ephemeral_config.py` (parse_host_port)
- `sfb/transport/tls_handshake/tls_handshake_config.py` (parse_host_port)
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py` (parse_host_port)
- `sfb/transport/dns/dns_client.py` (resolver parsing)
- `sfb/transport/dns/dns_server.py` (listen addr parsing)
- `sfb/modules/port_fwd/port_fwd_server.py` (local/remote spec parsing)
- `sfb/modules/nc_linux/nc_linux.py` (host:port spec parsing)
- Docs that mention IPv6 host:port in user-facing specs (scan and update)

## Plan
1. Add `sfb/utils.py` with a canonical `parse_host_port` function:
   - Accept `text_type` only; enforce ASCII and non-empty host.
   - Require `host:port` by default, with an optional `default_port` for
     call sites that allow a missing port.
   - Reject bracketed or multi-colon forms (IPv6 unsupported).
   - Raise a dedicated `HostPortError` (subclass of `ValueError`) with a
     stable `code` plus a concise default message.

2. Introduce a shared mapper in `sfb/utils.py` (for example,
   `parse_host_port_or_raise`) that converts `HostPortError` to a caller-
   supplied exception via a small `code -> (err_type, message)` mapping.
   This keeps all error classification in one place and avoids per-call-site
   string matching.

3. Replace duplicate helpers and inline parsing:
   - `sfb/cli.py` uses the shared parser with `default_port`.
   - `dns_client.py` and `dns_server.py` use the shared parser for resolver
     and listen addresses (default port 53).
   - Modules/transports use the shared mapper with their local `code` map.
   - Remove or inline-replace `_parse_host_port` functions in module/transport
     files after updating call sites.

4. Update docs that mention IPv6 host:port syntax to reflect IPv4-only
   parsing behavior (for example, nc_linux and other host:port specs).

5. Add or adjust unit tests for the shared parser and error codes, plus any
   updated call sites. Do not run E2E tests in `tests/e2e/`.

## Validation
- `python3 -m unittest tests.test_nc_linux`
- Add a focused unit test for `sfb/utils.py` once implemented.

## Execution Notes
- Added `sfb/utils.py` with shared host:port parsing and error mapping helpers.
- Added `build_host_port_error_map` to remove duplicated per-module error maps.
- Replaced per-module host:port parsing in DNS, TLS, UDP, proxy, CLI, port_fwd, and nc_linux.
- Added `tests/test_utils.py` for shared parser coverage.
- Tests run:
  - `python3 -m unittest tests.test_utils`
  - `python3 -m unittest tests.test_nc_linux`
- Docs scan: no IPv6 host:port examples found outside completed plans.
