# Application Modules

Modules sit above the tunnel and use channels to provide services.

**Latency note**: Both Alice and Bob can initiate tunnel-level operations (open
channels, send data, transfer files). However, Bob's transmissions only go out
when Alice polls. In idle mode (1-5s polling), Bob-initiated operations have
additional latency equal to the polling interval. Once active (100ms polling),
this latency is minimal.

---

## SOCKS Proxy Module

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
- For CONNECT requests: opens channel, sends OPEN via control
- Relays data between SOCKS client and channel

**Alice: Relay**
- Receives OPEN on control channel
- Connects to target address
- Relays data between TCP socket and channel

### Flow

```
User                Bob                    Alice              Target
 │                   │                       │                   │
 │── SOCKS CONNECT ─▶│                       │                   │
 │                   │── OPEN {ch:2,addr} ──▶│                   │
 │                   │                       │── TCP CONNECT ───▶│
 │                   │◀───── OPEN_OK ────────│                   │
 │◀── SOCKS OK ──────│                       │                   │
 │                   │                       │                   │
 │── data ──────────▶│══ ch2 data ══════════▶│── data ──────────▶│
 │◀── data ──────────│◀═ ch2 data ═══════════│◀── data ──────────│
 │                   │                       │                   │
 │── close ─────────▶│── CLOSE {ch:2} ──────▶│── close ─────────▶│
 │                   │◀──── CLOSE_OK ────────│                   │
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

```json
{"cmd":"open","ch":2,"atype":"ipv4","addr":"93.184.216.34","port":80}
{"cmd":"open","ch":2,"atype":"domain","addr":"example.com","port":443}
{"cmd":"open_ok","ch":2}
{"cmd":"open_fail","ch":2,"reason":"connection refused"}
{"cmd":"close","ch":2}
{"cmd":"close_ok","ch":2}
```

---

## File Transfer Module

### Overview

Transfer files between Alice and Bob over a dedicated channel. Either side can
initiate file operations:
- Bob initiates: request files from Alice's filesystem, or push files to Alice
- Alice initiates: request files from Bob's filesystem, or push files to Bob

### Commands

```json
{"cmd":"file_list","path":"/home/user"}
{"cmd":"file_list_ok","files":[{"name":"a.txt","size":1024,"dir":false}]}

{"cmd":"file_get","ch":4,"path":"/home/user/a.txt"}
{"cmd":"file_get_ok","ch":4,"size":1024}

{"cmd":"file_put","ch":4,"path":"/tmp/b.txt","size":2048}
{"cmd":"file_put_ok","ch":4}

{"cmd":"file_err","ch":4,"reason":"not found"}
```

After `file_get_ok`, data flows on the specified channel until `size` bytes are
received. After `file_put_ok`, data flows on the specified channel until `size`
bytes are sent.

### Considerations

- Large files: chunked across many tunnel packets
- Progress: track bytes transferred vs size
- Resume: possible future enhancement (offset in get/put)

---

## Shell Module (Future)

Interactive shell over tunnel.

### Design Options

1. **PTY forwarding**: Alice spawns shell with PTY, Bob sends/receives terminal data
2. **Command/response**: Bob sends commands, Alice executes, returns output

PTY forwarding is more flexible but more complex.

### Control Messages

```json
{"cmd":"shell_open","ch":6,"rows":24,"cols":80}
{"cmd":"shell_ok","ch":6}
{"cmd":"shell_resize","ch":6,"rows":30,"cols":120}
{"cmd":"shell_close","ch":6}
```

Data on channel is raw terminal I/O.

---

## Writing New Modules

1. Create `modules/mymodule.py`
2. Get reference to muxer
3. Open channels as needed
4. Send control messages for coordination
5. Read/write channel data

Modules are independent—multiple can run simultaneously using different channels.
