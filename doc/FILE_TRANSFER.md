# File Transfer Module

This document specifies the file transfer module that runs over tunnel
channels. Either side (Alice or Bob) can initiate operations.

**Message type**: `file` (see `doc/CONTROL_MESSAGES.md`)

---

## Overview

- File operations are coordinated via control messages on channel 0.
- File data is sent over a dedicated data channel identified in the command.
- One file transfer uses one data channel at a time.

---

## Control Messages

All file transfer messages use `t="file"`. Commands are:

| Command | Description |
|---------|-------------|
| `list` | Request directory listing |
| `list_ok` | Directory listing response |
| `get` | Request file download |
| `get_ok` | File download confirmed |
| `put` | Request file upload |
| `put_ok` | File upload confirmed |
| `err` | Error response |

All messages include a request ID (`rid`) to correlate requests and responses.

### List Directory

Request a directory listing.

```json
{"t":"file","c":"list","rid":1,"path":"/home/user"}
```

Response:

```json
{"t":"file","c":"list_ok","rid":1,"files":[{"name":"a.txt","size":1024,"dir":false}]}
```

If the request fails:

```json
{"t":"file","c":"err","rid":1,"code":"not_found","reason":"not found"}
```

### Download (get)

Request to receive a file from the peer.

```json
{"t":"file","c":"get","rid":2,"ch":4,"path":"/home/user/a.txt"}
```

Response on success:

```json
{"t":"file","c":"get_ok","rid":2,"ch":4,"size":1024}
```

Response on failure:

```json
{"t":"file","c":"err","rid":2,"ch":4,"code":"not_found","reason":"not found"}
```

After `get_ok`, the sender transmits exactly `size` bytes on channel `ch`.
The receiver reads until `size` bytes are received, then closes the channel.

### Upload (put)

Request to send a file to the peer.

```json
{"t":"file","c":"put","rid":3,"ch":4,"path":"/tmp/b.txt","size":2048}
```

Response on success:

```json
{"t":"file","c":"put_ok","rid":3,"ch":4}
```

Response on failure:

```json
{"t":"file","c":"err","rid":3,"ch":4,"code":"perm","reason":"permission denied"}
```

After `put_ok`, the sender transmits exactly `size` bytes on channel `ch`.
The receiver reads until `size` bytes are received, then closes the channel.

---

## Data Channel Rules

- `ch` must follow the even/odd convention (Alice opens odd, Bob opens even).
- The **command initiator** opens the channel and includes `ch` in the request.
  For `get`, the initiator requests data and opens the channel; the peer
  sends data on that channel. For `put`, the initiator opens the channel
  and sends data on it.
- The data channel carries raw file bytes, no framing.
- The receiver relies on the announced `size` to know when the transfer ends.
- The sender closes the channel after transmitting `size` bytes. The receiver
  closes after reading `size` bytes or on error.

Only one file transfer is active at a time. If a new request arrives while a
transfer is in progress, respond with `err` and `code="busy"`.

---

## Transfer Flow

Example: Bob downloads a file from Alice (Bob initiates, Alice sends data).

Bob opens channel 4 (even = Bob's channel) and requests the file. Alice sends
the file data on channel 4.

```
Bob                                 Alice
 │                                     │
 │── {t:file,c:get,ch:4,path:/x} ─────▶│  Bob opens ch:4, requests file
 │◀─ {t:file,c:get_ok,ch:4,size:N} ────│  Alice confirms
 │◀═ channel 4: N bytes ═══════════════│  Alice sends data TO Bob
 │                                     │
```

Example: Bob uploads a file to Alice (Bob initiates, Bob sends data).

Bob opens channel 4 and sends the file data on it.

```
Bob                                 Alice
 │                                     │
 │── {t:file,c:put,ch:4,path:/y,...} ─▶│  Bob opens ch:4, announces upload
 │◀─ {t:file,c:put_ok,ch:4} ───────────│  Alice confirms
 │═▶ channel 4: N bytes ═══════════════│  Bob sends data TO Alice
 │                                     │
```

Example: Alice downloads a file from Bob (Alice initiates, Bob sends data).

Alice opens channel 3 (odd = Alice's channel) and requests the file. Bob sends
the file data on channel 3.

```
Alice                               Bob
 │                                     │
 │── {t:file,c:get,ch:3,path:/x} ─────▶│  Alice opens ch:3, requests file
 │◀─ {t:file,c:get_ok,ch:3,size:N} ────│  Bob confirms
 │◀═ channel 3: N bytes ═══════════════│  Bob sends data TO Alice
 │                                     │
```

---

## Errors and Edge Cases

- If an `err` is returned, the initiator should close the channel (if open)
  and report the error to the user.
- If the sender cannot read the file after `get_ok`, it should close the
  channel and send `err` on channel 0.
- If the receiver gets fewer than `size` bytes before disconnect, treat the
  transfer as failed.
- Implementations should write uploads to a temporary file and rename on
  success. On failure, remove the partial file and send `err`.

### Error Codes

`err` messages include a `code` field:

| Code | Meaning |
|------|---------|
| `not_found` | File or directory does not exist |
| `perm` | Permission denied |
| `too_large` | File exceeds configured limit |
| `busy` | Another transfer is in progress |
| `io` | Generic I/O error |

---

## Path Rules

- `path` is interpreted on the receiver's filesystem.
- Paths are resolved using standard OS path normalization (`os.path.abspath`).
- Both absolute paths (e.g., `/etc/passwd`, `C:\Windows`) and relative paths are
  supported. Relative paths resolve from the current working directory.
- Windows: accept `C:\\path` and `C:/path`, normalize to native separators.

---

## Future Extensions

- Resume support via byte offsets.
- Directory recursion for bulk transfers.
