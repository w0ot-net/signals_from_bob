# Bob Shutdown Signal Plan

## Goal
- When Bob shuts down intentionally, he sends a tunnel control message that
  instructs Alice to close and requires an acknowledgment.
- The notification respects the asymmetry rules: Bob only responds to polls,
  so the shutdown notice is delivered in a response packet.

## Non-Goals
- Guarantee delivery if Alice never polls again.
- Change transport asymmetry or channel close semantics.

## Affected Components
- doc/CONTROL_MESSAGES.md
- doc/PROTOCOL.md
- doc/TUNNEL.md
- doc/LOGGING.md
- sfb/tunnel/tunnel_control_messages.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/alice_tunnel.py (or base_tunnel handling)
- sfb/cli.py
- sfb/config.py (if a shutdown notify timeout is added)
- tests/test_tunnel.py

## Plan
1. Define a new tunnel control message.
   - Add `tun_shutdown(reason=None)` and `tun_shutdown_ok()` to
     `sfb/tunnel/tunnel_control_messages.py`.
   - Document `{"t":"tun","c":"shutdown","reason":"<text>"}` in
     doc/CONTROL_MESSAGES.md and doc/PROTOCOL.md.
   - Document the required acknowledgment:
     `{"t":"tun","c":"shutdown_ok"}`.
   - Clarify behavior in doc/TUNNEL.md: Bob sends on shutdown, Alice closes
     after sending shutdown_ok.

2. Add Bob-side shutdown signaling.
   - Add flags in `sfb/tunnel/bob_tunnel.py` to track shutdown requested/sent.
   - Implement `request_shutdown(reason=None)` to enqueue the control message
     and set a deadline for waiting on shutdown_ok.
   - In `_send_response`, ensure the shutdown message is queued before segment
     collection so it is included in the next response.
   - After sending the response that contains the shutdown notice, mark it as
     sent and wait briefly for shutdown_ok before closing.
   - Keep the tunnel state CONNECTED until the notice is sent to avoid the
     unexpected-state path in `handle_request`.

3. Handle shutdown on Alice.
   - Extend `BaseTunnel._handle_tunnel_message` to recognize `shutdown`.
   - Implement `_handle_shutdown` to log, send shutdown_ok, and then close.
   - Ensure the shutdown_ok is queued before closing and allow one more poll
     to flush it (bounded by a timeout).

4. Update CLI shutdown path.
   - In `sfb/cli.py`, replace direct `tunnel.close()` calls in Bob signal
     handlers with `tunnel.request_shutdown()`.
   - Ensure `run_server_passive` and `run_server_command` wait briefly for the
     shutdown notice to be sent before calling `tunnel.close()`, then exit.
   - Keep the forced double-signal exit behavior intact.

5. Logging and docs.
   - Add `tunnel.shutdown_request` (Bob) and `tunnel.shutdown_recv` (Alice)
     log events with side/reason fields.
   - Add `tunnel.shutdown_ack` (Bob) and `tunnel.shutdown_ok_send` (Alice)
     for the acknowledgment flow.
   - Document the new events in doc/LOGGING.md.

6. Tests (no E2E runs).
   - Add unit coverage in `tests/test_tunnel.py`:
     - Receiving `tun.shutdown` closes the tunnel.
     - Alice queues shutdown_ok before close.
     - Bob queues a shutdown message and closes after shutdown_ok or timeout.
   - Avoid tests in tests/e2e; those are user-run only.
