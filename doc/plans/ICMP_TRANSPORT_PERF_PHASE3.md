# ICMP Transport Perf Phase 3 - Socket Buffers and Logging Guidance

Status: draft

## Summary
Introduce optional ICMP socket buffer sizing to reduce drops under bursty
polling and add documentation guidance for production logging settings.

## Goals
- Allow ICMP socket buffer sizing via config to improve burst handling.
- Log effective socket buffer sizes for visibility.
- Document logging guidance for production throughput.

## Non-Goals
- Change ICMP protocol behavior or polling semantics.
- Add new dependencies or run automated tests.

## Affected Components
- `sfb/config.py`
- `sfb/transport/icmp/icmp_client.py`
- `sfb/transport/icmp/icmp_server.py`
- `doc/architecture/ICMP_TRANSPORT.md`

## Plan
1. Add ICMP socket buffer config fields.
   - Introduce `icmp_socket_rcvbuf` and `icmp_socket_sndbuf` in
     `sfb/config.py` with defaults that disable sizing (for example, 0 or
     None).
2. Apply socket buffer sizing in ICMP transports.
   - In `IcmpClient` and `IcmpServer`, if a config value is set, call
     `setsockopt(SOL_SOCKET, SO_RCVBUF/SO_SNDBUF, value)`.
   - Read back `getsockopt` to log the effective size (kernel may adjust it).
   - On error, log a warning and continue with defaults.
3. Update ICMP transport docs.
   - Document the new config fields and any Linux-specific behavior.
   - Add guidance to disable ICMP component logs or whitelist events for
     production throughput.

## Testing
- Do not run tests.
