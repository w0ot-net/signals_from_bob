# Double Proxy Client Bug

## Summary

When two concurrent SOCKS clients (e.g., two `wget` processes) run through the
DNS tunnel, the session eventually breaks down. This does not happen with a
single client, and it did not reproduce when two clients targeted the same host
after a fix for duplicate connect requests. It still reproduces when two
clients target different hosts.

## Current Symptoms

- Alice (client) keeps sending DNS queries until pending hits the max.
- Alice reports repeated `channel.send_buf_full` for channel `ch=2`.
- Alice sees frequent `dns.error_response` events.
- Bob (server) enters a retransmit storm on a single sequence number (e.g. seq=26).
- ACK progress stalls on Bob; forward progress stops even though packets are
  still flowing.

## Evidence From Logs

Alice (`logs/client_log.db`):
- `sock.connect` events show the second connect starts, then DNS pending climbs.
- `channel.send_buf_full` repeats for `ch=2` at buffer size 65536.
- `dns.error_response` spikes around the same time as the buffer fills.
- `tunnel.retransmit` spikes shortly after.

Bob (`logs/server_log.db`):
- `tunnel.retransmit` repeats for the same `seq` (e.g. 26) for an extended span.
- Packet send appears to continue, but ACKs do not advance.

## What We Think This Means

The failure is not a channel state mismatch. It looks like a backpressure
collapse:

1) Alice send buffers saturate (ch=2 full).
2) DNS responses error out on Alice (likely dropped upstream).
3) Bob keeps retransmitting the same seq, but ACK progress stalls.
4) No forward progress, so both sides keep retrying.

## Instrumentation Added

Structured logging to SQLite with these events:

- Tunnel: `tunnel.packet_send`, `tunnel.packet_recv`, `tunnel.retransmit`,
  `tunnel.ack`, `tunnel.mtu_propose`, `tunnel.mtu_ok`, `tunnel.mtu_ack`,
  `tunnel.window_propose`, `tunnel.window_ok`, `tunnel.state`
- Channels: `channel.open`, `channel.open_ok`, `channel.open_fail`,
  `channel.close`, `channel.close_ok`, `channel.send_buf_full`,
  `channel.send_buf_high`
- SOCKS: `sock.connect`, `sock.connect_ok`, `sock.connect_err`
- DNS: `dns.send`, `dns.recv`, `dns.error_response`, `dns.send_blocked`,
  `dns.prune_stale`, `dns.send_empty`, `dns.cname_followup`

See `doc/LOGGING.md` for SQL queries.

## Queries Used

Example (Alice):

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event IN ('channel.send_buf_full', 'dns.error_response', 'tunnel.retransmit')
ORDER BY id DESC
LIMIT 50;
```

Example (Bob):

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event = 'tunnel.retransmit'
ORDER BY id DESC
LIMIT 50;
```

## Next Steps

- Add a focused query for ACK progression around a retransmit storm:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event IN ('tunnel.packet_send', 'tunnel.packet_recv', 'tunnel.ack')
  AND id BETWEEN <start_id> AND <end_id>
ORDER BY id ASC;
```

- To track ACK changes only:

```
SELECT id, datetime(created, 'unixepoch'), fields
FROM logs
WHERE event = 'tunnel.ack'
  AND id BETWEEN <start_id> AND <end_id>
ORDER BY id ASC;
```

- Add a focused query for ACK progression around the retransmit storm
  (seq and ack fields).
- Inspect whether send window is stuck with one unacked packet and why ACKs are
  not advancing.
- If ACKs are stuck, check whether channel segmentation or send window
  bookkeeping has a corner case under high concurrency.
