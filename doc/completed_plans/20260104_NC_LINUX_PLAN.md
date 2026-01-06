# NC Linux Module Plan

## Summary
Introduce a linux-only nc_linux module that binds a tunnel channel to a local
file descriptor on Alice, with Bob issuing the bind request and driving data
flow over the channel.

## Affected Components
- sfb/modules/nc_linux/__init__.py
- sfb/modules/nc_linux/nc_linux.py
- sfb/modules/nc_linux/nc_linux_control_messages.py
- sfb/modules/nc_linux/nc_linux_pump.py
- sfb/modules/__init__.py
- sfb/cli.py
- sfb/config.py
- sfb/logging_util.py
- sfb/log_profiles.py
- doc/MODULES.md
- doc/CONTROL_MESSAGES.md
- doc/NC_LINUX.md
- tests/test_nc_linux.py
- integration_tests/test_nc_linux.py

## Plan
1) Define control messages and flow.
   - Pick a short message type (e.g., "nc") and document it.
   - Add helpers for bind, bind_ok, err, and optional close/half-close.
   - Include fields for rid, ch, fd, and optional mode/close behavior.
   - Specify error codes for non-linux, invalid fd, or missing channel.

2) Implement the Alice-side module behavior.
   - Validate linux-only support early and respond with err on other OSes.
   - Resolve channel by id, track active bindings, and avoid double-bind.
   - Start two pump threads: fd -> channel and channel -> fd.
   - Use only stdlib (os, select, errno, fcntl) and keep py2/py3 safe.
   - On fd EOF, half-close channel; on channel EOF, optionally close fd.
   - Ensure shutdown stops pumps and cleans up channel state.

3) Implement Bob-side command entry points.
   - Add CLI subcommand to request binding a remote fd to a new channel.
   - Open channel, wait for open_ok, send bind request, wait for bind_ok.
   - Pump local stdin/stdout (or provided local fd) to the channel.
   - Close channel on local EOF and propagate close to the remote pump.

4) Register module and logging/config hooks.
   - Add nc_linux to AVAILABLE_MODULES and module registry.
   - Add config defaults (buffer sizes, poll timeouts, close-on-exit).
   - Add a log_component_module_nc_linux toggle in config/logging_util.

5) Document usage and control messages.
   - Add doc/NC_LINUX.md with usage and linux-only limitation.
   - Update doc/MODULES.md and doc/CONTROL_MESSAGES.md.

## Validation
- Unit tests for bind request/response and error paths.
- Integration test using in-memory transport with a pipe fd on Alice:
  Bob sends bytes and receives echoed data over the channel.
- Run new tests with python3; do not run tests/e2e.

## Open Questions
- Should Bob default to local stdin/stdout, or require explicit local fd args?
- Should Alice close the bound fd when the channel closes, or leave it open?
- Do we need read-only/write-only modes for one-way bindings?
- Should a bind failure trigger an automatic channel close on Bob?

## Execution Notes
- Required explicit local/remote fd specs; no stdin/stdout defaults.
- Bound fds close on channel shutdown; no read-only/write-only modes added.
- Bind failures close the channel on both sides.
- Accepted fd specs as numeric fd, path, or host:port ([::1]:port supported).
- Ran `python3 -m unittest tests.test_nc_linux integration_tests.test_nc_linux`.
