# Application Modules

Modules sit above the tunnel and use channels to provide services. Each module
registers a message type for its control messages (see `doc/CONTROL_MESSAGES.md`).

**Latency note**: Both Alice and Bob can initiate tunnel-level operations (open
channels, send data, transfer files). However, Bob's transmissions only go out
when Alice polls. In idle mode (1-5s polling), Bob-initiated operations have
additional latency equal to the polling interval. Once active (100ms polling),
this latency is minimal.

---

## SOCKS Proxy Module

**Message type**: `sock`

### Overview

Bob exposes a local SOCKS5 server. Connections are relayed through Alice to their destinations.

```
User App ──▶ Bob SOCKS ══════▶ Alice Relay ──▶ Target
            (localhost:1080)    (tunnel)       (internet)
```

### Components

**Bob: SOCKS Server**
- Listens on local port (default 1080)
- Handles SOCKS5 handshake directly
- For CONNECT requests: opens channel via `t=ch` messages
- Relays data between SOCKS client and channel

**Alice: Relay**
- Receives channel open request
- Connects to target address
- Relays data between TCP socket and channel

### Flow

```
User                Bob                    Alice              Target
 │                   │                       │                   │
 │── SOCKS CONNECT ─▶│                       │                   │
 │                   │── {t:ch,c:open,...} ─▶│                   │
 │                   │                       │── TCP CONNECT ───▶│
 │                   │◀── {t:ch,c:open_ok} ──│                   │
 │◀── SOCKS OK ──────│                       │                   │
 │                   │                       │                   │
 │── data ──────────▶│══ ch2 data ══════════▶│── data ──────────▶│
 │◀── data ──────────│◀═ ch2 data ═══════════│◀── data ──────────│
 │                   │                       │                   │
 │── close ─────────▶│── {t:ch,c:close} ────▶│── close ─────────▶│
 │                   │◀── {t:ch,c:close_ok} ─│                   │
```

### SOCKS5 Subset

Supported:
- Auth method: 0x00 (no auth)
- Command: 0x01 (CONNECT)
- Address types: IPv4, IPv6, domain

Not supported (v1):
- BIND
- UDP ASSOCIATE
- Username/password auth

### Control Messages

The SOCKS module uses channel messages (`t=ch`) for connection management:

```json
{"t":"ch","c":"open","ch":2,"atype":"ipv4","addr":"93.184.216.34","port":80}
{"t":"ch","c":"open","ch":2,"atype":"domain","addr":"example.com","port":443}
{"t":"ch","c":"open_ok","ch":2}
{"t":"ch","c":"open_fail","ch":2,"reason":"connection refused"}
{"t":"ch","c":"close","ch":2}
{"t":"ch","c":"close_ok","ch":2}
```

---

## File Transfer Module

**Message type**: `file`

See `doc/FILE_TRANSFER.md` for the complete specification.

### Overview

Transfer files between Alice and Bob over a dedicated channel. Either side can
initiate file operations:
- Bob initiates: request files from Alice's filesystem, or push files to Alice
- Alice initiates: request files from Bob's filesystem, or push files to Bob

### Commands

```json
{"t":"file","c":"list","path":"/home/user"}
{"t":"file","c":"list_ok","files":[{"name":"a.txt","size":1024,"dir":false}]}

{"t":"file","c":"get","ch":4,"path":"/home/user/a.txt"}
{"t":"file","c":"get_ok","ch":4,"size":1024}

{"t":"file","c":"put","ch":4,"path":"/tmp/b.txt","size":2048}
{"t":"file","c":"put_ok","ch":4}

{"t":"file","c":"err","ch":4,"reason":"not found"}
```

After `get_ok`, data flows on the specified channel until `size` bytes are
received. After `put_ok`, data flows on the specified channel until `size`
bytes are sent.

### Considerations

- Large files: chunked across many tunnel packets
- Progress: track bytes transferred vs size
- Resume: possible future enhancement (offset in get/put)

---

## Shell Module (Future)

**Message type**: `sh`

Interactive shell over tunnel.

### Design Options

1. **PTY forwarding**: Alice spawns shell with PTY, Bob sends/receives terminal data
2. **Command/response**: Bob sends commands, Alice executes, returns output

PTY forwarding is more flexible but more complex.

### Control Messages

```json
{"t":"sh","c":"open","ch":6,"rows":24,"cols":80}
{"t":"sh","c":"open_ok","ch":6}
{"t":"sh","c":"resize","ch":6,"rows":30,"cols":120}
{"t":"sh","c":"close","ch":6}
```

Data on channel is raw terminal I/O.

---

## Writing New Modules

1. Choose a message type code (2-4 chars, e.g., `mymod`)
2. Register your module's message handler with the tunnel
3. Define your control messages following the pattern:
   ```json
   {"t":"mymod","c":"start","ch":N,...}
   {"t":"mymod","c":"start_ok","ch":N}
   {"t":"mymod","c":"err","ch":N,"reason":"..."}
   ```
4. Open channels as needed using `t=ch` messages
5. Read/write channel data

### Module Handler Interface

```python
class MyModule:
    TYPE = 'mymod'  # Message type code

    def __init__(self, tunnel):
        tunnel.register_module(self.TYPE, self.handle_message)

    def handle_message(self, msg):
        cmd = msg.get('c')
        if cmd == 'start':
            self._handle_start(msg)
        elif cmd == 'start_ok':
            self._handle_start_ok(msg)
        # ...
```

Modules are independent—multiple can run simultaneously using different channels.
