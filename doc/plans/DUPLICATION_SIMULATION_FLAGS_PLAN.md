# Duplication Simulation Flags Plan

Status: draft

## Goal

Add CLI flags to inject packet duplication via the lossy transport wrapper for
any transport: `--dup` (both directions), `--rx-dup`, and `--tx-dup`.

## Non-Goals

- Add CLI flags for loss, delay, jitter, reordering, or corruption.
- Change transport semantics, MTU negotiation, or tunnel reliability behavior.
- Add or run tests under tests/e2e/.

## Affected Components

- sfb/cli.py
- doc/architecture/LOSSY_TRANSPORT.md
- doc/architecture/TRANSPORTS.md

## Design Notes

- Flags accept percentages in [0, 100] and convert to duplication rates in
  [0.0, 1.0].
- `--dup` sets both directions; `--rx-dup`/`--tx-dup` override per direction.
- Direction mapping is local:
  - Client: tx = requests (Alice -> Bob), rx = responses (Bob -> Alice).
  - Server: tx = responses (Bob -> Alice), rx = requests (Alice -> Bob).
- Implement by wrapping the transport with `LossyTransport` (client) or
  `LossyServer` (server) using `NetworkImpairment(dup_rate=rate)`; only wrap
  when any rate is non-zero.
- If loss flags are also enabled, configure a single wrapper per side with both
  `loss_rate` and `dup_rate` to avoid double-wrapping.
- Log the effective rates at startup; using duplication on both sides compounds
  duplicates and can amplify traffic.

## Implementation Steps

1. Add `--dup`, `--rx-dup`, and `--tx-dup` to common CLI arguments with percent
   validation and clear help text about direction and precedence.
2. Extend the loss wrapper helper in `sfb/cli.py` to compute tx/rx duplication
   rates, merge with loss configuration when present, and emit a structured log
   event with both loss and duplication rates.
3. Wire the wrapper into `run_client` and `run_server` before tunnel creation,
   and ensure `0` disables duplication for that direction.
4. Update `doc/architecture/LOSSY_TRANSPORT.md` and
   `doc/architecture/TRANSPORTS.md` to document the CLI flags, direction
   mapping, and compounding behavior when both sides enable duplication.

## Validation

- Manual run with python3: start a client/server pair using `--dup 1` and
  confirm logs show the lossy wrapper and configured rates.
- Verify `--rx-dup`/`--tx-dup` override behavior and that `0` disables dup.
- Do not run tests/e2e/.
