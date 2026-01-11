# Relay Clean Shutdown Plan

## Summary
Treat common peer disconnects as expected so relay channels shut down cleanly
on both sides without being flagged as fatal.

## Goals
- Map expected socket close/reset errors to clean shutdown outcomes.
- Propagate socket closure to the tunnel channel so the opposite pump exits
  cleanly.
- Preserve clear log reasons that distinguish peer closes from real errors.
- Keep Python 2/3 compatibility and ASCII-only code/scripts.

## Non-Goals
- Changes to tunnel protocol behavior or transport implementations.
- Adding non-stdlib dependencies.
- Tests or e2e validation.

## Affected Components
- sfb/modules/relay_pump.py
- sfb/modules/relay_connection.py
- sfb/modules/socks/socks_server.py
- sfb/modules/socks/socks_relay.py
- sfb/modules/port_fwd/port_fwd_server.py
- sfb/modules/port_fwd/port_fwd_relay.py

## Design Notes
- Treat the following socket errors as expected peer shutdowns:
  EPIPE, ECONNRESET, ECONNABORTED, ENOTCONN, ESHUTDOWN, ETIMEDOUT, plus
  Windows WSA* equivalents.
- When a socket->channel pump hits an expected close, call the EOF callback
  (close_write) and exit with a non-fatal reason like socket_eof or peer_reset.
- When a channel->socket pump hits an expected close, close or half-close the
  channel so the socket->channel pump exits cleanly.
- Keep stop_cause as eof for expected closes so clean_shutdown remains true.
- Add a small log field (for example close_reason) to clarify why a relay
  stopped without changing event names.

## Implementation Steps
1. Add a helper in relay_pump to classify expected socket-close error codes,
   including Windows errno values via getattr.
2. Update socket recv/send/select error handling:
   - If the error is an expected close, avoid fatal_error and record a
     peer-close exit_reason, then trigger the EOF/close path.
   - Otherwise keep current fatal_error behavior.
3. Ensure RelayConnection summary fields reflect the expected-close stop
   reason and keep clean_shutdown true.
4. Update relay stop logging to include the close_reason field for debugging.

## Validation
- Do not run tests here.

## Execution Notes
- Classified expected socket-close errors and treated them as clean shutdowns
  with channel close propagation.
- Relay summaries now include close_reason and treat peer-close reasons as eof.
- SOCKS server now uses clean channel half-close on client disconnects.
