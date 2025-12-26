Project Rules
- Maintain Python 2.7 and 3 compatibility in all code changes.
- Use ASCII only for code and scripts. Non-ASCII is allowed in .md files.
- Must support windows and linux
- never mention claude, anthropic, or use emojis
- Tunnel enforces symmetric MTU: clamp to `min(send_mtu, recv_mtu)` on each side.
- Keepalive pongs are suppressed when any channel has pending data.
- Asymmetry rules (doc/ASYMMETRY.md): Alice initiates transport, Bob only responds to polls; Alice uses RTT-based retransmit, Bob retransmits opportunistically; Alice timeouts by packet count, Bob by wall-clock silence; throughput for Bob is bounded by Alice's polling rate.
