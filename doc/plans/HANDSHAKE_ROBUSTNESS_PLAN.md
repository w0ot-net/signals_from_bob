# Handshake Robustness Plan

Status: draft

## Goal

Make the Alice/Bob handshake resilient to loss, duplication, and reordering
without violating the asymmetry rules (Alice initiates, Bob only responds to
polls). The handshake should converge reliably under lossy transports and
high delay without relying on test-only transport tweaks.

## Affected Components

- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/base_tunnel.py
- sfb/config.py
- doc/PROTOCOL.md
- doc/TUNNEL.md
- doc/ASYMMETRY.md
- tests/test_tunnel.py
- integration_tests/test_inmemory_lossy_file_transfer.py

## Design Notes

- Preserve the current wire format (SYN, SYN+ACK, ACK) and keep Bob response-only.
- Track an explicit handshake state on both sides:
  - remote_isn, local_isn, start_time, last_synack_sent, synack_count
- Allow duplicate handshake packets to be handled idempotently:
  - Bob should reuse the same local_isn when replying to duplicate SYNs that
    match the in-progress remote_isn.
  - Alice should reply to duplicate SYN+ACKs after CONNECTED without resetting
    state.
- Avoid handshake regression during MTU/window negotiation by ensuring the
  first post-handshake poll is always answered even if data is pending.
- Keep all logic ASCII-only and standard library only.

## Implementation Steps

1. Add a small handshake state struct on Bob to remember the current remote_isn
   and local_isn while CONNECTING; reuse local_isn on duplicate SYNs that match
   the in-progress remote_isn.
2. Update Bob handshake handling:
   - If CONNECTING and SYN arrives with the same remote_isn, resend SYN+ACK
     without resetting recv/send window state.
   - If CONNECTING and SYN arrives with a new remote_isn, reset handshake state
     and windows, then respond with SYN+ACK.
   - If CONNECTING and ACK does not match the current local_isn, resend SYN+ACK
     to prompt the correct ACK.
3. Update Alice handshake handling:
   - If CONNECTED and a SYN+ACK arrives that matches the original local_isn,
     send an ACK but do not reset state.
   - If CONNECTING and a SYN+ACK arrives with an unexpected ack, ignore it but
     keep retrying with the original local_isn.
4. Add a configurable handshake retry backoff cap in Config so lossy links
   can retry quickly without waiting for large RTO growth.
5. Ensure the first post-handshake poll is not suppressed by pacing or pending
   data so Bob can confirm the handshake promptly.
6. Update protocol/docs to describe duplicate SYN/SYN+ACK handling and the
   handshake retry/backoff behavior.
7. Add tests:
   - Unit tests for Bob duplicate SYN handling and local_isn reuse.
   - Unit tests for Alice handling duplicate SYN+ACK after CONNECTED.
   - An integration test that exercises handshake under moderate loss (not
     full chaos) to verify convergence without test-only shortcuts.

## Validation

- Run unit tests for tunnel/handshake behavior.
- Run integration tests that cover in-memory handshake under loss.
- Do not run tests in tests/e2e/.
