# ICMP Transport Performance Plan

Status: draft

## Summary
Target low-risk hot-path improvements in the ICMP client and server while
preserving current protocol behavior. Note that Bob's ICMP throughput is
fundamentally bounded by Alice's poll rate per doc/architecture/ASYMMETRY.md,
so server-side tweaks are incremental.

## Goals
- Reduce syscall and allocation overhead in ICMP client and server hot paths.
- Reuse receive buffers where safe on Python 3 without breaking Python 2.
- Preserve ICMP transport semantics, logging, and error handling.

## Non-Goals
- Change asymmetry behavior, retransmit strategy, or polling semantics.
- Add new dependencies or run automated tests.
- Modify non-ICMP transports.

## Affected Components
- `sfb/transport/icmp/icmp_client.py`
- `sfb/transport/icmp/icmp_server.py`
- `sfb/transport/icmp/icmp_packet.py` (if buffer-view parsing is needed)
- `sfb/compat.py` (only if a new helper is required)
- `sfb/config.py` (only if ICMP socket buffer sizing becomes configurable)
- `doc/architecture/ICMP_TRANSPORT.md` (if config or behavior notes change)

## Helper Notes
- Bob throughput is bounded by Alice polling; improvements should not assume
  server-side receive throughput can exceed the poll rate.
- Avoid list/dict/set comprehensions in `sfb/` for Python 2 flat builds.
- Per-packet logging is expensive; keep ICMP logging disabled or whitelist
  events in production runs.

## Plan
1. Review the current ICMP client/server hot paths and confirm all changes
   stay within the asymmetry rules in doc/architecture/ASYMMETRY.md.
2. ICMP client: reduce per-call allocations and syscalls.
   - Cache `self._sock_list = [self._sock]` for select.
   - Cache `self._target_addr = (self._target_ip, 0)` for sendto.
   - For `timeout == 0`, try a direct non-blocking recv and treat
     EAGAIN/EWOULDBLOCK as "no data" to skip select.
   - After select readiness, drain multiple packets until EAGAIN/EWOULDBLOCK,
     returning the first valid response.
3. ICMP client: optional recv buffer reuse on Python 3.
   - Allocate a persistent bytearray receive buffer.
   - Use recvfrom_into and pass a bytes-like view to parse.
   - Fall back to recvfrom on Python 2 to avoid buffer handling quirks.
4. ICMP server: drain after select.
   - Replace the single recvfrom with a loop that drains until
     EAGAIN/EWOULDBLOCK, skipping malformed/oversize packets.
   - Return the first valid request to the caller.
5. ICMP server: optional recv buffer reuse on Python 3.
   - Allocate a persistent bytearray receive buffer.
   - Use recvfrom_into and pass a bytes-like view to parse.
   - Fall back to recvfrom on Python 2.
6. ICMP server: increase socket buffer sizes to reduce drops under bursts.
   - Decide whether to add config keys (for example,
     `icmp_socket_rcvbuf`/`icmp_socket_sndbuf`) or use a fixed default.
   - Apply SO_RCVBUF/SO_SNDBUF after socket creation, handle errors
     gracefully, and log the effective sizes.
   - Update `sfb/config.py` and `doc/architecture/ICMP_TRANSPORT.md` if new
     config is introduced.
7. Document logging guidance.
   - Add a short note in `doc/architecture/ICMP_TRANSPORT.md` about disabling
     ICMP component logs or whitelisting events for production throughput.

## Testing
- Do not run tests.
