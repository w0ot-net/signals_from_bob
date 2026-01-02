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

## Plan
1) Define intended close semantics and update design docs.
   - Decide on a clean API that avoids transitional signatures. One option:
     make close() graceful (drain send buffer, then send CLOSE) and add an
     abort() for immediate close; update all call sites accordingly.
   - Define "drain" precisely (for example: no queued bytes, in-flight
     frames may still arrive) and specify the maximum wait policy before
     forcing abort.
   - Define behavior on remote CLOSE (full close vs half-close), and how
     late in-flight data is handled after CLOSE.
   - Document when CLOSE is sent and how pending data is handled.
2) Fix the ControlChannel error path.
   - Import ChannelError in sfb/channel/control_channel.py.
   - Add or adjust a unit test to ensure invalid control messages raise
     ChannelError and are handled by the tunnel layer.
3) Add a receive buffer bound and overflow behavior.
   - Introduce a new config option (for example channel_max_recv_buf) or
     re-use an existing limit if appropriate.
   - Define limit semantics (bytes vs messages), and specify where the
     limit is enforced (pre- or post-reassembly).
   - Define overflow behavior (for example: close channel with error and
     drop excess data), and make it consistent across platforms.
   - Update docs to describe receive-side limits and error behavior.
4) Reduce active-channel churn overhead.
   - Replace the O(n) removal pattern with a data structure that supports
     faster removal and stable round-robin ordering (for example: a deque
     plus a set of active IDs, or a list with lazy cleanup and periodic
     compaction).
   - Define compaction criteria if using lazy cleanup and how fairness is
     preserved when channels are removed mid-iteration.
   - Keep round-robin fairness and avoid lock-heavy operations.
5) Validation and documentation.
   - Run unit or module-level tests that cover close/abort semantics,
     control message handling, and send/recv buffer limits (including
     overflow behavior).
   - Do not run tests/e2e; note this in the change summary.
   - Update doc/CHANNEL.md, doc/CHANNEL_MANAGER.md, and any protocol or
     control-message docs that describe CLOSE and error behavior.

## Execution Notes
- Use python3 for any scripts or tests.
- Keep code ASCII-only and use only the standard library.
