# Server Notify Peer Data Plan

Status: draft

## Goal

Eliminate the AttributeError from Bob's serve loop by ensuring server-side
transports expose the notify_peer_data hook, and keep the hint propagation
consistent across wrapped transports.

## Affected Components

- sfb/transport/transport_base.py

## Design Notes

- base_tunnel calls notify_peer_data on both Alice and Bob; the server base
  class should provide a no-op default to avoid AttributeError.
- Server should mirror Transport by offering notify_peer_data as an optional
  hint with a default no-op implementation. Subclasses can override if needed.
- Keep Python 2.7/3 compatibility and standard-library-only behavior.

## Implementation Steps

1. Add notify_peer_data to sfb/transport/transport_base.py Server with a no-op
   default and a docstring aligned with Transport.notify_peer_data.
2. Verify bob startup no longer raises AttributeError for ICMP and other
   server transports.

## Validation

- Run focused unit or integration tests for tunnel/transport behavior
  (exclude tests/e2e/).
- Start an ICMP tunnel and confirm the serve loop proceeds without
  AttributeError logs.
