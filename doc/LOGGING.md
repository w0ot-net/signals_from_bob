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
- `sock.connect`
- `sock.connect_ok`
- `sock.connect_err`

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
