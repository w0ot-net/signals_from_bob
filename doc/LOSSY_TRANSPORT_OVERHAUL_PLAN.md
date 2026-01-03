# Lossy Transport Overhaul Plan

## Summary
Rewrite the lossy transport wrappers to apply impairment in a transport-agnostic
way, with accurate pending tracking and full support for drop/delay/jitter,
duplication, reordering, and corruption on both request and response paths.

## Goals
- Work cleanly with the existing DNS, ICMP, and memory transports without
  transport-specific assumptions.
- Apply impairment symmetrically on both directions (Alice->Bob and Bob->Alice).
- Preserve transport semantics: `reserve_send()`, `send()`, `recv()`,
  `pending_count()`, `max_in_flight`, and asymmetric MTU.
- Deterministic behavior via seeds and stable RNG usage.
- Keep Python 2.7/3 compatibility, standard library only, and Windows/Linux
  support.

## Non-Goals
- Add new transport types or change tunnel protocol behavior.
- Introduce non-standard dependencies or external tooling.

## Affected Components
- `sfb/transport/lossy.py` (complete rewrite)
- `sfb/transport/transport_base.py` (optional shared helpers for pending queues)
- `sfb/transport/__init__.py` (exports if new helpers or config are added)
- `tests/test_lossy_transport.py`
- `tests/test_transport_base.py` (if new helper types are added)
- `tests/e2e/test_dns_e2e_lossy.py` (adapt expectations only; do not run)
- `doc/TRANSPORTS.md` (lossy behavior and corr_id mapping notes)
- `doc/LOSSY_TRANSPORT.md` (new spec doc for impairment semantics)

## Design Notes

### Impairment Engine
- Split configuration (`NetworkImpairment`) from execution:
  - Add an internal `ImpairmentEngine` that consumes a config and produces a
    single per-packet decision with stable RNG usage (drop, corrupt, delay,
    reorder, duplicate count).
- Implement real byte corruption when enabled:
  - Use `bytearray` and the configured `corrupt_bytes` range to flip random
    bytes.
  - Provide a `corrupt_mode` switch (`drop` vs `mutate`) so current tests can
    keep drop semantics until updated.
- Use a min-heap (`heapq`) for delayed delivery queues to avoid O(n) scans.

### Transport Wrapper (Alice)
- Maintain wrapper-level correlation IDs and pending tracking independent of
  inner transport IDs.
- `reserve_send()`:
  - Prune wrapper pending entries by timeout.
  - Enforce `max_in_flight` against wrapper pending + reserved.
  - Do not rely on inner `pending_count()` for wrapper capacity.
- `send()`:
  - Validate payload type/size against `send_mtu` before impairment decisions.
  - Generate a wrapper corr_id immediately and register it as pending.
  - Apply send impairment:
    - Drop: record pending entry with timeout and return corr_id without calling
      inner transport.
    - Corrupt: mutate bytes (or drop, per `corrupt_mode`) before dispatch.
    - Delay/reorder: schedule a send event with a held inner permit.
    - Duplicate: schedule additional send events; map all inner corr_ids to the
      same wrapper corr_id.
- Dispatch scheduled sends on `recv()` and `reserve_send()` calls:
  - When due, call inner `send()` and record `inner_corr_id -> wrapper_corr_id`.
  - Track per-wrapper corr_id completion state and an optional duplicate linger
    window so late duplicates can still be delivered.
- `recv()`:
  - Flush due delayed sends and delayed responses before blocking.
  - Poll inner transport, map `inner_corr_id` to wrapper corr_id, and apply
    receive impairment decisions (drop/corrupt/delay/reorder/duplicate).
  - Dropped responses should keep the wrapper pending entry until timeout.
- `pending_count()`:
  - Report wrapper pending entries (including delayed or dropped) to preserve
    backpressure and headroom behavior in the tunnel.

### Server Wrapper (Bob)
- Incoming request impairment:
  - Apply drop/corrupt/delay/reorder/duplicate before handing data to the
    tunnel.
  - Queue delayed requests in a heap keyed by delivery time.
- Response impairment:
  - Wrap the responder to apply send impairment.
  - If delay/reorder/duplicate is selected, queue the actual responder calls
    and flush them during `recv()` iterations.
- Preserve asymmetry: the lossy wrapper never initiates sends without a
  responder invocation.

### API Compatibility
- Keep `LossyTransport`, `LossyServer`, and `NetworkImpairment` public names.
- If new knobs are needed (e.g., `pending_timeout`, `corrupt_mode`,
  `dup_linger_ms`), add them explicitly and update all call sites at once.
- Document the exact semantics in `doc/LOSSY_TRANSPORT.md`.

## Implementation Order
1. Add `doc/LOSSY_TRANSPORT.md` describing impairment semantics, corr_id
   mapping, and pending behavior.
2. Introduce internal helpers in `lossy.py` (impairment engine, event queues,
   pending tracker utilities) and update `NetworkImpairment` as needed.
3. Rework `LossyTransport` send path with wrapper corr_ids, queued sends, and
   deterministic impairment decisions.
4. Rework `LossyTransport` recv path with mapping, response impairment, and
   non-recursive loops.
5. Rework `LossyServer` request and responder handling with queued delays and
   send impairment.
6. Update tests to cover send-side delay/reorder, duplicate mapping behavior,
   corruption mode, and pending_count accuracy.
7. Update `doc/TRANSPORTS.md` and any other relevant docs to align with new
   semantics.

## Tests to Update or Add
- `tests/test_lossy_transport.py`:
  - Send-side delay/reorder/duplication behavior.
  - Corruption mode (drop vs mutate).
  - Pending count remains elevated after dropped responses until timeout.
  - Duplicate sends map to the same wrapper corr_id.
  - No recursion loops when dropping many packets in a row.
- `tests/test_transport_base.py` (only if shared helpers are added).

## Open Questions
- Default corruption behavior: keep drop semantics for compatibility or switch
  to byte mutation by default.
- Duplicate response delivery: keep mapping alive for a short linger window or
  drop duplicates once a response has been delivered.
