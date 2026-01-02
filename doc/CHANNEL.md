# Channel

The channel layer provides logical, bidirectional byte streams on top of the
tunnel. Channels are multiplexed into tunnel packets by the muxer and carried
over the reliability and transport layers.

## Channel IDs

- Channel 0 is reserved for control messages and is always open.
- Alice opens odd-numbered channels (1, 3, 5...).
- Bob opens even-numbered channels (2, 4, 6...).

The odd/even convention prevents collisions when both sides open channels.

Channel IDs are 8-bit (0-255). After reaching 255, IDs wrap around. The
allocator skips IDs that are still in use. If all IDs in a side's range are
exhausted, channel allocation fails.

## State Machine

Channels follow a simple state machine:

```
INIT -> OPENING -> OPEN -> CLOSING -> CLOSED
```

- INIT: channel object created, no OPEN sent yet.
- OPENING: OPEN sent, waiting for OPEN_OK/OPEN_FAIL.
- OPEN: data may flow in both directions.
- CLOSING: CLOSE pending or sent, waiting for CLOSE_OK.
- CLOSED: channel fully closed, no further data.

Control messages are sent on channel 0 to drive OPEN/CLOSE transitions.

If `close()` is called while in INIT state, the channel transitions directly to
CLOSED without sending a CLOSE message (the channel was never opened). If
`close()` is called while in OPENING or OPEN state, the channel transitions to
CLOSING and CLOSE is sent after the send buffer drains. Any pending OPEN request
may still receive OPEN_OK/OPEN_FAIL from the peer, which is ignored.

## Data Flow

- `write(data)` queues bytes for transmission. The muxer drains queued bytes
  into outgoing packets. Returns the number of bytes actually queued, which may
  be less than requested if the send buffer has limited space remaining.
- `_deliver(data)` is called by the muxer to deliver incoming bytes into the
  channel receive buffer. Data is silently discarded if the channel is not in
  OPEN or CLOSING state (e.g., INIT or OPENING).
- `read(size, timeout=None)` blocks (or times out) until data is available,
  returning up to `size` bytes. On timeout it returns `None`. If called on a
  channel in INIT or OPENING state, it blocks until the channel opens or closes.

### ControlChannel

Channel 0 carries control messages encoded as compact, single-line JSON,
ASCII-encoded and newline terminated. `ControlChannel` wraps a channel-0
instance with helpers:

- `send_message(obj)` encodes JSON and appends `\n`.
- `recv_message(timeout=None)` returns a decoded object, or `None` on timeout
  or clean close. Raises `ChannelError` on invalid JSON or partial message at
  close. Note: the timeout applies to each internal `read()` call, not the
  entire message assembly—partial data may accumulate across timeout periods.

The tunnel layer should catch `ChannelError` from `recv_message()`, log the
error, and continue—invalid control messages are dropped, not fatal.

## Buffering

- Send data is buffered in a FIFO deque up to `max_send_buf`.
- Receive data is buffered in a FIFO deque up to `max_recv_buf` (default 64k,
  per channel).
- If inbound data would exceed `max_recv_buf`, the channel aborts with error
  code `recv_overflow`, buffered data is discarded, and subsequent inbound
  data for that channel is dropped.

## Errors and Close Semantics

- `write` raises `ChannelError` if the channel is not open. If the send buffer
  is completely full, `write` also raises `ChannelError`. If the buffer has
  partial space, `write` queues as much as fits and returns the byte count.
- `close()` is graceful: it stops new writes, drains queued send data, then
  sends CLOSE. The channel closes after CLOSE_OK is received.
- `abort(code, reason)` is immediate: queued send/recv data is dropped, the
  channel closes locally, and a `close_err` control message is sent with the
  error code and reason.
- On receiving CLOSE, the channel closes and any later in-flight data is
  dropped.
- `read` returns any buffered data first after a clean close. Once the buffer
  is empty and the channel is cleanly closed, `read` returns `b''`.
- `read` returns `None` on timeout (no data available within the timeout).
- If a channel is closed with an error (local abort, `close_err`, or receive
  overflow), `read` raises `ChannelError` with the error code and message.

## Thread Safety

Channel operations use a lock and events to support multi-threaded access.
The muxer and application may operate concurrently.
