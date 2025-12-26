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
 │                   │── {t:ch,c:open} ─────▶│                   │
 │                   │◀── {t:ch,c:open_ok} ──│                   │
 │                   │── {t:sock,c:connect} ▶│                   │
 │                   │                       │── TCP CONNECT ───▶│
 │                   │◀── {t:sock,c:connect_ok}                  │
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

The SOCKS module uses generic channel messages for the data pipe, then its own
message type (`sock`) for target negotiation:

```json
{"t":"ch","c":"open","ch":2}
{"t":"ch","c":"open_ok","ch":2}
{"t":"sock","c":"connect","ch":2,"atype":"ipv4","addr":"93.184.216.34","port":80}
{"t":"sock","c":"connect_ok","ch":2}
{"t":"sock","c":"connect_fail","ch":2,"err":"refused"}
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

Modules inherit from `BaseModule` which provides:
- Automatic registration with the tunnel
- Message dispatch to `handle_X` methods
- Threading support for blocking handlers
- Error handling and logging

### Basic Module

```python
from sfb.modules import BaseModule, blocking

class MyModule(BaseModule):
    TYPE = 'mymod'  # Message type code (2-4 chars)

    def __init__(self, tunnel):
        super(MyModule, self).__init__(tunnel)

    # Non-blocking handler (runs in tunnel thread)
    def handle_start_ok(self, msg):
        # Fast operations only
        pass

    # Blocking handler (runs in separate thread)
    @blocking
    def handle_work(self, msg):
        # Safe to do I/O here
        do_slow_operation()
        self.send_message({'t': 'mymod', 'c': 'work_ok'})
```

### Request-Response Pattern

For modules that need request-response with correlation IDs:

```python
from sfb.modules import BaseModule, RequestResponseMixin, blocking

class MyModule(RequestResponseMixin, BaseModule):
    TYPE = 'mymod'

    def __init__(self, tunnel):
        super(MyModule, self).__init__(tunnel)

    def do_request(self, param, timeout=10.0):
        """Public API - called by user."""
        rid = self._alloc_rid()
        pending = self._register_pending(rid)
        self.send_message({'t': 'mymod', 'c': 'req', 'rid': rid, 'param': param})
        response = self._wait_response(rid, pending, timeout)
        return response.get('result')

    def handle_req_ok(self, msg):
        """Response handler - signals waiter."""
        self._complete_pending(msg)

    @blocking
    def handle_req(self, msg):
        """Incoming request handler."""
        rid = msg.get('rid')
        result = process(msg.get('param'))
        self.send_message({'t': 'mymod', 'c': 'req_ok', 'rid': rid, 'result': result})
```

### Handler Naming

The dispatcher routes messages based on the `c` (command) field:
- `{"t":"mymod","c":"start"}` -> `handle_start(msg)`
- `{"t":"mymod","c":"start_ok"}` -> `handle_start_ok(msg)`
- `{"t":"mymod","c":"err"}` -> `handle_err(msg)`

Unhandled commands are logged and dropped.

### Threading Rules

- **Non-blocking handlers** run in the tunnel's thread. Keep them fast.
- **`@blocking` handlers** run in separate threads. Safe for I/O.
- `send_message()` is thread-safe.
- Channel operations (`read`/`write`) block and should use `@blocking`.

### Shutdown

Call `module.shutdown()` before destroying a module to wait for handler
threads to complete:

```python
module = MyModule(tunnel)
# ... use module ...
module.shutdown()
```

Modules are independent - multiple can run simultaneously using different channels.
