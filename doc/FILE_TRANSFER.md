# File Transfer Module

This document specifies the file transfer module that runs over tunnel
channels. Either side (Alice or Bob) can initiate operations.

---

## Overview

- File operations are coordinated via control messages on channel 0.
- File data is sent over a dedicated data channel identified in the command.
- One file transfer uses one data channel at a time.

---

## Control Messages

All control messages are JSON objects encoded in ASCII, one per line, terminated
with a newline (`\n`).

### List Directory

Request a directory listing.

```json
{"cmd":"file_list","path":"/home/user"}
```

Response:

```json
{"cmd":"file_list_ok","files":[{"name":"a.txt","size":1024,"dir":false}]}
```

If the request fails:

```json
{"cmd":"file_err","reason":"not found"}
```

### Download (file_get)

Request to receive a file from the peer.

```json
{"cmd":"file_get","ch":4,"path":"/home/user/a.txt"}
```

Response on success:

```json
{"cmd":"file_get_ok","ch":4,"size":1024}
```

Response on failure:

```json
{"cmd":"file_err","ch":4,"reason":"not found"}
```

After `file_get_ok`, the sender transmits exactly `size` bytes on channel `ch`.
The receiver reads until `size` bytes are received, then closes the channel.

### Upload (file_put)

Request to send a file to the peer.

```json
{"cmd":"file_put","ch":4,"path":"/tmp/b.txt","size":2048}
```

Response on success:

```json
{"cmd":"file_put_ok","ch":4}
```

Response on failure:

```json
{"cmd":"file_err","ch":4,"reason":"permission denied"}
```

After `file_put_ok`, the sender transmits exactly `size` bytes on channel `ch`.
The receiver reads until `size` bytes are received, then closes the channel.

---

## Data Channel Rules

- `ch` must follow the even/odd convention (Alice opens odd, Bob opens even).
- The **command initiator** opens the channel and includes `ch` in the request.
  For `file_get`, the initiator requests data and opens the channel; the peer
  sends data on that channel. For `file_put`, the initiator opens the channel
  and sends data on it.
- The data channel carries raw file bytes, no framing.
- The receiver relies on the announced `size` to know when the transfer ends.

---

## Transfer Flow

Example: Bob downloads a file from Alice (Bob initiates, Alice sends data).

Bob opens channel 4 (even = Bob's channel) and requests the file. Alice sends
the file data on channel 4.

```
Bob                                 Alice
 │                                     │
 │── file_get {ch:4,path:/x} ─────────▶│  Bob opens ch:4, requests file
 │◀─ file_get_ok {ch:4,size:N} ────────│  Alice confirms
 │◀═ channel 4: N bytes ═══════════════│  Alice sends data TO Bob
 │                                     │
```

Example: Bob uploads a file to Alice (Bob initiates, Bob sends data).

Bob opens channel 4 and sends the file data on it.

```
Bob                                 Alice
 │                                     │
 │── file_put {ch:4,path:/y,size:N} ──▶│  Bob opens ch:4, announces upload
 │◀─ file_put_ok {ch:4} ───────────────│  Alice confirms
 │═▶ channel 4: N bytes ═══════════════│  Bob sends data TO Alice
 │                                     │
```

Example: Alice downloads a file from Bob (Alice initiates, Bob sends data).

Alice opens channel 3 (odd = Alice's channel) and requests the file. Bob sends
the file data on channel 3.

```
Alice                               Bob
 │                                     │
 │── file_get {ch:3,path:/x} ─────────▶│  Alice opens ch:3, requests file
 │◀─ file_get_ok {ch:3,size:N} ────────│  Bob confirms
 │◀═ channel 3: N bytes ═══════════════│  Bob sends data TO Alice
 │                                     │
```

---

## Errors and Edge Cases

- If a `file_err` is returned, the initiator should close the channel (if open)
  and report the error to the user.
- If the sender cannot read the file after `file_get_ok`, it should close the
  channel and send `file_err` on channel 0.
- If the receiver gets fewer than `size` bytes before disconnect, treat the
  transfer as failed.

---

## Security Notes

- File paths are interpreted on the receiver's filesystem.
- Implementations should validate and normalize paths to avoid unsafe access.

---

## Future Extensions

- Resume support via byte offsets.
- Directory recursion for bulk transfers.
