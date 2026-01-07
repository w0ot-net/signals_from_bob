# MTU Handler and Poll Decision Cleanup Plan

## Goal
- Reduce duplication in MTU negotiation handlers without changing asymmetric
  MTU semantics.
- Centralize Alice poll/keepalive decision logic into a single, explicit
  decision output to make reasoning about state transitions easier.

## Non-Goals
- Modify MTU negotiation rules or packet formats.
- Change keepalive or retransmit behavior.
- Update or run tests.

## Decision
- Defer this refactor.
- Poll decision logic is already centralized and used in one call site;
  replacing the tuple with an object or enum adds complexity with little gain.
- The MTU handler duplication is small; a helper risks subtle semantic drift
  around default payload fallbacks and min-payload clamping.
- Revisit only if you are already changing MTU behavior or poll logic; in that
  case, consider only the MTU helper or keep the tuple return.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- doc/TUNNEL.md (if behavior description needs tightening)

## Plan
1) Add a private helper in BaseTunnel to parse and clamp MTU payloads:
   - Validate payload length and format.
   - Convert payload to MTU values.
   - Clamp to allowed min/max while preserving asymmetric semantics and the
     current min-payload rule (SEGMENT_HEADER_SIZE + 1).
   - Preserve the default-payload fallback used when tx/rx are missing.
   - Return a normalized result used by both _handle_mtu and _handle_mtu_ok.
2) Update _handle_mtu and _handle_mtu_ok to call the helper and reuse a
   shared logging path, eliminating duplicated validation and conversion.
3) Introduce a small poll decision object/enum in AliceTunnel:
   - Encapsulate the outcome (poll now, keepalive, delay) and a short reason.
   - Move the flag interpretation for _last_was_pong_only,
     _pong_grace_remaining, _got_data, and _has_pending_data_acks into a
     single decision function (replacing the existing tuple return).
4) Update the call site(s) to act on the decision object; preserve existing
   behavior and add the reason to the relevant log entry if helpful.
5) If needed, update doc/TUNNEL.md to align the description with the new
   decision structure (no behavioral changes).

## Testing
- Do not run tests here. The user will run E2E tests as needed.
