# SQLite Logging

This project can log to SQLite for queryable debugging. Enable with:

```
python3 -m sfb.cli --role alice --domain example.com --db-log
```

When `--db-log` is provided without a value, the default path is
`./logs/<role>_log.db` (role is `client` or `server`).

## Component Logging (config-only)

Fine-grained logging is controlled in `sfb/config.py` and applies to both
stdout and SQLite handlers.

Current toggles:
-- `log_component_transport_dns` (default: false) - enable or disable DNS
  transport logs and events (`dns.*`).
- `log_component_transport_icmp` (default: false) - enable or disable ICMP
  transport logs.
- `log_component_tunnel` (default: true) - enable or disable tunnel logs and
  events (`tunnel.*`).
- `log_component_channel` (default: false) - enable or disable channel logs and
  events (`channel.*`).
- `log_component_protocol` (default: false) - enable or disable protocol logs.
- `log_component_module_socks` (default: true) - enable or disable SOCKS module
  logs and events (`sock.*`).
- `log_component_module_file_transfer` (default: true) - enable or disable file
  transfer module logs.

Set the default in `sfb/config.py`, or pass to `Config(...)` when embedding.
Logger names are standardized under the `sfb.*` namespace for consistency.

## Log Profiles

For repeatable troubleshooting setups, you can apply a named logging profile:

```
python3 -m sfb.cli --log-profile tunnel_verbose ...
```

Profiles live in `sfb/log_profiles.py`. You can add new profiles there to
toggle `log_component_*` flags or override `log_event_whitelist` and
`log_event_blacklist` settings.

Available profiles (current):
- `icmp_retransmit_debug` (default)
- `icmp_transport`
- `dns_transport`
- `tunnel_verbose`

## Event Whitelist/Blacklist (structured events only)

You can filter structured events (those emitted via `log_event`) by pattern:
- `log_event_whitelist`: tuple of wildcard patterns to allow.
- `log_event_blacklist`: tuple of wildcard patterns to deny.

Matching uses `fnmatch` wildcards (for example, `tunnel.mtu*`).
If the whitelist is non-empty, only matching events are emitted.
Blacklist is applied after whitelist.
Plain logger messages without an `event` field are not affected.
Records at ERROR or higher always pass filtering.

Default blacklist (to reduce high-volume debug events):
- `tunnel.packet_*`
- `tunnel.ack`
- `tunnel.send_blocked`
- `tunnel.recv_window`
- `tunnel.deliver_segments`
- `tunnel.control_dispatch`
- `tunnel.control_processed`
- `tunnel.command`
- `module.send`
- `module.recv`
- `sock.pump_stats`
- `channel.drain`
- `channel.pack`
- `channel.send_buf_*`
- `channel.write_wait`
- `dns.send`
- `dns.recv`
- `icmp.send`
- `icmp.recv`

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
- `tunnel.init`
- `tunnel.wait`
- `tunnel.connected`
- `tunnel.handshake_attempt`
- `tunnel.handshake_synack_sent`
- `tunnel.handshake_error`
- `tunnel.handshake_timeout`
- `tunnel.idle_timeout`
- `tunnel.packet_decode_failed`
- `tunnel.request_state_unexpected`
- `tunnel.send_window_inconsistent`
- `tunnel.send_window_full`
- `tunnel.ack_send_failed`
- `tunnel.timeout_packets`
- `tunnel.tick_error`
- `tunnel.bg_error`
- `tunnel.closed`
- `tunnel.message_type_allowed`
- `tunnel.message_type_rejected`
- `tunnel.serve_error`
- `tunnel.packet_send`
- `tunnel.packet_recv`
- `tunnel.retransmit`
- `tunnel.retransmit_skip`
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
- `channel.unknown_segment`
- `channel.send_buf_full`
- `channel.send_buf_high`
- `channel.pack`
- `sock.connect`
- `sock.connect_ok`
- `sock.connect_err`
- `dns.send`
- `dns.recv`
- `dns.send_empty` (reason: `qtype_mismatch`, `decode_failed`)
- `dns.cname_followup`
- `dns.cname_invalid_addr`
- `dns.error_response`
- `dns.malformed_response`
- `dns.stale_response`
- `dns.mismatched_response`
- `dns.send_blocked`
- `dns.prune_stale`
- `tunnel.send_blocked`
- `tunnel.send_window_distance`

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
