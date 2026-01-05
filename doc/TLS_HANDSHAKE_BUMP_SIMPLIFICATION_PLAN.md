# TLS Handshake Bump Simplification Plan

Status: draft

## Summary

Reduce duplication in the TLS bump client state handling and consolidate base
domain validation while preserving existing wire behavior, timeouts, and
platform compatibility.

## Goals

- Introduce a single explicit phase state for client connections to simplify
  select/timeout/read/write logic.
- Centralize base domain validation by using the codec helper.
- Preserve timing semantics, MTU limits, and error handling behavior.
- Maintain Python 2.7/3 compatibility and Windows/Linux support.

## Non-Goals

- Changing TLS bump wire format or payload framing.
- Altering config defaults or adding new configuration options.
- Reworking transport architecture (no threads/async).
- Running E2E tests (user-owned).

## Affected Components

- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py

## Plan

1. Add explicit client phases
   - Define phase constants (connect, proxy, handshake, request, response).
   - Extend `_PendingConn` with a `phase` field initialized at creation.
   - Add helpers for phase-specific deadlines and read/write interests.

2. Centralize client state driving
   - Replace `_handle_readable`/`_handle_writable` with a single driver that
     advances state based on phase and readiness flags.
   - Update `_select_timeout`, `_build_select_lists`, and `_prune_deadlines`
     to use the phase helper instead of repeated branching.
   - Preserve current timeout scheduling (connect, proxy, handshake, pending)
     and existing logging/error behavior.

3. Consolidate base domain validation
   - Replace `_validate_base_domain` with `codec.normalize_domain`.
   - Wrap `ValueError` as `TransportError` and, if needed, map error messages
     to current `tls_bump_base_domain ...` strings to avoid regressions.
   - Keep existing ASCII/non-empty checks if they are still needed to preserve
     error wording.

4. Update tests only if needed
   - If any tests depend on exact error strings or behavior, update them to
     reflect the consolidated validation path.

## Validation

- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`.
- Run `python3 -m unittest tests.test_tls_handshake_bump_codec`.
- Do not run tests in `tests/e2e/`.
