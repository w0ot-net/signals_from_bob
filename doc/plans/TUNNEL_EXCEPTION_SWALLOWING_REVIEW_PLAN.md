# Tunnel Exception Swallowing Review Plan

Status: draft

## Goal

Inventory and review every swallowed exception path in tunnel-related code,
then decide whether each should be fatal (preferred) or handled locally.
The default posture is to crash when a condition is not clearly safe to
continue.

## Affected Components

- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/module_loader.py

## Inventory

### Loop and background exception handling

- BaseTunnel._bg_run: logs background loop error, continues (sfb/tunnel/base_tunnel.py:1558).
- AliceTunnel._run_loop: logs tick error, continues (sfb/tunnel/alice_tunnel.py:1875).
- BobTunnel.serve_forever: logs serve loop error, continues (sfb/tunnel/bob_tunnel.py:172).
- BobTunnel._run_loop: logs background serve error, continues (sfb/tunnel/bob_tunnel.py:210).

### Handshake retry behavior

- AliceTunnel.connect: logs handshake error, backs off, retries (sfb/tunnel/alice_tunnel.py:269).
- AliceTunnel._complete_handshake: logs ACK send failed, resets state, raises (sfb/tunnel/alice_tunnel.py:369).

### Send-path error handling

- AliceTunnel._send_new_packet: logs send failure, releases permit, returns (sfb/tunnel/alice_tunnel.py:1515).
- AliceTunnel._send_retransmit: logs send failure, releases permit, returns False (sfb/tunnel/alice_tunnel.py:1605).
- BobTunnel._respond: logs responder send failure, raises (sfb/tunnel/bob_tunnel.py:826).

### Decode/control dispatch handling

- BaseTunnel._decode_packet: logs decode failure, returns None (sfb/tunnel/base_tunnel.py:687).
- BaseTunnel._dispatch_control_message: module handler errors logged, message dropped (sfb/tunnel/base_tunnel.py:1065).
- BobTunnel._dispatch_control_message: unknown message types logged, dropped (sfb/tunnel/bob_tunnel.py:946).

### Module loader handling

- ModuleLoader._handle_load: logs load failure, sends mod_load_err (sfb/tunnel/module_loader.py:156).
- ModuleLoader._handle_unload: logs unload failure, sends mod_unload_err (sfb/tunnel/module_loader.py:199).
- ModuleLoader.shutdown: logs shutdown errors, continues (sfb/tunnel/module_loader.py:479).
- ModuleLoader.shutdown: unregister_module errors ignored (sfb/tunnel/module_loader.py:487).

### Telemetry/logging guardrails

- BaseTunnel._reliability_snapshot: stats snapshot errors ignored (sfb/tunnel/base_tunnel.py:426).
- BaseTunnel._reliability_snapshot: RTT snapshot errors ignored (sfb/tunnel/base_tunnel.py:433).
- BaseTunnel._decode_packet: bytes length extraction errors ignored (sfb/tunnel/base_tunnel.py:679).
- AliceTunnel._log_transport_blocked: pending_count errors ignored (sfb/tunnel/alice_tunnel.py:1199).
- AliceTunnel._poll_pacing_cap: transport max_in_flight errors ignored (sfb/tunnel/alice_tunnel.py:1236).
- AliceTunnel._maybe_log_poll_pace: pending_count errors ignored (sfb/tunnel/alice_tunnel.py:1276).
- AliceTunnel._maybe_log_pacer_summary: stats snapshot errors ignored (sfb/tunnel/alice_tunnel.py:1447).
- BobTunnel._respond: bytes length extraction errors ignored (sfb/tunnel/bob_tunnel.py:810).

### Cleanup handling

- AliceTunnel.close: transport close errors ignored (sfb/tunnel/alice_tunnel.py:1889).
- BobTunnel.close: transport close errors ignored (sfb/tunnel/bob_tunnel.py:960).

## Review Criteria

For each instance above, decide:

- Fatal: Should immediately close the tunnel and exit non-zero.
- Non-fatal: Safe to continue; document why and ensure logging is adequate.

Default bias: fatal unless continued operation is clearly correct and safe.

## Execution Notes

- Identify which exceptions represent programming errors vs expected runtime
  conditions.
- Track any changes that turn non-fatal paths into fatal ones.
- Update CLI exit behavior as needed to surface fatal errors to users.
