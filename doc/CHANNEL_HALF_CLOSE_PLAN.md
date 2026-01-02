# Plan: Channel Half-Close Control Messages

Status: draft. This plan adds protocol-level half-close support because the
SOCKS module half-close change did not resolve the SSH disconnects described in
doc/bugs/ssh_disconnects_socks_proxy.md.

## Goals
- Add a channel-level half-close control message to represent a sender
  finishing its send stream without closing the receive side.
- Update channel state to track send and receive closure independently while
  preserving explicit full-close semantics.
- Replace SOCKS-specific half-close with channel-level behavior.
- Keep Python 2.7/3 compatibility, stdlib-only, Windows/Linux support, and the
  existing asymmetry and keepalive rules.

## Non-Goals
- No changes to transport retransmit, polling rate fairness, or DNS error
  handling.
- No compatibility shims for mixed versions; both sides must upgrade together.
- No e2e test runs (user will run tests/e2e/).

## Affected Components
- sfb/channel/channel_control_messages.py
- sfb/channel/channel.py
- sfb/channel/channel_manager.py
- sfb/modules/socks/socks_control_messages.py (remove sock_half_close)
- sfb/modules/socks/socks_relay.py
- sfb/modules/socks/socks_server.py
- sfb/modules/socks/data_pump.py
- sfb/modules/file_transfer/file_transfer.py (audit close semantics)
- doc/CONTROL_MESSAGES.md
- doc/CHANNEL.md
- doc/CHANNEL_MANAGER.md
- doc/PROTOCOL.md
- doc/SOCKS.md
- doc/TUNNEL.md
- tests (new channel and manager coverage)

## Protocol Changes
- Add control message: {"t":"ch","c":"half_close","ch":<id>}.
- Semantics: sender will not send more data on this channel. Receiver treats
  this as remote send closed and returns EOF (b'') once its recv buffer drains.
  Receiver may continue sending until it half-closes or fully closes.
- Disallow half_close on channel 0 (control channel); log and close the tunnel
  or ignore (decision required).
- Full close remains ch_close/ch_close_ok; abort uses ch_close_err.
- Wire compatibility: half_close is required for correctness, so mixed versions
  are unsupported.

## Channel State Changes
- Track send_closed and recv_closed flags (or equivalent half-close states).
- Add Channel.close_write() (or Channel.half_close()) to send half-close:
  - Marks send closed.
  - If send buffer is empty, emit ch_half_close immediately.
  - If send buffer has data, mark pending and emit once the buffer drains.
- Channel.write() raises ChannelError('send_closed', ...) if send closed.
- Channel.read() returns b'' when recv closed and buffer is empty.
- Channel.close() remains a full close that sends ch_close regardless of half
  close state.
- When both halves are closed and the send buffer is drained, transition to
  closed and unregister the channel.

## Channel Manager Changes
- Add ch_half_close handling:
  - Mark recv closed on the channel.
  - If both halves are closed, finalize close and unregister the channel.
- Add a callback path for local half-close sends.
- Maintain existing close/abort control paths.
- Add log events: channel.half_close_out, channel.half_close_in,
  channel.half_close_auto_close.

## Module Changes
- SOCKS:
  - Remove sock_half_close control messages and handlers.
  - On socket EOF, call channel.close_write() and keep the reverse pump active.
  - When channel signals remote half-close, shut down socket write after
    outbound drain.
- File transfer:
  - Audit whether any flows should use close_write (for example, sender done
    sending but still expects a status message). Keep full close if not needed.
- Audit any other modules for assumptions around channel.close() vs EOF.

## Docs Updates
- doc/CONTROL_MESSAGES.md: document ch_half_close.
- doc/PROTOCOL.md: add half-close semantics and constraints.
- doc/CHANNEL.md and doc/CHANNEL_MANAGER.md: update state machine and read/write
  behavior.
- doc/SOCKS.md and doc/TUNNEL.md: note protocol-level half-close and module
  usage.

## Tests to Add or Update
- Channel tests:
  - close_write triggers EOF on read after buffer drains.
  - write after close_write raises send_closed.
  - auto-close when both halves are closed.
- Channel manager tests:
  - ch_half_close marks recv closed and does not unregister early.
  - full close still works after half-close.
- SOCKS tests (non-e2e) for half-close paths if existing harness allows.

## Verification
- rg -n "sock_half_close" -S . to ensure removal after migration.
- Run relevant unit tests with python3 (skip tests/e2e/).
- Reproduce SSH scenario from doc/bugs/ssh_disconnects_socks_proxy.md and
  compare logs for half-close and channel close reasons.
- If SSH still disconnects, open a separate plan for fairness/backpressure
  investigation.

## Implementation Order
1. Define ch_half_close in channel control messages.
2. Add send/recv closed flags and close_write to Channel.
3. Update ChannelManager to emit and handle ch_half_close.
4. Update SOCKS to use channel-level half-close and remove sock_half_close.
5. Audit other modules for close semantics.
6. Update docs.
7. Add tests and run non-e2e checks with python3.
8. Commit and push.
