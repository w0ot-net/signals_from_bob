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

## Implementation Steps

1. Documentation sweep:
   - Add an explicit "IPv4 only" statement in README and transport docs.
   - Remove or rewrite any IPv6 examples (for example, bracketed host:port).
   - Update SOCKS docs to state only ipv4 and domain address types are supported.
2. CLI and config messaging:
   - Update CLI help strings for listen/target/resolver fields to say IPv4 only.
   - Add a short note in Config docstrings for host:port settings.
3. Validation and parsing:
   - Introduce a shared IPv6-literal check (simple ":" and bracket detection
     for host strings) in a compat helper.
   - Use it in host:port parsing helpers (udp_ephemeral, tls_handshake,
     tls_handshake_bump, proxy_helpers) to raise a clear "IPv6 not supported"
     error when an IPv6 literal is provided.
   - Ensure the error path is covered in icmp_packet IPv6 handling docs.
4. Protocol docs:
   - Clarify that any address fields labeled "ipv4" are the only supported
     address type in the protocol.
5. Tests:
   - Add unit tests for host:port parsers rejecting IPv6 literals.
   - Do not add or run tests in tests/e2e/.

## Validation

- Run unit tests that exercise address parsing and error messages.
- Skip tests/e2e/.
