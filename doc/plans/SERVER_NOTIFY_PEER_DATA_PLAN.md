# Server Notify Peer Data Plan

Status: draft

## Goal

Eliminate the AttributeError from Bob's serve loop by ensuring server-side
transports expose the notify_peer_data hook, and keep the hint propagation
consistent across wrapped transports.

## Affected Components

- sfb/tunnel/base_tunnel.py
- sfb/transport/transport_base.py
- sfb/transport/lossy.py
- sfb/transport/icmp/icmp_server.py
- sfb/transport/dns/dns_server.py
- sfb/transport/udp_ephemeral/udp_ephemeral_server.py
- sfb/transport/memory/memory_server.py
- sfb/transport/tls_handshake/tls_handshake_server.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_server.py

## Design Notes

- base_tunnel calls notify_peer_data on both Alice and Bob; server transports
  should provide a no-op default to avoid AttributeError.
- Server should mirror Transport by offering notify_peer_data as an optional
  hint with a default no-op implementation.
- LossyServer should forward notify_peer_data to its inner server so wrapper
  transports do not drop the hint if a server implementation starts using it.
- Keep Python 2.7/3 compatibility and standard-library-only behavior.

## Implementation Steps

1. Add notify_peer_data to sfb/transport/transport_base.py Server with a no-op
   default and a docstring aligned with Transport.notify_peer_data.
2. Update sfb/transport/lossy.py LossyServer to forward notify_peer_data to
   the wrapped server when available.
3. Audit other Server implementations (ICMP, DNS, UDP ephemeral, memory, TLS
   handshake, TLS handshake bump) to confirm they inherit the new default or
   add explicit overrides if they need to track peer data.
4. Verify bob startup no longer raises AttributeError for ICMP and other
   server transports.

## Validation

- Run focused unit or integration tests for tunnel/transport behavior
  (exclude tests/e2e/).
- Start an ICMP tunnel and confirm the serve loop proceeds without
  AttributeError logs.
