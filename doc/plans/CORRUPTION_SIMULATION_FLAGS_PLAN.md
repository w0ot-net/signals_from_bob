# Corruption Simulation Flags Plan

Status: draft

## Goal

Add CLI flags to inject packet corruption via the lossy transport wrapper for
any transport: `--corrupt` (both directions), `--rx-corrupt`, and
`--tx-corrupt`.

## Non-Goals

- Add CLI flags for delay, jitter, duplication, reordering, or loss.
- Change transport semantics, MTU negotiation, or tunnel reliability behavior.
- Add or run tests under tests/e2e/.

## Affected Components

- sfb/cli.py
- doc/architecture/LOSSY_TRANSPORT.md
- doc/architecture/TRANSPORTS.md

## Design Notes

- Flags accept percentages in [0, 100] and convert to corruption rates in
  [0.0, 1.0].
- `--corrupt` sets both directions; `--rx-corrupt`/`--tx-corrupt` override per
  direction.
- Direction mapping is local:
  - Client: tx = requests (Alice -> Bob), rx = responses (Bob -> Alice).
  - Server: tx = responses (Bob -> Alice), rx = requests (Alice -> Bob).
- Implement by wrapping the transport with `LossyTransport` (client) or
  `LossyServer` (server) using `NetworkImpairment(corrupt_rate=rate,
  corrupt_mode='mutate')`; only wrap when any rate is non-zero.
- Log the effective rates at startup; using the flags on both sides compounds
  impairment (document this explicitly).

## Implementation Steps

1. Add `--corrupt`, `--rx-corrupt`, and `--tx-corrupt` to common CLI arguments
   with percent validation and clear help text about direction and precedence.
2. Add a helper in `sfb/cli.py` to compute tx/rx rates and wrap the transport
   with `LossyTransport` or `LossyServer`, passing `stats_enabled` when verbose.
3. Wire the wrapper into `run_client` and `run_server` before tunnel creation,
   and emit a structured log event when corruption is enabled.
4. Update `doc/architecture/LOSSY_TRANSPORT.md` and
   `doc/architecture/TRANSPORTS.md` to document the CLI flags, direction
   mapping, and compounding behavior when both sides enable corruption.

## Validation

- Manual run with python3: start a client/server pair using `--corrupt 1` and
  confirm logs show the lossy wrapper and configured rates.
- Verify `--rx-corrupt`/`--tx-corrupt` override behavior and that `0` disables
  corruption.
- Do not run tests/e2e/.
