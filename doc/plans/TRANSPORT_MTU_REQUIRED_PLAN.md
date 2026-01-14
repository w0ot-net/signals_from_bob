# Transport MTU Required Plan

Status: draft

## Summary
Remove the protocol-level 100-byte initial MTU concept and require transports
to supply send/recv MTUs from the start. Update tunnel MTU negotiation to rely
only on transport-derived limits and explicit control-message fields.

## Goals
- Remove the protocol-level default MTU constant and config knob.
- Require explicit tx/rx payload MTUs in tun.mtu/tun.mtu_ok messages.
- Treat missing/invalid MTU fields as protocol violations (log + close).
- Keep asymmetric MTU negotiation and transport-specific MTU calculations.
- Align architecture and protocol docs with transport-derived MTUs.

## Non-Goals
- Retune MTU caps or add new transport features.
- Add compatibility shims for old MTU defaults.
- Add or run tests.

## Affected Components
- `sfb/protocol/constants.py`
- `sfb/protocol/__init__.py`
- `sfb/config.py`
- `sfb/tunnel/base_tunnel.py`
- `doc/architecture/ARCHITECTURE.md`
- `doc/architecture/PROTOCOL.md`
- `doc/architecture/CONTROL_MESSAGES.md`
- `doc/architecture/TRANSPORTS.md` (only if MTU summary text changes)
- `scripts/flatten.py`
- `sfb_flat.py`
- `sfb_flat.py.gz`

## Plan
1. Remove `DEFAULT_MTU` and `protocol_initial_packet_mtu` from protocol constants,
   config defaults, validation, and exports.
2. Tighten tunnel MTU negotiation to require `tx` and `rx` fields in `tun.mtu`
   and `tun.mtu_ok`. Missing/invalid fields are protocol violations (log + close)
   and must be handled consistently.
3. Remove `_default_packet_mtu` fallbacks in BaseTunnel so negotiated MTUs are
   computed strictly from transport-derived limits and peer-provided payload
   sizes. Add an explicit guard (TunnelError/assert) so MTU negotiation cannot
   run unless `_proposed_send_packet_mtu` and `_proposed_recv_packet_mtu` are set.
4. Sweep docs for references to the protocol-level initial MTU and update them
   to describe transport-derived initial MTUs only.
5. Regenerate `sfb_flat.py` and `sfb_flat.py.gz` after config changes.

## Compatibility Notes
- Breaking change: configs that set `protocol_initial_packet_mtu` will fail.
- Breaking change: peers that omit `tx`/`rx` in MTU control messages will be
  closed under the new rules.
