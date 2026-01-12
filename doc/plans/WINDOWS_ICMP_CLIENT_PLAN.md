# Windows ICMP Client Plan

Status: draft

## Summary
Add a Windows-specific ICMP client implementation (client-only) using the
standard library. Keep the ICMP server Linux-only and select the client
implementation by OS at import time. This plan assumes the project is willing
to relax the current Linux-only constraint for the ICMP client.

## Goals
- Provide a Windows ICMP client compatible with Python 2.7 and Python 3.
- Keep the transport API and CLI/config usage unchanged for callers.
- Preserve current logging detail and MTU behavior.
- Keep code minimal, readable, and standard-library-only.

## Non-Goals
- Windows ICMP server support.
- IPv6 support.
- External dependencies or native extensions.
- Automated tests.

## Affected Components
- `sfb/transport/icmp/windows_icmp_client.py` (new)
- `sfb/transport/icmp/icmp_client.py` (shared logic extraction)
- `sfb/transport/icmp/__init__.py` (OS dispatch)
- `sfb/transport/icmp/icmp_packet.py` (if Windows receive parsing needs tweaks)
- `sfb/transport/__init__.py` (only if dispatch changes)
- `README.md` (document Windows requirements)

## Plan
1. Choose the Windows implementation strategy.
   - Prefer raw sockets (`AF_INET`, `SOCK_RAW`, `IPPROTO_ICMP`) to mirror the
     Linux client logic and avoid extra code paths.
   - If raw sockets cannot support required payload behavior, fall back to a
     `ctypes` wrapper around `IcmpSendEcho2` and document the tradeoffs.
2. Refactor shared client logic.
   - Extract OS-agnostic send/receive/pending logic into a small base class or
     helper module used by both Linux and Windows clients.
   - Use explicit loops (no comprehensions) to keep flat builds safe.
3. Implement `WindowsIcmpClient`.
   - Open the raw socket and treat `WSAEACCES` as a privilege error.
   - Use non-blocking sockets with `select` and existing `_WOULD_BLOCK` handling.
   - Parse replies with `parse_icmp_echo` (IPv4 header present on Windows).
   - Apply existing MTU limits and config values consistently.
4. Dispatch by OS.
   - In `sfb/transport/icmp/__init__.py`, map `IcmpClient` to
     `WindowsIcmpClient` when `os.name == 'nt'`, otherwise use the Linux client.
   - Keep `IcmpServer` Linux-only and fail fast on Windows.
5. Document Windows requirements.
   - Update `README.md` with admin privilege requirements and firewall notes.
   - Clarify that the server remains Linux-only.

## Testing
- Do not run tests.
