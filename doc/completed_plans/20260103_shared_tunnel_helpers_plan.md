# Shared Tunnel Helpers Plan

Status: completed.

## Goal

Reduce duplication between `AliceTunnel` and `BobTunnel` send paths by adding
small, shared helpers in `BaseTunnel`, without changing protocol behavior or
asymmetry.

## Affected Components

- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py

## Plan

1. Add a `BaseTunnel` helper that returns both encrypted body and packet bytes
   (e.g. `_encode_packet_for_send(packet, encrypted_body=None)`), using the
   outbound direction and current packet seq.
2. Add a `BaseTunnel` helper that builds the common `tunnel.packet_send` log
   fields (e.g. `_packet_send_fields(packet, data_len, context)`).
3. Update `AliceTunnel` send paths (`_send_new_packet`, `_send_retransmit`) to
   use the helpers, keeping pacing and retransmit decisions unchanged.
4. Update `BobTunnel` send paths (`_send_response`, `_send_ack_only_response`,
   `_send_retransmit_response`) to use the helpers, keeping keepalive
   suppression and window checks unchanged.
5. Verify log fields and encoded bytes match current behavior, only the
   construction path changes.

## Notes and Risks

- Helpers must not change when `SendWindow.send()` is called or when seq/ack
  values are captured.
- Keepalive suppression when pending data exists must stay intact.

## Testing

- No new tests planned; rely on existing coverage.

## Execution Notes

- Added shared packet encoding and packet_send logging helpers in BaseTunnel.
- Swapped Alice and Bob send paths to use the shared helpers without changing
  send timing or window behavior.
