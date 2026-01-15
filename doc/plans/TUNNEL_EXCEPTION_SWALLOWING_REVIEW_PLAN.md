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

- BaseTunnel._bg_run: logs background loop error, continues (base_tunnel.py).
- AliceTunnel._run_loop: logs tick error, continues (alice_tunnel.py).
- BobTunnel.serve_forever: logs serve loop error, continues (bob_tunnel.py).
- BobTunnel._run_loop: logs background serve error, continues (bob_tunnel.py).

### Handshake retry behavior

- AliceTunnel.connect: logs handshake error, backs off, retries (alice_tunnel.py).
- AliceTunnel._complete_handshake: logs ACK send failed, resets state, raises (alice_tunnel.py).

### Send-path error handling

- AliceTunnel._send_new_packet: logs send failure, releases permit, returns (alice_tunnel.py).
- AliceTunnel._send_retransmit: logs send failure, releases permit, returns False (alice_tunnel.py).
- BobTunnel._respond: logs responder send failure, raises (bob_tunnel.py).

### Decode/control dispatch handling

- BaseTunnel._decode_packet: logs decode failure, returns None (base_tunnel.py).
- BaseTunnel._dispatch_control_message: module handler errors logged, message dropped (base_tunnel.py).
- BobTunnel._dispatch_control_message: unknown message types logged, dropped (bob_tunnel.py).

### Module loader handling

- ModuleLoader._handle_load: logs load failure, sends mod_load_err (module_loader.py).
- ModuleLoader._handle_unload: logs unload failure, sends mod_unload_err (module_loader.py).
- ModuleLoader.shutdown: logs shutdown errors, continues (module_loader.py).
- ModuleLoader.shutdown: unregister_module errors ignored (module_loader.py).

### Telemetry/logging guardrails

- BaseTunnel._reliability_snapshot: stats snapshot errors ignored (base_tunnel.py).
- BaseTunnel._reliability_snapshot: RTT snapshot errors ignored (base_tunnel.py).
- BaseTunnel._decode_packet: bytes length extraction errors ignored (base_tunnel.py).
- AliceTunnel._log_transport_blocked: pending_count errors ignored (alice_tunnel.py).
- AliceTunnel._poll_pacing_cap: transport max_in_flight errors ignored (alice_tunnel.py).
- AliceTunnel._maybe_log_poll_pace: pending_count errors ignored (alice_tunnel.py).
- AliceTunnel._maybe_log_pacer_summary: stats snapshot errors ignored (alice_tunnel.py).
- BobTunnel._respond: bytes length extraction errors ignored (bob_tunnel.py).

### Cleanup handling

- AliceTunnel.close: transport close errors ignored (alice_tunnel.py).
- BobTunnel.close: transport close errors ignored (bob_tunnel.py).

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
