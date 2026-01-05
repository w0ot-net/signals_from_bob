# Port Forward Module

## Overview
The port forward module listens on a Bob-side TCP address and forwards each
incoming connection to a fixed Alice-side target. Each connection uses its own
channel for bidirectional relay.

## CLI
Bob (server role) starts the listener and specifies the target:

```
python3 sfb.py --role server --module port_fwd \
  --local 127.0.0.1:8080 \
  --remote 10.0.0.5:22
```

Optional arguments:
- `--backlog N`: listen backlog
- `--timeout SECONDS`: connect timeout waiting for Alice

Alice (client role) runs without module args; Bob loads the module remotely.

### Address Format
- IPv4/DNS: `host:port`
- IPv6: `[::1]:port`

## Control Messages
Message type: `fwd`.

```json
{"t":"fwd","c":"connect","rid":1,"ch":2,"host":"10.0.0.5","port":22}
{"t":"fwd","c":"connect_ok","rid":1,"ch":2,"bhost":"10.0.0.1","bport":54321}
{"t":"fwd","c":"err","rid":1,"ch":2,"code":"refused","reason":"connection refused"}
```

## Flow

```
Client                Bob                           Alice             Target
  |                    |                              |                  |
  |--- TCP connect --->|                              |                  |
  |                    |--- {t:ch,c:open} ---------->|                  |
  |                    |<-- {t:ch,c:open_ok} --------|                  |
  |                    |--- {t:fwd,c:connect} ------>|                  |
  |                    |                              |--- TCP connect ->|
  |                    |<-- {t:fwd,c:connect_ok} ----|                  |
  |<== data ==========>|<== channel data ===========>|<== data =========>|
  |--- close ----------|--- {t:ch,c:close} --------->|--- close -------->|
```

## Relay Behavior
- EOF from the local TCP socket triggers `channel.close_write()`.
- EOF from the channel triggers a socket write shutdown.
- Errors or connection failures close the channel and local socket.

## Limitations
- TCP only.
- One fixed target per listener.
- Bob initiates connection requests; Alice only responds.
