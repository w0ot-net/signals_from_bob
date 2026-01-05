# Tunnel Race Conditions Plan

## Summary
Address races in tunnel background thread lifecycle, module loader pending state,
and module handler dispatch to prevent concurrent transport use and missed
module load responses.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/module_loader.py
- doc/TUNNEL_RACE_CONDITIONS_PLAN.md

## Plan
1) Make background thread lifecycle thread-safe in BaseTunnel.
   - Add a lock guarding start_background() and stop_background().
   - Only clear _bg_thread when the thread has actually exited; if join times
     out and it is still alive, keep the reference and log a warning so a
     second background thread cannot start.
   - If a previous thread exists but is not alive, clean it up before starting
     a new one.

2) Add synchronization for module handler registration and dispatch.
   - Guard register_module() and unregister_module() with a lock.
   - In _dispatch_control_message(), copy the handler under the lock and
     invoke it outside the lock to avoid deadlocks.

3) Fix ModuleLoader pending coordination for concurrent load_remote() calls.
   - Track per-module pending state with a list of waiter events and an
     in_flight flag.
   - Only send the load request once per in-flight module load.
   - When a response arrives, set success/reason and signal all waiters.
   - Remove pending entries only after the response is processed and the
     last waiter exits, so timeouts by one caller do not prevent signaling
     others.

4) Clarify and guard run mode usage in BobTunnel.
   - Add a small guard or log warning to discourage simultaneous use of
     serve_forever() and start_background(), since both consume transport.recv.

## Validation
- Run existing non-E2E checks (if any) with python3.
- Manually exercise: start/stop background threads repeatedly, run concurrent
  load_remote() calls for the same module, and confirm only one request is sent
  while all waiters complete.
- Do not run tests under tests/e2e/.

## Risks and Notes
- Background thread shutdown relies on transport recv timeouts; ensure the
  join timeout is sized to at least one recv interval so the thread can exit
  cleanly.
- Concurrency changes should remain compatible with Windows and Linux and use
  only the Python standard library.
