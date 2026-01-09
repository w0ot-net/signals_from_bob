# Wrapper Reduction Plan

Status: completed.

## Goal
- Replace trivial wrappers with renames, aliases, or shared helpers to reduce
  indirection without changing behavior or error messages.
- Keep Python 2.7 + 3 compatibility and standard-library-only constraints.

## Non-Goals
- Change public APIs or external behavior.
- Add new dependencies.
- Run tests here.

## Affected Components
- sfb/protocol/segment.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py
- sfb/modules/port_fwd/port_fwd_server.py
- sfb/modules/nc_linux/nc_linux.py
- sfb/modules/base_module.py
- sfb/transport/transport_base.py
- sfb/transport/dns/dns_client.py
- sfb/transport/memory/memory_client.py
- sfb/transport/icmp/icmp_client.py
- sfb/transport/udp_ephemeral/udp_ephemeral_client.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py

## Plan
1. Replace single-line wrappers with aliases or direct calls.
   - Replace `_coerce_bytes` in `sfb/protocol/segment.py` with an alias to
     `to_bytes` or inline `to_bytes` at call sites.
   - Replace `_base32_decode_bytes` in
     `sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py` with an
     alias to `shared_base32_decode_bytes`.
   - Replace `_try_decode_response_header` with a default-argument form of
     `_try_decode_response_header_at` (rename accordingly) to remove the
     wrapper.
2. Centralize module error factories.
   - Add a shared `invalid_spec` factory in `sfb/modules/base_module.py`.
   - Update `sfb/modules/port_fwd/port_fwd_server.py` and
     `sfb/modules/nc_linux/nc_linux.py` to use the shared factory instead of
     local wrappers.
3. Standardize pending-count behavior in transports.
   - Align client transports to use a common `_pending` attribute where
     feasible.
   - Add a default `pending_count` implementation in
     `sfb/transport/transport_base.py` that returns `len(self._pending)`.
   - Remove per-transport trivial `pending_count` methods that only return
     `len(...)`, keeping custom implementations (e.g., lossy transport)
     intact.
4. Verify behavior parity by inspection.
   - Confirm error messages and exception types remain unchanged.
   - Confirm method names and call sites are updated consistently.

## Testing
- Do not run tests here.

## Execution Notes
- Inlined `to_bytes` in `Segment` construction and aliased shared base32 decode
  helpers in the TLS bump codec.
- Collapsed TLS bump response header wrappers into a single default-argument
  helper and updated call sites.
- Centralized `invalid_spec` creation in `BaseModule`, updating port forward and
  nc_linux host:port error maps to use it.
- Added a default `Transport.pending_count` and removed trivial overrides from
  transports that already track `_pending`.
