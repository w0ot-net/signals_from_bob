# SOCKS5 Module

The SOCKS module provides a SOCKS5 proxy interface for tunneling TCP connections through the tunnel. Either endpoint can run the SOCKS server; the peer acts as the relay.

## Typical Topology

```
    BOB (outside)                                    ALICE (DMZ)
         │                                                │
  ┌──────┴──────┐                                  ┌──────┴──────┐
  │   Browser   │                                  │   Target    │
  │  (SOCKS5)   │                                  │   Server    │
  └──────┬──────┘                                  └──────▲──────┘
         │ SOCKS5                                         │ TCP
         ▼                                                │
  ┌─────────────┐         Covert Channel          ┌───────┴─────┐
  │    SOCKS    │◀═══════════════════════════════▶│    SOCKS    │
  │   Server    │           (DNS/HTTP)            │    Relay    │
  │   Module    │                                 │    Module   │
  └──────┬──────┘                                 └─────────────┘
         │
  ┌──────┴──────┐
  │   Tunnel    │
  │    (Bob)    │
  └─────────────┘
```

**Bob** runs the SOCKS server. Local applications connect to it.
**Alice** runs the relay. She makes TCP connections to targets in her network.

The roles are symmetric - Alice could run the SOCKS server if Bob had the interesting network access.

## Protocol Layers

The SOCKS module uses two protocol layers:

1. **Channel layer** (`ch/*`) - Opens/closes data pipes, knows nothing about SOCKS
2. **SOCKS layer** (`sock/*`) - Negotiates connections, carries target information

### Channel Messages (Generic)

Channels are just bidirectional byte streams:

```json
{"t":"ch","c":"open","ch":1}
{"t":"ch","c":"open_ok","ch":1}
{"t":"ch","c":"close","ch":1}
{"t":"ch","c":"close_ok","ch":1}
```

No application-specific data. The channel layer only handles:
- Channel ID allocation
- Channel lifecycle
- Segment routing

### SOCKS Messages (Application)

The SOCKS module uses its own message type for connection negotiation:

```json
{"t":"sock","c":"connect","ch":1,"atype":"ipv4","addr":"10.0.0.5","port":443}
{"t":"sock","c":"connect","ch":3,"atype":"domain","addr":"internal.corp","port":80}
{"t":"sock","c":"connect_ok","ch":1}
{"t":"sock","c":"connect_fail","ch":1,"err":"refused"}
```

### Connection Flow

1. Server opens channel: `{"t":"ch","c":"open","ch":1}`
2. Relay accepts channel: `{"t":"ch","c":"open_ok","ch":1}`
3. Server sends target info: `{"t":"sock","c":"connect","ch":1,"atype":"...","addr":"...","port":...}`
4. Relay makes TCP connection to target
5. Relay responds: `{"t":"sock","c":"connect_ok","ch":1}` or `{"t":"sock","c":"connect_fail","ch":1,"err":"..."}`
6. On success: data flows bidirectionally on channel 1
7. On failure: server closes channel with `{"t":"ch","c":"close","ch":1}`

### Error Codes

| Code | SOCKS5 REP | Description |
|------|------------|-------------|
| `"refused"` | 0x05 | Connection refused |
| `"unreachable"` | 0x04 | Host unreachable |
| `"timeout"` | 0x04 | Connection timeout |
| `"dns"` | 0x04 | DNS resolution failed |
| `"forbidden"` | 0x02 | Not allowed by ruleset |
| `"error"` | 0x01 | General failure |

## SOCKS5 Protocol

### Phase 1: Authentication

```
Client → Server:  VER=5, NMETHODS, METHODS[]
Server → Client:  VER=5, METHOD
```

Supported methods:
- `0x00` - No authentication

### Phase 2: Connect Request

```
Client → Server:  VER=5, CMD, RSV, ATYPE, DST.ADDR, DST.PORT
Server → Client:  VER=5, REP, RSV, ATYPE, BND.ADDR, BND.PORT
```

Only CMD=0x01 (CONNECT) is supported.

### Phase 3: Data Relay

Bidirectional byte stream between client socket and tunnel channel.

## Implementation

### SocksServer Class

Runs on the side that wants to proxy connections (typically Bob).

