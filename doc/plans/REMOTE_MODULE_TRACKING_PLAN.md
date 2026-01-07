# Remote Module Tracking Plan

## Goal
- Track which module instances are loaded on the remote side.
- Have modules announce unload events so the remote registry stays accurate.

## Non-Goals
- Add a remote unload request command.
- Change tunnel asymmetry or control channel framing.
- Run tests here.

## Affected Components
- sfb/tunnel/tunnel_control_messages.py
- sfb/tunnel/module_loader.py
- sfb/modules/base_module.py
- sfb/modules/file_transfer/file_transfer.py
- sfb/modules/socks/socks_server.py
- sfb/modules/socks/socks_relay.py
- sfb/modules/port_fwd/port_fwd_server.py
- sfb/modules/port_fwd/port_fwd_relay.py
- sfb/modules/nc_linux/nc_linux.py
- doc/CONTROL_MESSAGES.md
- doc/PROTOCOL.md
- doc/MODULES.md
- doc/LOGGING.md

## Plan
1. Add module loader unload messages:
   - Define `mod_unload(name, mid, reason=None)` in
     `sfb/tunnel/tunnel_control_messages.py`.
   - Document `t="mod", c="unload"` alongside `load/load_ok/load_err`.
2. Introduce a required module loader name on modules:
   - Add `LOADER_NAME` to `BaseModule` and enforce it is set for modules.
   - Set `LOADER_NAME` on each built-in module to match the key in
     `AVAILABLE_MODULES` (e.g., `file_transfer`, `socks`, `port_fwd_server`).
3. Announce local unloads:
   - Extend `BaseModule.shutdown()` to call a module loader helper that:
     - Removes the local `(name, mid)` entry if present.
     - Sends `mod_unload` to the peer once per module instance.
4. Track remote loaded modules:
   - Add `_remote_modules` in `ModuleLoader` keyed by `(name, mid)`.
   - On `load_ok`, record the remote module; on `load_err`, clear any entry.
   - On `unload`, remove the remote entry and log the event.
5. Logging and docs:
   - Add `module_loader.remote_unload` and `module_loader.local_unload`
     log events (include name, mid, reason).
   - Update module docs to mention unload announcements and the required
     `LOADER_NAME` attribute.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
