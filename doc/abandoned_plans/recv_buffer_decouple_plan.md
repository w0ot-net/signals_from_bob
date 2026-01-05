# Recv Buffer Decouple Plan

## Summary
Introduce a `tunnel_recv_buffer` configuration knob to let Bob size his
recv_window buffer independently of the negotiated send window. Default
behavior remains unchanged, but operators can increase Bob's out-of-order
buffer to absorb reordering without increasing Alice's max_in_flight.

## Goals
- Allow explicit recv buffer sizing per role without affecting window negotiation.
- Preserve existing defaults when the new setting is unset.
- Keep SACK and out-of-window enforcement unchanged (cap at 256).

## Affected Components
- `sfb/config.py` (new `tunnel_recv_buffer` field + validation)
- `sfb/cli.py` (new `--tunnel-recv-buffer` arg + config wiring)
- `sfb/tunnel/base_tunnel.py` (initialize recv_window max_buffer from config)
- `sfb/reliability/recv_window.py` (clarify max_buffer bounds, reuse setter if needed)
- `doc/RELIABILITY.md` (buffer sizing description)
- `doc/PROTOCOL.md` (window negotiation guarantees section)

## Plan
1. Add `tunnel_recv_buffer: Optional[int] = None` to `Config` and validate:
   - Allow None for default behavior.
   - Enforce 1-256 when set (matching SACK_BITS/MAX_IN_FLIGHT).
2. Add a CLI flag (e.g., `--tunnel-recv-buffer`) in `add_common_args` and wire
   into `config_kwargs`, so Bob can set it without changing `max_in_flight`.
3. In `BaseTunnel.__init__`, compute `recv_buffer_max` as:
   - `config.tunnel_recv_buffer` if set, else `self._proposed_window`.
   - Clamp to `self.MAX_WINDOW` before creating `RecvWindow`.
4. If needed, reuse `RecvWindow.set_max_buffer` or update its docstring to
   emphasize the 1-256 limit and that it is independent of send window size.
5. Update docs:
   - `doc/RELIABILITY.md`: describe optional recv buffer sizing and note it
     is not tied to negotiated max_in_flight when configured.
   - `doc/PROTOCOL.md`: adjust the window negotiation guarantees to state that
     send window caps inflight, but recv buffer can be independently bounded.

## Success Criteria
- With `--tunnel-recv-buffer` set on Bob, logs show `recv_max_buffer` matching
  the configured value while negotiated `max_in_flight` remains unchanged.
- Default behavior remains identical when the option is unset.
- Buffer-full drops on Bob are reduced in the same workload without increasing
  Alice's in-flight window.