```python
class SocksServer:
    """SOCKS5 server - accepts local connections, tunnels to relay."""

    MSG_TYPE = 'sock'

    def __init__(self, tunnel, listen_addr='127.0.0.1', port=1080):
        self._tunnel = tunnel
        self._listen_addr = listen_addr
        self._port = port
        self._server_sock = None

        # Active connections: channel_id -> client_socket
        self._clients = {}

        # Pending connections: channel_id -> (client_socket, target_info)
        self._pending = {}

        # Register SOCKS message handler
        tunnel.register_module(self.MSG_TYPE, self._handle_sock_message)

    def start(self):
        """Start listening for SOCKS clients."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._listen_addr, self._port))
        self._server_sock.listen(16)

    def handle_client(self, client_sock):
        """Handle a SOCKS5 client connection."""
        # 1. Auth negotiation
        if not self._negotiate_auth(client_sock):
            client_sock.close()
            return

        # 2. Read CONNECT request
        atype, addr, port = self._read_connect_request(client_sock)
        if atype is None:
            client_sock.close()
            return

        # 3. Open tunnel channel (no target info - channels are generic)
        channel = self._tunnel.channel_manager.open_channel()

        # 4. Wait for channel to open
        if not channel.wait_open(timeout=30.0):
            self._send_reply(client_sock, REP_GENERAL_FAILURE)
            client_sock.close()
            return

        # 5. Send SOCKS connect request over control channel
        self._pending[channel.id] = (client_sock, (atype, addr, port))
        self._send_sock_connect(channel.id, atype, addr, port)
        # Response handled in _handle_sock_message

    def _handle_sock_message(self, msg):
        """Handle incoming sock/* messages."""
        cmd = msg.get('c')
        if cmd == 'connect_ok':
            self._handle_connect_ok(msg)
        elif cmd == 'connect_fail':
            self._handle_connect_fail(msg)

    def _handle_connect_ok(self, msg):
        """Relay successfully connected to target."""
        channel_id = msg.get('ch')
        pending = self._pending.pop(channel_id, None)
        if pending is None:
            return

        client_sock, _ = pending
        self._send_reply(client_sock, REP_SUCCEEDED)
        self._clients[channel_id] = client_sock

    def _handle_connect_fail(self, msg):
        """Relay failed to connect to target."""
        channel_id = msg.get('ch')
        err = msg.get('err', 'error')
        pending = self._pending.pop(channel_id, None)
        if pending is None:
            return

        client_sock, _ = pending
        self._send_reply(client_sock, self._error_to_rep(err))
        client_sock.close()

        # Close the channel since connection failed
        self._tunnel.channel_manager.close_channel(channel_id)

    def _send_sock_connect(self, channel_id, atype, addr, port):
        msg = {'t': 'sock', 'c': 'connect', 'ch': channel_id,
               'atype': atype, 'addr': addr, 'port': port}
        self._tunnel.control.send_message(msg)  # Serializes to compact JSON
```

### Relay Pumps

Each SOCKS connection spawns two threads: one for socket-to-channel and one for
channel-to-socket. After SOCKS negotiation/connect replies, sockets are switched
to non-blocking mode and the pumps use `select` for read/write readiness.
`socks_relay_socket_timeout` applies to handshake/connect; `socks_relay_write_timeout`
bounds stalled sends in the pumps.

### SocksRelay Class

Runs on the side with network access to targets (typically Alice).

```python
class SocksRelay:
    """SOCKS relay - connects to targets on behalf of the server."""

    MSG_TYPE = 'sock'

    def __init__(self, tunnel):
        self._tunnel = tunnel

        # Active connections: channel_id -> target_socket
        self._targets = {}

        # Register SOCKS message handler
        tunnel.register_module(self.MSG_TYPE, self._handle_sock_message)

    def _handle_sock_message(self, msg):
        """Handle incoming sock/* messages."""
        cmd = msg.get('c')
        if cmd == 'connect':
            self._handle_connect(msg)

    def _handle_connect(self, msg):
        """Server requests connection to target."""
        channel_id = msg.get('ch')
        atype = msg.get('atype')
        addr = msg.get('addr')
        port = msg.get('port')

        # Verify channel exists and is open
        channel = self._tunnel.channel_manager.get_channel(channel_id)
        if channel is None or not channel.is_open:
            self._send_connect_fail(channel_id, 'error')
            return

        # Attempt TCP connection to target
        try:
            if atype == 'ipv6':
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            sock.settimeout(10.0)

            # Resolve domain names
            if atype == 'domain':
                resolved = socket.gethostbyname(addr)
            else:
                resolved = addr

            sock.connect((resolved, port))
            sock.setblocking(False)

        except socket.gaierror:
            self._send_connect_fail(channel_id, 'dns')
            return
        except socket.timeout:
            self._send_connect_fail(channel_id, 'timeout')
            return
        except socket.error as e:
            err = 'refused' if getattr(e, 'errno', None) == errno.ECONNREFUSED else 'error'
            self._send_connect_fail(channel_id, err)
            return

        # Success
        self._targets[channel_id] = sock
        self._send_connect_ok(channel_id)

    def _send_connect_ok(self, channel_id):
        self._tunnel.control.send_message(
            {'t': 'sock', 'c': 'connect_ok', 'ch': channel_id}
        )

    def _send_connect_fail(self, channel_id, err):
        self._tunnel.control.send_message(
            {'t': 'sock', 'c': 'connect_fail', 'ch': channel_id, 'err': err}
        )

    def relay_tick(self):
        """Called periodically to relay data between channels and sockets."""
        for channel_id, sock in list(self._targets.items()):
            channel = self._tunnel.channel_manager.get_channel(channel_id)

            if channel is None or channel.is_closed:
                sock.close()
                del self._targets[channel_id]
                continue

            # Channel → Socket
            data = channel.read(4096, timeout=0)
            if data:
                try:
                    sock.sendall(data)
                except socket.error:
                    channel.close()
                    continue

            # Socket → Channel
            try:
                data = sock.recv(4096)
                if data:
                    channel.write(data)
                elif data == b'':
                    # Target closed connection
                    channel.close()
            except socket.error:
                pass  # No data available (non-blocking)
```

