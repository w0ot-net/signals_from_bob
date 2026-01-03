# Plan: SOCKS Instrumentation

## Goals
- Add structured logging and lightweight counters across all SOCKS modules to
  make relay stalls, connect failures, and backpressure visible without
  changing behavior.
- Standardize event names and fields so logs are queryable across client/server.
- Keep overhead low by using lazy field builders and existing log filters.

## Constraints
- Python 2.7 and 3 compatibility; standard library only.
- Windows and Linux support; no ICMP-specific assumptions.
- Preserve asymmetry rules (doc/ASYMMETRY.md) and keepalive suppression.
- Do not run tests under tests/e2e/ (user runs those).
- Use python3 for any local test commands.

## Affected Components
- sfb/modules/socks/__init__.py
- sfb/modules/socks/data_pump.py
- sfb/modules/socks/relay_connection.py
- sfb/modules/socks/socks_control_messages.py
- sfb/modules/socks/socks_relay.py
- sfb/modules/socks/socks_server.py
- sfb/log_profiles.py (add/update SOCKS log profiles)
- doc/LOGGING.md (document new events/fields)
- tests/test_socks.py (instrumentation-focused unit coverage)

## Instrumentation Plan
1) Inventory and schema
   - Enumerate existing sock.* events and identify gaps per module.
   - Define a shared field schema (rid, ch, side, peer, direction, label,
     bytes, buffer sizes, durations, error codes) and consistent event names.
   - Add a tiny helper in sfb/modules/socks/ to build common field dicts and
     to format durations without extra allocations.

2) RelayConnection lifecycle
   - Add start/stop events for connection lifecycle with elapsed time and
     thread names.
   - Capture and log exit reasons from both pumps at stop time.
   - Record stop_event cause and whether shutdown was clean or error-driven.

3) SocksServerModule visibility
   - Instrument server start/stop with listen address/port and backlog.
   - Add per-client handshake timing: method negotiation, connect request
     parse, channel open wait, and connect response latency.
   - Log pending connect counts, rid allocation, and cleanup reasons.
   - Emit structured events for connect request/response (rid/ch/host/port).

4) SocksRelayModule visibility
   - Instrument connect handling: request validation, duplicate/pending
     detection, and channel lookup failures.
   - Log target connect latency, bound address, and error category mapping.
   - Emit a final relay lifecycle event with total duration and bytes moved.

5) Data pump stats coverage
   - Ensure both directions emit consistent pump stats fields at a fixed
     interval (bytes, buffer sizes, wait time, backoff, timeouts).
   - Add explicit events for socket timeout, channel EOF, and stop_event exit.
   - Gate any high-volume events behind DEBUG and keep the default blacklist.

6) Control message instrumentation
   - Add sock.* events for connect/connect_ok/err send and receive paths with
     sanitized fields (rid/ch/host/port, error code/reason).
   - Keep BaseModule module.send/module.recv for full message payloads.

7) Logging profiles and docs
   - Update sfb/log_profiles.py to include new sock.* events in SOCKS-focused
     profiles and add a dedicated socks_instrumentation profile if needed.
   - Document new event names and field schema in doc/LOGGING.md.

8) Tests
   - Add unit tests in tests/test_socks.py that patch log_event and assert key
     events fire for handshake, connect error, and relay stop paths.
   - Add targeted tests for pump stop reasons and timeout logging without
     relying on E2E tests.

9) Verification
   - Run python3 -m unittest tests.test_socks (avoid tests/e2e/).
   - Confirm no new logs appear when log_component_module_socks is disabled.

## Acceptance Criteria
- Every SOCKS module emits at least start/stop and error events with consistent
  rid/ch/side fields.
- Connect attempts log latency and result (ok/error) on both sides.
- Pump stats expose bytes, buffer sizes, and wait/timeout behavior with
  minimal overhead and clear stop reasons.
- Default behavior and wire protocol remain unchanged.

## Execution Notes
- Added SOCKS logging helpers plus standardized pump stats/stop events and
  explicit EOF/timeout/stop_event signals.
- Instrumented RelayConnection, SocksServerModule, and SocksRelayModule for
  lifecycle timing, control message send/recv, and target connect latency.
- Updated SOCKS log profiles and documented new event names/fields.
- Added unit tests in tests/test_socks.py and ran:
  `python3 -m unittest tests.test_socks`.
