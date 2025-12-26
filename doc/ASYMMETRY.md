# Protocol Asymmetry

This document explains the fundamental asymmetry between Alice and Bob, and how
it affects reliability, flow control, and timeout behavior.

---

## The Core Constraint

**Alice initiates all transport-level connections. Bob cannot send a packet
unless Alice sends one first.**

For polling transports (DNS, ICMP):
- Alice sends queries; Bob can only respond to queries
- Bob's response must follow a just-received query
- If Alice stops polling, Bob cannot transmit anything

This is a hard network-level constraint. However, at the tunnel level, both
sides can initiate operations (open channels, send data, transfer files). Bob
just has latency waiting for the next poll to transmit his data.

---

## Retransmission

Both sides buffer unacked packets, but retransmission triggers differ:

| Aspect | Alice | Bob |
|--------|-------|-----|
| Trigger | Timer (RTO expires) | Opportunity (poll arrives) |
| Decision | Can choose *when* to retransmit | Retransmits when polled |
| RTT tracking | Yes | No |

**Alice (timer-driven):**
- Tracks round-trip time (RTT) using exponential moving average
- Computes retransmission timeout: `RTO = SRTT * 2`
- When RTO expires, retransmits oldest unacked packet
- Retransmits reuse existing sequence numbers; outstanding count stays capped
- Can act proactively based on time

**Bob (opportunity-driven):**
- Cannot act on timers; only transmits in response to polls
- On each poll: if unacked packets exist, retransmit oldest unacked packet
- Retransmits reuse existing sequence numbers; outstanding count stays capped
- Retransmits when the opportunity arises, not when a timer fires
- Does not track RTT (Alice's polling interval dominates latency)

---

## RTT and Timing

**Alice's RTT** is meaningful:
- Measures: send packet → receive ACK in Bob's response
- Reflects actual network round-trip time
- Used to compute retransmission timeout

**Bob's "RTT"** would be meaningless:
- Would measure: send response → receive ACK in Alice's next query
- Dominated by Alice's polling interval, not network latency
- A 5-second polling interval would yield 5-second "RTT"
- Therefore Bob does not track RTT

---

## Timeout Detection

If the connection dies, each side detects it differently:

| Aspect | Alice | Bob |
|--------|-------|-----|
| Metric | Packets sent without response | Wall-clock silence |
| Threshold | 30 consecutive packets with no response | 60 seconds without poll |
| Rationale | Can count her own sends | Cannot send, so counts time |

**Alice:**
- Counts packets sent without receiving any response from Bob
- "Response" means any packet from Bob (which carries an ack field), not
  specifically cumulative ack advancement
- If 30 consecutive packets sent with no response, connection is dead
- A stuck cumulative ack with active responses indicates packet loss (handled
  by retransmission), not a dead connection
- Packet-based to accommodate variable polling intervals

**Bob:**
- Cannot send packets unless Alice polls
- If Alice disappears, Bob has nothing to count
- Uses wall-clock time: 60 seconds of silence = dead connection

---

## Flow Control

Flow control is implicit, emerging from the protocol's structure:

**Bob is throttled by Alice's query rate:**
- Bob sends one packet per response
- If Alice polls slowly (idle mode), Bob's throughput is low
- If Alice polls rapidly (active mode), Bob's throughput is high
- Alice controls the overall data rate

**Alice is throttled by max_in_flight:**
- Alice cannot have more than max_in_flight packets outstanding
- If she sends faster than Bob can ACK, she blocks
- Window slides as ACKs arrive

**No explicit receive window (rwnd):**
- If processing slows on either side, ACKs are delayed
- Delayed ACKs cause the sender's window to fill
- Sender naturally pauses until ACKs arrive
- Self-regulating without explicit signaling

---

## Pipelining

Alice can send multiple queries in parallel for throughput:

```
Alice                                          Bob
  │                                              │
  │─── query 1 (packet 1) ──────────────────────▶│
  │─── query 2 (packet 2) ──────────────────────▶│
  │─── query 3 (packet 3) ──────────────────────▶│
  │                                              │
  │◀─────────────────────── response 1 ──────────│
  │◀─────────────────────── response 2 ──────────│
  │◀─────────────────────── response 3 ──────────│
```

**Alice:** Sends up to max_in_flight queries before waiting for responses.
Responses may arrive out of order; reliability layer handles reordering.

**Bob:** Processes queries serially. Each query gets a response. If Bob has
data queued, he includes one packet per response. Bob can have multiple
packets in-flight (awaiting ACKs in Alice's subsequent queries).

---

## Latency Implications

**Alice-initiated operations:** Network RTT only.

**Bob-initiated operations:** Network RTT + polling interval.

When idle (1-5s polling), Bob-initiated operations have significant latency.
When Bob sends real data (not pong), Alice polls again immediately with zero
delay. This ensures that when Bob has data queued, Alice drains it as fast as
the network allows. After a pong, Alice returns to the idle polling interval.

---

## Summary Table

| Aspect | Alice | Bob |
|--------|-------|-----|
| Network initiation | Can send anytime | Must respond to poll |
| Tunnel initiation | Yes | Yes (with latency) |
| Retransmit trigger | Timer (RTO) | Opportunity (poll) |
| RTT tracking | Yes | No |
| Timeout metric | 30 packets with no response | 60s silence |
| Throughput limit | max_in_flight | Alice's query rate |
| Pipelining | Sends parallel queries | Responds serially |
