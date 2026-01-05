# NC Linux Module

The nc_linux module binds a tunnel channel to a Linux file descriptor on Alice.
Bob issues the bind request and pumps data between his local fd and the channel.
This module is linux-only and uses only the Python standard library.

## Usage

Run on Bob (server role) with explicit local and remote fd specs:

```
python3 sfb.py server --module nc_linux bind --local <spec> --remote <spec>
```

## FD Spec Format

The bind request accepts one of:

- Numeric fd (e.g., `3`)
- Path (e.g., `/tmp/data.txt`)
- TCP address (e.g., `1.1.1.1:443` or `[::1]:443`)

For paths, the file is opened read/write (created if missing). For addresses,
a TCP connection is established. The bound fd is closed when the channel closes.

## Control Messages

```json
{"t":"nc","c":"bind","rid":1,"ch":2,"fd":"/tmp/data.txt"}
{"t":"nc","c":"bind_ok","rid":1,"ch":2}
{"t":"nc","c":"err","rid":1,"ch":2,"code":"open_failed","reason":"..."}
```

## Errors

Common error codes:

- `not_linux`: non-linux platform
- `invalid_spec`: missing or unsupported fd spec
- `invalid_fd`: invalid numeric fd
- `channel_missing`: channel not found
- `channel_open_failed`: channel did not open
- `already_bound`: channel already bound
- `open_failed`: failed to open path
- `connect_failed`: failed to connect to host:port

Bind failures close the channel on both sides.
