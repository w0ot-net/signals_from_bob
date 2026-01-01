# Keepalive Flag Plan

## Goal

Replace keepalive ping/pong control messages on channel 0 with a packet header
flag that marks "pure keepalive" packets. Keepalive packets will carry no
segments and allow receivers to skip control-message parsing entirely.

This change is intentionally wire-incompatible with older clients.

## Protocol Changes

- Add a new packet header flag bit: `FLAG_KEEPALIVE` (name TBD but consistent
  with existing `FLAG_SYN`/`FLAG_ACK`).
- Update the header validator to accept the new bit.
- Define usage constraints:
  - `FLAG_KEEPALIVE` is only valid after the tunnel reaches CONNECTED state.
  - `FLAG_KEEPALIVE` MUST NOT be combined with `FLAG_SYN` or `FLAG_ACK`.
  - Any keepalive flag seen before CONNECTED or mixed with SYN/ACK is a fatal
    protocol error: log, drop the packet, and close the tunnel.
- Define a strict invariant:
  - If `FLAG_KEEPALIVE` is set, the packet MUST contain zero segments.
  - If any segments are present, `FLAG_KEEPALIVE` MUST be unset.
  - Treat any violation as a fatal protocol error: log, drop the packet, and
    close the tunnel.
- Semantics:
  - Alice sending a keepalive poll uses `FLAG_KEEPALIVE`.
  - Bob responding with a keepalive uses `FLAG_KEEPALIVE`.
  - Ping vs pong is inferred from role (Alice initiates, Bob responds).

## Sender Changes

### Alice

- When keepalive is due and there is no pending data, build a packet with:
  - `FLAG_KEEPALIVE` set
  - No segments
  - Normal seq/ack/sack
- Do not enqueue a `{"t":"tun","c":"ping"}` control message.
- "No pending data" means no queued send data in any channel (including
  control messages other than keepalive), not just "no segments after packing."

### Bob

- When responding and there is no queued data to send, build a packet with:
  - `FLAG_KEEPALIVE` set
  - No segments
  - Normal seq/ack/sack
- Do not enqueue a `{"t":"tun","c":"pong"}` control message.
- If a response is required but the send window is full or the send-window
  distance cap is exceeded, send an empty ACK-only packet with no keepalive
  flag (since it is not recorded in the send window).
- If data is queued but cannot fit the payload cap, do not send keepalive;
  respond with an empty ACK-only packet.

## Receiver Changes

- Keep ACK/SACK processing and recv_window ordering even for keepalive-only
  packets; do not return early before reliability bookkeeping runs.
- If `FLAG_KEEPALIVE` is set and there are zero segments:
  - Skip channel delivery and control-message parsing.
  - Treat as "no real data" for pacing/poll decisions.
- If `FLAG_KEEPALIVE` is set but segments are present:
  - Log a fatal protocol violation, drop the packet, and close the tunnel.
  - Document this as a hard protocol violation in `doc/PROTOCOL.md`.

## Reliability and Retransmit

Keepalive packets still consume sequence numbers and must be tracked by the
send window so ACK/SACK processing remains consistent.

Plan:
- Extend `SendWindow.send()` to accept packet flags and store them per
  unacked packet.
- Update `_rebuild_packet()` to preserve the stored flags on retransmit.
- Keep the existing behavior where keepalive packets can be retransmitted and
  they MUST retain `FLAG_KEEPALIVE` on retransmit.
- Only emit `FLAG_KEEPALIVE` when the packet will be recorded in the send
  window (no keepalive flag on untracked ACK-only responses).

## Logging and Metrics

- Include the keepalive flag in `tunnel.packet_send` and `tunnel.packet_recv`
  log fields (if not already exposed).
- If control-message logging depends on parsing channel 0, it will no longer
  log ping/pong; that is expected.

## Docs to Update

- `doc/PROTOCOL.md`: define the new flag and the keepalive-only packet format,
  including the hard drop rule for violations.
- `doc/TUNNEL.md`: update the Keepalive section (header-only, no channel 0).
- `doc/CONTROL_MESSAGES.md`: remove ping/pong from tunnel control messages or
  mark as deprecated and unused.
- `doc/RELIABILITY.md`: keepalive is a header flag, not a control message.
- `doc/ASYMMETRY.md`: note that ping/pong is inferred by role, not message.
- `doc/CHANNEL_MANAGER.md`: remove ping/pong references from channel 0.
- `doc/ARCHITECTURE.md`: update keepalive/polling descriptions.
- `doc/DNS_TRANSPORT.md`: replace ping/pong mention with keepalive flag.

## Tests to Update or Add

- Packet header encode/decode accepts the new flag.
- Keepalive-only packet has zero segments and is accepted.
- A keepalive flag with segments is a fatal protocol error (drop + close).
- A keepalive flag seen before CONNECTED or mixed with SYN/ACK is fatal.
- Alice: `has_real_data` is false for keepalive-flag responses without JSON
  parsing.
- Bob: when idle, response uses keepalive flag and no control segment.
- Remove or rewrite tests that assert `{"t":"tun","c":"ping"}` / `"pong"` on
  the wire.

## Implementation Order

1. Add flag constant, PacketHeader property, and validator update.
2. Enforce keepalive flag usage constraints (CONNECTED-only, no SYN/ACK mix).
3. Extend send-window metadata to preserve flags on retransmit.
4. Update Alice/Bob send paths to emit header-only keepalive packets.
5. Update receive path to short-circuit keepalive-flag packets without
   skipping reliability bookkeeping.
6. Update docs and tests.
