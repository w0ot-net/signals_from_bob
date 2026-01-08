# Remote Module Tracking Plan

## Goal
- Track which module instances are loaded on the remote side.
- Add explicit remote unload requests so the registry stays accurate without
  implicit BaseModule notifications.

## Non-Goals
- Add implicit remote unload notifications from `BaseModule.shutdown()`.
- Change tunnel asymmetry or control channel framing.
- Run tests here.

## Affected Components
- sfb/tunnel/tunnel_control_messages.py
- sfb/tunnel/module_loader.py
- sfb/cli.py
- doc/architecture/CONTROL_MESSAGES.md
- doc/architecture/PROTOCOL.md
- doc/architecture/MODULES.md
- doc/architecture/LOGGING.md

## Plan
1. Add module loader unload request/response messages:
   - Define `mod_unload(name, mid)` and `mod_unload_ok(name, mid)` plus
     `mod_unload_err(name, mid, reason)` in `sfb/tunnel/tunnel_control_messages.py`.
   - Document `t="mod", c="unload/unload_ok/unload_err"` alongside
     `load/load_ok/load_err`.
2. Handle unloads in the module loader:
   - Add `unload_remote(name, module_id, timeout)` with the same waiter pattern
     as `load_remote()`.
   - On `unload`, look up `(name, mid)` in `_loaded_modules`, call
     `module.shutdown()`, remove the entry, and respond with `unload_ok`.
     If missing, respond with `unload_err` so the controller can reconcile.
3. Track remote loaded modules:
   - Add `_remote_modules` in `ModuleLoader` keyed by `(name, mid)`.
   - On `load_ok`, record the remote module; on `load_err`, clear any entry.
   - On `unload_ok`, remove the remote entry; on `unload_err`, log and keep
     the entry (caller decides whether to retry).
4. CLI-driven unloads:
   - After `module_cls.run_command()` returns, if the module loader was used to
     load the remote module and the tunnel is still connected, call
     `module_loader.unload_remote(remote_module, module_id)` to avoid stale
     remote entries.
5. Logging and docs:
   - Add `module_loader.remote_unload`, `module_loader.local_unload`, and
     `module_loader.unload_failed` events with name/mid/reason.
   - Update module docs to mention loader-driven unloads and that unload is a
     controller action (not implicit in `BaseModule.shutdown()`).

## Testing
- Do not run tests here. The user will run tests as needed with python3.

## Notes
- If long-lived modules need more graceful teardown than `module.shutdown()`,
  add module-specific stop commands in a separate change.

## Execution Notes
- Added module unload control messages and loader handling with remote tracking.
- CLI now unloads remote modules after command completion when connected.
- Updated module loader and logging documentation for unload semantics.
