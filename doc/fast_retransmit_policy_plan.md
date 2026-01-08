# Fast Retransmit Policy Extraction Plan

## Summary
Move fast-retransmit selection and bookkeeping out of `AliceTunnel` into a
reliability helper that sits on top of `SendWindow` and `RttEstimator`. The
goal is to keep behavior unchanged while reducing tunnel complexity and
keeping policy close to the reliability layer.

## Goals
- Keep fast-retransmit behavior and logging outcomes unchanged.
- Remove fast-retransmit state and selection logic from
  `sfb/tunnel/alice_tunnel.py`.
- Avoid direct access to `SendWindow` internals from the tunnel.

## Affected Components
- `sfb/tunnel/alice_tunnel.py` (replace fast-retransmit logic with helper)
- `sfb/reliability/fast_retransmit.py` (new helper/controller)
- `sfb/reliability/__init__.py` (export helper)
- `sfb/reliability/send_window.py` (optional: add minimal public helper for
  unacked keys to support pruning)

## Plan
1. Add `FastRetransmitController` (or similar) in
   `sfb/reliability/fast_retransmit.py`:
   - Constructor takes `send_window`, `rtt`, `min_age_ratio`,
     `max_per_seq`, and `min_rto_ms`.
   - `prune()` removes counts for seqs no longer in-flight without relying on
     private `SendWindow` fields.
   - `select_candidate(now, ack_silence, max_window, cap_override=None)`
     returns a `(seq, segments, flags, encrypted_body, send_time)` tuple or
     `None` using the same rules currently in `AliceTunnel`:
       - ACK silence < RTO
       - SACK progress ready
       - `distance_exceeded()` with `cap_override` and `max_window`
       - min-age gating with `min_rto_ms` cap
       - per-seq backoff after `max_per_seq`
   - `note_sent(seq)` records fast-retransmit count increments on success.
2. Update `sfb/tunnel/alice_tunnel.py`:
   - Replace `_fast_retransmit_*` fields and helpers with the controller.
   - `_maybe_fast_retransmit()` becomes a thin wrapper:
     - compute `cap_override` (via pacer if enabled)
     - ask the controller for a candidate
     - if allowed and send succeeds, call `note_sent(seq)`
   - Keep existing log events and reasons unchanged.
3. If needed, add a small public helper on `SendWindow` to expose unacked
   keys for pruning (for example `unacked_seqs()` returning a list or set).
4. Export the controller from `sfb/reliability/__init__.py`.
5. Verify no behavior changes:
   - Same fast-retransmit gating and backoff rules.
   - Same tunnel-side send gating via `_can_send_retransmit`.
   - Same log events and pacing interactions.

## Success Criteria
- `AliceTunnel` no longer owns fast-retransmit selection/bookkeeping state.
- Fast-retransmit selection resides under `sfb/reliability`.
- No new protocol or pacing behavior changes.
