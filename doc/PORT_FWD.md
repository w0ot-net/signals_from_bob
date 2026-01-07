# Port Forward Module

## Overview

The port forward module provides TCP port forwarding over the tunnel. Bob
listens on a local TCP address and forwards each connection to a fixed remote
host:port on Alice. Data flows over a dedicated channel per connection.

Message type: `fwd`

## Usage

### Bob (server)

```
python3 -m sfb.cli --role bob --transport dns --domain t.example.com \
  --module port_fwd_server start --local 127.0.0.1:8080 --remote 10.0.0.5:22
```

### Alice (relay)

```
python3 -m sfb.cli --role alice --transport dns --domain t.example.com \
  --module port_fwd_relay
```

## Flow

```
Client              Bob                    Alice              Target
 │                   │                       │                   │
 │── TCP CONNECT ───▶│                       │                   │
 │                   │── {t:ch,c:open} ─────▶│                   │
 │                   │◀── {t:ch,c:open_ok} ──│                   │
 │                   │── {t:fwd,c:connect,mid:1} ▶│              │
 │                   │                       │── TCP CONNECT ───▶│
 │                   │◀── {t:fwd,c:connect_ok,mid:1}             │
 │                   │                       │                   │
 │── data ──────────▶│══ ch data ═══════════▶│── data ──────────▶│
 │◀── data ──────────│◀═ ch data ════════════│◀── data ──────────│
 │                   │                       │                   │
 │── close ─────────▶│── {t:ch,c:close} ────▶│── close ─────────▶│
 │                   │◀── {t:ch,c:close_ok} ─│                   │
```

## Control Messages

```json
{"t":"fwd","c":"connect","mid":1,"rid":1,"ch":2,"host":"example.com","port":443}
{"t":"fwd","c":"connect_ok","mid":1,"rid":1,"ch":2}
{"t":"fwd","c":"err","mid":1,"rid":1,"ch":2,"code":"refused","reason":"connection refused"}
```

`connect_ok` may include `bhost`/`bport` for the bound address on Alice.

## Configuration

Port forwarding uses the shared relay settings from `sfb/config.py`:

- `relay_listen_backlog`
- `relay_accept_timeout`
- `relay_channel_open_timeout`
- `relay_connect_timeout`
- `relay_target_connect_timeout`
- `relay_socket_timeout`
- `relay_channel_timeout`
- `relay_write_timeout`
- `relay_buffer_size`
- `relay_pump_poll_timeout`
- `relay_pump_backoff_max`
- `relay_thread_join_timeout`

Port forward does not define additional tuning knobs.

## Logging

Events use the `fwd.*` prefix and are controlled by
`log_component_module_relay` in `sfb/config.py`.

## Limitations

- TCP only
- IPv4 only (IPv6 unsupported)
- One remote host:port per module instance
