# SQLite Logging

This project can log to SQLite for queryable debugging. Enable with:

```
python3 -m sfb.cli --role alice --domain example.com --db-log
```

When `--db-log` is provided without a value, the default path is
`./logs/<role>_log.db` (role is `client` or `server`).

## Schema

Table: `logs`

- `id INTEGER PRIMARY KEY`
- `created REAL` (epoch seconds)
- `level TEXT`
- `logger TEXT`
- `message TEXT`
- `event TEXT` (optional structured event name)
- `fields TEXT` (JSON text, optional)
- `pathname TEXT`
- `lineno INTEGER`
- `func TEXT`
- `thread INTEGER`
- `process INTEGER`

Indexes:
- `idx_logs_created` on `created`
- `idx_logs_logger` on `logger`
- `idx_logs_level` on `level`
- `idx_logs_event` on `event`

## Event Names

Current structured events (non-exhaustive):

- `tunnel.state`
- `tunnel.packet_send`
- `tunnel.packet_recv`
- `tunnel.retransmit`
- `tunnel.ack`
- `tunnel.mtu_propose`
- `tunnel.mtu_ok`
- `tunnel.mtu_ack`
- `tunnel.window_propose`
- `tunnel.window_ok`
- `channel.open`
- `channel.open_in`
- `channel.open_ok`
- `channel.open_fail`
- `channel.close`
- `channel.close_in`
- `channel.close_ok`
- `channel.send_buf_full`
- `channel.send_buf_high`
- `channel.pack`
- `sock.connect`
- `sock.connect_ok`
- `sock.connect_err`
- `dns.send`
- `dns.recv`
- `dns.send_empty`
- `dns.cname_followup`
- `dns.error_response`
- `dns.malformed_response`
- `dns.stale_response`
- `dns.send_blocked`
- `dns.prune_stale`
- `tunnel.send_blocked`

## Useful Queries

Open the database:

```
sqlite3 logs/client_log.db
```

Last 50 events:

```
SELECT id, datetime(created, 'unixepoch'), level, event, fields
FROM logs
WHERE event IS NOT NULL
ORDER BY id DESC
LIMIT 50;
```

Channel lifecycle for a channel id:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event LIKE 'channel.%'
  AND fields LIKE '%\"ch\":4%'
ORDER BY id ASC;
```

SOCKS connect flow for a request id:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event LIKE 'sock.%'
  AND fields LIKE '%\"rid\":2%'
ORDER BY id ASC;
```

Packet send/recv around a sequence number:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event IN ('tunnel.packet_send', 'tunnel.packet_recv')
  AND fields LIKE '%\"seq\":195%'
ORDER BY id ASC;
```

Retransmits:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event = 'tunnel.retransmit'
ORDER BY id DESC
LIMIT 50;
```

DNS send/recv:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event IN ('dns.send', 'dns.recv', 'dns.error_response', 'dns.malformed_response', 'dns.stale_response')
ORDER BY id DESC
LIMIT 100;
```

DNS blocked or stale:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event IN ('dns.send_blocked', 'dns.prune_stale')
ORDER BY id DESC
LIMIT 100;
```

Channel buffer pressure:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event IN ('channel.send_buf_full', 'channel.send_buf_high')
ORDER BY id DESC
LIMIT 100;
```

Segment packing:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event = 'channel.pack'
ORDER BY id DESC
LIMIT 100;
```

Send blocked:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event = 'tunnel.send_blocked'
ORDER BY id DESC
LIMIT 100;
```

MTU negotiation:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event LIKE 'tunnel.mtu%'
ORDER BY id ASC;
```

Window negotiation:

```
SELECT id, datetime(created, 'unixepoch'), event, fields
FROM logs
WHERE event LIKE 'tunnel.window%'
ORDER BY id ASC;
```