## Data Flow

### Connection Establishment

```
SOCKS Client      Bob (Server)              Tunnel              Alice (Relay)         Target
     │                 │                       │                      │                  │
     │──SOCKS AUTH────▶│                       │                      │                  │
     │◀────────────────│                       │                      │                  │
     │──SOCKS CONNECT─▶│                       │                      │                  │
     │                 │                       │                      │                  │
     │                 │───ch/open {ch:1}─────▶│                      │                  │
     │                 │                       │───ch/open {ch:1}────▶│                  │
     │                 │                       │◀──ch/open_ok {ch:1}──│                  │
     │                 │◀──wait_open() returns─│                      │                  │
     │                 │                       │                      │                  │
     │                 │───sock/connect───────▶│                      │                  │
     │                 │  {ch:1,addr,port}     │───sock/connect──────▶│                  │
     │                 │                       │                      │──TCP CONNECT────▶│
     │                 │                       │                      │◀─────────────────│
     │                 │                       │◀──sock/connect_ok────│                  │
     │                 │◀──sock/connect_ok─────│                      │                  │
     │◀─SOCKS SUCCESS──│                       │                      │                  │
```

### Data Relay

```
SOCKS Client      Bob (Server)              Tunnel              Alice (Relay)         Target
     │                 │                       │                      │                  │
     │──DATA──────────▶│                       │                      │                  │
     │                 │──channel.write───────▶│                      │                  │
     │                 │                       │══SEGMENT════════════▶│                  │
     │                 │                       │                      │──socket.send────▶│
     │                 │                       │                      │                  │
     │                 │                       │                      │◀──socket.recv────│
     │                 │                       │◀══SEGMENT════════════│                  │
     │                 │◀──channel.read────────│                      │                  │
     │◀──DATA──────────│                       │                      │                  │
```

## I/O Loop

The module needs to multiplex between:
- SOCKS server socket (accepting new clients)
- SOCKS client sockets (reading requests, relaying data)
- Tunnel channels (reading/writing data)
- Target sockets (relay side only)

Options:
- `select.select()` based loop
- `selectors` module (Python 3.4+)
- Thread per connection (simpler but heavier)

## Configuration

In the typical deployment, Bob runs the SOCKS server (exposing a local proxy
port) and Alice runs the relay (making outbound TCP connections).

The tunnel class names (`AliceTunnel`/`BobTunnel`) refer to the transport role,
not the SOCKS role. Alice initiates transport connections (DNS queries); Bob
responds to them. When Bob runs the SOCKS server, he uses `AliceTunnel` because
he initiates the DNS transport to reach Alice.

```python
# Bob (SOCKS server side)
# Uses AliceTunnel because Bob initiates DNS queries to reach Alice
tunnel = AliceTunnel(dns_client, crypto=...)
socks_server = SocksServer(tunnel, listen_addr='127.0.0.1', port=1080)
socks_server.start()

# Alice (SOCKS relay side)
# Uses BobTunnel because Alice receives and responds to DNS queries
tunnel = BobTunnel(dns_server, crypto=...)
socks_relay = SocksRelay(tunnel)
```

## Security Considerations

- **Localhost binding**: SOCKS server binds to 127.0.0.1 by default
- **DNS on relay side**: Domain resolution happens on relay (Alice), preventing DNS leaks
- **Target restrictions**: Relay could implement allow/deny lists for targets
- **No UDP**: Only TCP CONNECT is supported (no UDP ASSOCIATE)
