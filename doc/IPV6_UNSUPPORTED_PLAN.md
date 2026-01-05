# IPv6 Unsupported Plan

Status: draft

## Goal

Make it explicit in code and documentation that the project is IPv4-only and
does not support IPv6 addresses or IPv6 sockets.

## Affected Components

- README.md
- doc/PROTOCOL.md
- doc/TRANSPORTS.md
- doc/DNS_TRANSPORT.md
- doc/ICMP_TRANSPORT.md
- doc/UDP_EPHEMERAL_TRANSPORT.md
- doc/TLS_TRANSPORT.md
- doc/MODULES.md
- doc/SOCKS.md
- sfb/cli.py
- sfb/config.py
- sfb/transport/udp_ephemeral/udp_ephemeral_config.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/transport/proxy_helpers.py
- sfb/transport/icmp/icmp_packet.py

## Design Notes

- Keep Python 2.7/3 compatibility and standard library only.
- Treat any IPv6 literal (including bracketed host:port) as invalid input.
- Keep asymmetry behavior unchanged; this is a documentation and validation
  clarity update, not a protocol change.

## Non-Goals

- Standardize error messages to say "IPv6 not supported" instead of keeping
  generic parsing failures.
- Add IPv6 literal parsing checks or validation helpers in code.
- Change parsing behavior to reject IPv6 literals with new error paths.
- Add tests that assert IPv6 literal parsing failures.

## Implementation Steps

1. Documentation sweep:
   - Add an explicit "IPv4 only" statement in README and transport docs.
   - Remove or rewrite any IPv6 examples (for example, bracketed host:port).
   - Update SOCKS docs to state only ipv4 and domain address types are supported.
2. CLI and config messaging:
   - Update CLI help strings for listen/target/resolver fields to say IPv4 only.
   - Add a short note in Config docstrings for host:port settings.
3. Protocol docs:
   - Clarify that any address fields labeled "ipv4" are the only supported
     address type in the protocol.
4. Tests:
   - No new tests required for docs/CLI messaging updates.
   - Do not add or run tests in tests/e2e/.

## Validation

- No new tests required; skip tests/e2e/.
