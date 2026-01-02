# Channel Performance and Correctness Plan

## Goals
- Fix correctness issues in the channel layer without adding non-standard
  dependencies and while keeping Python 2.7/3 compatibility.
- Improve performance and robustness under high channel churn and high load.
- Update docs and tests to reflect the final behavior.

## Issues to Address
- ControlChannel raises NameError because ChannelError is not imported.
- Channel close can drop queued data because CLOSE is sent before the send
  buffer drains.
- Channel receive buffer is unbounded, risking memory growth and throughput
  collapse when readers are slow.
- Active channel removal is O(n) per removal and can degrade under churn.

## Acceptance Criteria (draft)
- close() is graceful by default: stop new writes, drain queued send data,
  then send CLOSE; late in-flight data is handled per the documented rule.
- abort() is immediate: no drain, queued outbound data is dropped, and the
  channel closes with an explicit error code/message.
- Close timeout policy follows asymmetry rules: Alice uses packet-count
  progress thresholds, Bob uses wall-clock silence.
- Receive buffer limit is per-channel bytes; on overflow, close with error
  and drop subsequent inbound data for that channel.
- Keepalive pong suppression remains when any channel has pending data.
- Active-channel removal is O(1) while keeping round-robin ordering stable.
- Mixed-version peers are not supported; protocol changes may be breaking.

## Plan
1) Define intended close semantics and update design docs.
   - Decide on a clean API that avoids transitional signatures. One option:
     make close() graceful (drain send buffer, then send CLOSE) and add an
     abort() for immediate close; update all call sites accordingly.
   - Specify state transitions and return values for read/write after local
     close, remote close, and abort; choose exact ChannelError codes.
   - Define "drain" precisely (for example: no queued bytes, in-flight
     frames may still arrive) and specify the maximum wait policy before
     forcing abort.
   - Define whether close() is half-close or full close and what read/write
     calls return after local or remote CLOSE; document how late in-flight
     data is handled after CLOSE.
   - Document when CLOSE is sent and how pending data is handled.
   - Cross-check close/abort, retransmit, and timeout behavior against
     doc/ASYMMETRY.md and ensure keepalive pong suppression is preserved
     when data is pending on any channel.
2) Fix the ControlChannel error path.
   - Import ChannelError in sfb/channel/control_channel.py.
   - Add or adjust a unit test to ensure invalid control messages raise
     ChannelError and are handled by the tunnel layer.
3) Add a receive buffer bound and overflow behavior.
   - Introduce a new config option (for example channel_max_recv_buf) or
     re-use an existing limit if appropriate; make it bytes and per-channel.
   - Decide whether the limit is negotiated or local-only; default to
     local-only unless a negotiation benefit is clear.
   - Define limit semantics (bytes vs messages), clarify that the limit is
     per-channel, and specify where the limit is enforced (pre- or
     post-reassembly).
   - Set the default receive buffer limit to 64k and document the
     rationale and tuning guidance.
   - Define overflow behavior (for example: close channel with error and
     drop excess data), including which control message is sent and what
     happens to subsequent inbound data on that channel.
   - Update docs to describe receive-side limits and error behavior.
4) Reduce active-channel churn overhead.
   - Replace the O(n) removal pattern with a data structure that supports
     faster removal and stable round-robin ordering (for example: an
     OrderedDict keyed by channel ID to allow O(1) remove while preserving
     iteration order in Python 2.7).
   - Specify required invariants up front (stable ordering, O(1)
     add/remove, bounded cleanup cost, no fairness regressions).
   - Document the exact round-robin rotation method (for example:
     popitem(last=False) and reinsert to tail after scheduling) so
     fairness remains unchanged.
   - Keep round-robin fairness and avoid lock-heavy operations.
5) Validation and documentation.
   - Add or update unit or module-level tests that cover close/abort
     semantics, including state transitions and error codes.
   - Add tests for receive buffer overflow (limit enforcement, error close,
     and dropping subsequent inbound data).
   - Add coverage for keepalive pong suppression with pending channel data.
   - Add a light churn regression check (unit-style or microbench) that
     exercises add/remove under load without relying on e2e tests.
   - Run unit or module-level tests that cover close/abort semantics,
     control message handling, and send/recv buffer limits (including
     overflow behavior).
   - Ensure tests remain Python 2.7/3 compatible, but run them with
     python3.
   - Do not run tests/e2e; note this in the change summary.
   - Update doc/CHANNEL.md, doc/CHANNEL_MANAGER.md, and any protocol or
     control-message docs that describe CLOSE and error behavior.

## Execution Notes
- Use python3 for any scripts or tests.
- Keep code ASCII-only and use only the standard library.
- Commit and push after making code changes.
