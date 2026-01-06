# DNS Dynamic Clamp Bias Plan

## Goal
- Add dynamic clamp bias selection based on real data availability for Alice and Bob.
- Bias for Alice max payload when Bob has no real data to send.
- Bias for Bob max payload when Alice has no real data to send.
- Bias for balance when both sides have real data (accept lower throughput).
- Preserve current DNS MTU limits, retransmit guard behavior, and asymmetry rules.

## Non-Goals
- Change retransmit logic, polling asymmetry, or keepalive behavior.
- Add non-stdlib dependencies or drop Python 2.7/3 compatibility.
- Update or run E2E tests under tests/e2e.

## Affected Components
- sfb/transport/dns/dns_client.py
- sfb/transport/transport_base.py
- sfb/transport/lossy.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/base_tunnel.py
- sfb/channel/channel_manager.py
- doc/DNS_TRANSPORT.md

## Plan
1) Define "real data" signals for bias decisions:
   - Alice: pending non-control channel data (data channels only), not control-only.
   - Bob: received packets that include at least one non-control segment.
   - Keep the short-lived poll-based memory for Bob data, but drive it from
     data segments only (not control-only packets).
2) Extend the transport notification interface:
   - Add a no-op method in sfb/transport/transport_base.py for reporting
     peer data presence (e.g., notify_peer_data(has_data)).
   - Add a method or adjust notify_send_pending to accept a data-only signal
     (e.g., notify_send_pending(has_data) where has_data means real data).
   - Update sfb/transport/lossy.py to pass through the new notification(s).
3) Wire Alice data availability into the transport:
   - In sfb/tunnel/alice_tunnel.py, compute data-only pending using
     channel_manager.has_pending_data(mode='data') or has_data_channels_pending().
   - Call the transport notification with the data-only signal before reserving
     a send permit so DNS can bias the clamp correctly.
4) Wire Bob data availability into the transport:
   - In sfb/tunnel/base_tunnel.py, after decoding segments, compute
     has_data_segments = any(not segment.is_control for segment in segments).
   - Call transport.notify_peer_data(has_data_segments) for inbound packets.
   - Update dns_client to use this signal for _bob_has_data_remaining, instead
     of the current payload length heuristic.
5) Implement dynamic bias selection in dns_client:
   - Keep retransmit_guard and recv_window_sack as highest priority (force
     response_max to protect reliability).
   - When only Alice has real data: bias to Alice max (largest query payload).
   - When only Bob has real data: bias to Bob max (response_max).
   - When both have real data: use balanced mode.
   - When neither has real data: bias to Alice max (same as Bob idle).
6) Document and log the bias decisions:
   - Update doc/DNS_TRANSPORT.md clamp section to describe the new bias rules
     and the definition of real data.
   - Add a debug log event in dns_client for clamp decisions (mode and
     data flags) to aid troubleshooting.
