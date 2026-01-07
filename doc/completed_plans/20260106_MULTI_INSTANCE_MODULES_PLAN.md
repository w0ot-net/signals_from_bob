# Multi-Instance Modules Plan

## Goal
- Allow multiple instances of any module to run concurrently within a single tunnel.
- Route module control messages to the correct instance deterministically.
- Expose an instance selector in the CLI so concurrent module runs are practical.

## Non-Goals
- Preserve compatibility with legacy module messages that omit an instance id.
- Change per-module concurrency rules inside a single instance.
- Run multiple module loaders in the same tunnel.

## Affected Components
- doc/CONTROL_MESSAGES.md
- doc/MODULES.md
- doc/PROTOCOL.md
- doc/LOGGING.md
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/module_loader.py
- sfb/tunnel/tunnel_control_messages.py
- sfb/modules/base_module.py
- sfb/cli.py
- sfb/modules (module constructors updated to accept module_id)
- tests (unit coverage for routing and loader behavior)

## Plan
1. Define a module instance id field.
   - Require `mid` on all module control messages (t not in {tun, ch}).
   - Document the `mid` field in doc/CONTROL_MESSAGES.md and doc/PROTOCOL.md.
   - Standardize `mid` as a positive integer; default instance is `1`.

2. Route module messages by `(type, mid)`.
   - Change `BaseTunnel.register_module`/`unregister_module` to accept `module_id`.
   - Store handlers by `(type, mid)` and update dispatch to select by `mid`.
   - Log and drop module messages that are missing `mid`, use non-positive
     `mid`, or target an unknown instance.

3. Make BaseModule instance-aware.
   - Add `module_id` parameter to the BaseModule constructor (default 1).
   - Register/unregister with `(TYPE, module_id)` via the tunnel.
   - Inject `mid` into outbound messages in `send_message` and include it in module logs.

4. Extend module loader to manage multiple instances.
   - Update `mod.load`, `mod.load_ok`, and `mod.load_err` to carry `mid`.
   - Key `_loaded_modules` and pending loads by `(name, mid)` instead of name.
   - Add `load_remote(name, module_id)` and align local instantiation with that id.
   - Define duplicate-load behavior for the same `(name, mid)` (idempotent ok or error).

5. Expose module instance selection in the CLI.
   - Add `--module-id` (int, default 1).
   - Pass `module_id` into `load_remote` and module construction.
   - Include `module_id` in CLI log fields where modules are loaded.

6. Update module documentation.
   - Document multi-instance semantics and `mid` usage in doc/MODULES.md.
   - Note module instance ids in doc/LOGGING.md where module events are described.
   - Update any module-specific docs that imply a single instance (FILE_TRANSFER.md,
     SOCKS.md, PORT_FWD.md).

7. Tests (unit only, no E2E runs).
   - Add coverage for dispatch routing by `(type, mid)` and loader multi-instance
     behavior.
   - Ensure two instances of the same module type can exchange messages without
     cross-talk.

## Execution Notes (20260106)
- Enforced `mid` on module messages and routed handlers by `(t, mid)`, reserving
  `t="mod"` for the module loader (wildcard handler).
- Updated module loader, module base class, and CLI wiring for instance ids.
- Updated protocol/control/module docs and module-specific examples for `mid`.
- Tests not run (per instructions; tests not updated).
