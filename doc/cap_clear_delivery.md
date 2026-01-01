# Cap Clear Delivery Issue

Context from `tests/dns_cap_need_sim.py` simulation:
- Bob emits `cap_need` when a retransmit exceeds the DNS response cap.
- A relaxed cap is applied, and Bob sends `cap_clear` once the constraint is cleared.
- In practice the tunnel does not deliver `cap_clear` to Alice; her `_cap_need_active` latch remains True unless the handler is invoked directly.

What we observe now:
- The simulation forces `cap_clear` by calling Bob’s `_send_cap_signal` and then directly invoking Alice’s `_handle_cap_clear`. Without that direct call, `cap_clear` is logged on Bob but never processed by Alice.
- SQLite logs show repeated `tunnel.cap_need` and `tunnel.cap_clear` events on Bob, plus `tunnel.cap_clear_ignored` on Alice (no active request seen when the packet arrives).
- Control-channel framing was tightened: control messages are never split across payload caps; oversize control lines now raise `ChannelError` instead of sending partial bytes.

Likely root cause to chase next:
- `cap_clear` control packets are sent but either dropped or arrive before Alice has set `_cap_need_active`, leading to the “ignored” path.
- Need to trace control-channel sequencing and ordering after cap_need is set to ensure `cap_clear` is queued and delivered after Alice is ready to accept it (no fallback injections).
