# Lossy Transport

## Overview

The lossy transport wrappers inject controlled impairment around any existing
transport. They operate at the wrapper layer, without transport-specific
assumptions, and preserve the request/response semantics for both Alice and
Bob.

- Alice uses `LossyTransport` (client side)
- Bob uses `LossyServer` (server side)
- Impairment is applied to both directions (request and response)

## Configuration

`NetworkImpairment` holds the impairment configuration. It is a pure config
object; per-packet decisions come from the internal impairment engine.

Parameters (all rates are probabilities in [0.0, 1.0]):

- `loss_rate`: random packet loss
- `burst_loss_prob`: probability to enter a burst loss run
- `burst_loss_len`: `(min, max)` packets to drop in a burst
- `delay_ms`: base delay in milliseconds
- `jitter_ms`: jitter range added to `delay_ms`
- `dup_rate`: probability of duplicating a packet
- `reorder_rate`: probability of holding a packet for reordering
- `reorder_wait_ms`: extra hold time for reordered packets
- `corrupt_rate`: probability of corrupting packet bytes
- `corrupt_bytes`: `(min, max)` bytes to flip per packet
- `corrupt_mode`: `drop` (discard corrupted packet) or `mutate`
- `seed`: RNG seed for deterministic runs

`LossyTransport` adds `pending_timeout_sec` to control how long synthetic
pending entries (drops or stale requests) linger before pruning.

## Impairment Decisions

Each packet gets a single decision with deterministic RNG usage:

- Drop (including burst loss)
- Corrupt (drop or mutate)
- Delay and jitter
- Reorder (adds `reorder_wait_ms` to delay)
- Duplicate count (at most one duplicate)

Stats are based on impairment decisions, not guaranteed deliveries. For
example, duplication is counted when selected even if later suppressed by
capacity.

## LossyTransport (Alice)

### Send Path

- Validates payload size against `send_packet_mtu` before impairment decisions.
- Allocates a wrapper correlation ID immediately and marks it as pending.
- Applies impairment in this order:
  - Drop: do not call the inner transport; pending remains until timeout.
  - Corrupt: drop or mutate depending on `corrupt_mode`.
  - Delay/reorder: schedule the send for later with a held inner permit.
  - Duplicate: schedule one extra send if an extra inner permit is available.

Wrapper IDs are independent of inner transport IDs. Each inner `corr_id` maps
back to the wrapper `corr_id`. Duplicate sends map multiple inner IDs to the
same wrapper ID.

Scheduled sends are dispatched during `reserve_send()` and `recv()`.

### Receive Path

- Flushes due delayed sends and delayed responses before blocking.
- Polls the inner transport and maps inner IDs to wrapper IDs.
- Applies receive impairment decisions:
  - Drop: response is discarded; pending remains until timeout.
  - Corrupt: drop or mutate depending on `corrupt_mode`.
  - Delay/reorder: response is queued for later delivery.
  - Duplicate: schedules one extra delivery of the same response.

### Pending Tracking

`pending_count()` reports the total outstanding requests, including:

- Synthetic drops (no inner send)
- Delayed sends
- In-flight inner requests (including duplicates)

Wrapper pending entries are pruned after `pending_timeout_sec` using the
wrapper timer, not the inner transport's timeout.

## LossyServer (Bob)

### Request Path

Incoming requests are impaired before handing data to the tunnel:

- Drop and corrupt decisions occur before delivery.
- Delay/reorder requests are queued and delivered later.
- Duplicates are delivered as additional requests on subsequent `recv()` calls.

### Response Path

The responder is wrapped to apply send impairment:

- Drop and corrupt decisions discard the response or mutate it.
- Delay/reorder/duplicate responses are queued and flushed during `recv()`.

This preserves the asymmetric constraint that Bob only sends responses in
reaction to polls.

## Determinism

Use `seed` to make impairment decisions deterministic. Each wrapper direction
gets its own impairment engine unless the same `NetworkImpairment` instance is
reused for both directions, in which case RNG state and stats are shared.

## Stats

`LossyTransport` and `LossyServer` accept `stats_enabled` (default false) to
enable decision counters. When disabled, counters are not updated and
`stats()` returns `{}`.

When enabled, `LossyTransport.stats()` and `LossyServer.stats()` report
decision counts for send and receive impairment:

- `sent`
- `dropped`
- `delayed`
- `duplicated`
- `reordered`
- `corrupted`
