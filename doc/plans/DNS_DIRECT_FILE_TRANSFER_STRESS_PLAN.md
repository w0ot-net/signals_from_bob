# DNS Direct File Transfer Stress Plan

## Goal
- Add integration tests that stress DNS direct mode with bidirectional file
  transfer using varying concurrency.
- Exercise the poll-hint clamp path by driving Bob backpressure while keeping
  Alice throughput high.

## Non-Goals
- Modify core transport, tunnel, or file transfer behavior.
- Run any tests under tests/e2e/.

## Affected Components
- doc/plans/DNS_DIRECT_FILE_TRANSFER_STRESS_PLAN.md
- integration_tests/test_dns_direct_file_transfer_stress.py

## Design Notes
- Use DNS direct mode locally with port 5353 and 127.0.0.1.
- Use python3 for all test runs.
- Use only Python standard library APIs; keep code ASCII-only.
- Keep file paths portable for Windows and Linux.
- Skip the test if port 5353 is already in use to avoid flaky failures.
- Use ./test_download_files/1MB.bin as the source payload; copy it into
  temporary Alice/Bob roots to avoid mutating repo files.

## Plan
1) Create integration_tests/test_dns_direct_file_transfer_stress.py
   - Follow the structure of tests/e2e/test_dns_e2e.py for DNS setup, but place
     it under integration_tests.
   - Implement a lightweight tunnel runner for Alice (tick loop thread).
   - Use DnsServer (Bob) in a background thread with serve_forever().
   - Use DnsClient (Alice) with direct resolver on 127.0.0.1:5353.
   - Configure file_transfer_max_active to the maximum concurrency used.
   - Create temp directories for Alice and Bob roots; copy 1MB.bin into each.
2) Add bidirectional stress cases with varying concurrency
   - Define a list of concurrency pairs (alice_to_bob, bob_to_alice), e.g.:
     [(1, 1), (2, 2), (4, 4), (1, 4), (4, 1)].
   - For each case, run two worker pools in parallel:
     - Alice uploads 1MB.bin to Bob with unique remote filenames.
     - Alice downloads Bob's 1MB.bin to unique local filenames.
   - Use Queue-based workers with bounded timeouts per transfer.
   - Verify each completed transfer by file size; optionally hash the first
     transfer in each direction for correctness.
3) Add port-availability check
   - Attempt to bind UDP 127.0.0.1:5353 before starting the server.
   - If the bind fails, skip the test case with a clear message.

## Validation
- python3 -m unittest integration_tests.test_dns_direct_file_transfer_stress
- Do not run tests/e2e/.
