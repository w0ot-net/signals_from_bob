# CLI Complexity Reduction Phase 2

Status: draft

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce complexity in `create_config` and `_wrap_lossy_transport` by
  extracting focused helpers while preserving configuration semantics and
  logging payloads.
- Keep transport-specific defaults, port normalization, and `None` filtering
  behavior identical.

## Non-Goals
- Change config fields, defaults, or how CLI arguments map to Config.
- Alter lossy transport behavior, impairment math, or logging fields.
- Run tests here.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Inventory existing config paths.
   - Enumerate per-transport config assignments (DNS, ICMP, UDP ephemeral,
     TLS handshake, TLS bump).
   - Record client-only settings (pacing, retransmit, poll pacing).
   - Record server-only settings (file transfer root/size).
   - Record logging, relay, and crypto settings from `create_config`.
2. Extract transport config helpers.
   - `_build_dns_config(args)`, `_build_icmp_config(args)`,
     `_build_udp_ephemeral_config(args)`, `_build_tls_handshake_config(args)`,
     `_build_tls_bump_config(args)` returning dicts.
   - Keep host:port normalization identical for DNS listen address.
   - Preserve conditional setting of listen addr fields and defaults.
3. Extract role config helpers.
   - `_build_client_config(args)` for pacing, retransmit, and poll pacing args.
   - `_build_server_config(args)` for file transfer root/max size.
4. Extract logging and relay config helpers.
   - `_build_logging_config(args)` for stats, DB log fields, log profile, and
     relay/channel buffer options.
   - Keep `stats_enabled` mapping to `--verbose` unchanged.
5. Extract crypto config helpers.
   - `_build_crypto_config(args)` for `crypto_mode` and `crypto_psk` mapping.
   - Preserve precedence order: xor, rc4, sha256 (only one via argparse).
   - Keep `_normalize_psk` behavior unchanged.
6. Simplify `_wrap_lossy_transport`.
   - Add a helper for percent-to-rate conversion returning `(tx, rx)` pairs.
   - Add a helper to build impairment objects and return both send/recv
     impairment objects in the correct symmetric/asymmetric cases.
   - Keep log payload fields and values exactly the same.
7. Reassemble `create_config`.
   - Merge dicts in the same precedence order as today.
   - Retain the final `None` filtering before `Config(**kwargs)`.

## Acceptance Criteria
- `create_config` and `_wrap_lossy_transport` are shorter and clearer.
- No changes in computed config values for any known CLI path.
- Lossy transport logging payloads remain identical.

## Notes
- Keep helpers private and local to `sfb/cli.py`.
- Preserve Python 2.7 compatibility (no f-strings, no type hints).

## Testing
- Do not run tests here. If needed, use `radon cc sfb/cli.py` to verify the
  complexity drop after the refactor.
