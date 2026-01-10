# DNS Flat Stager Server Refactor Plan

Status: completed

## Summary
Move DNS flat stager handling out of `sfb/transport/dns/dns_server.py` into
a dedicated helper module, leaving `DnsServer` to delegate stager queries to
the helper.

## Goals
- Relocate stager-specific state, query parsing, and response building to a
  new module.
- Keep the DNS flat stager behavior and logging unchanged.
- Keep `DnsServer` changes minimal: instantiate helper and delegate.

## Non-Goals
- Functional changes to stager query naming, payload formats, or logging.
- Changes to stager generation, CLI, or config beyond the refactor.
- Tests or end-to-end validation.

## Affected Components
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_flat_stager.py` (new)

## Plan
1. Add `sfb/transport/dns/dns_flat_stager.py` helper.
   - Encapsulate stager state (chunks, count, meta, name patterns).
   - Provide a `handle_query(...)` method that returns `True` when it
     handles a stager query.
   - Build responses using `dns_codec` and `DNS_STANDARD_SIZE`, omitting
     OPT records for stager replies.
   - Emit the existing `dns.flat_count`, `dns.flat_piece`, and
     `dns.flat_invalid` events with identical fields.

2. Update `DnsServer` to delegate.
   - Instantiate the helper from config fields when stager data exists.
   - Replace inlined stager parsing with a single helper call in `recv()`.
   - Remove stager-specific helper methods and state from `DnsServer` once
     all logic lives in the new module.

3. Preserve behavior and interfaces.
   - Ensure stager responses use `flat0.count.<base_domain>` and
     `flat0.%05d.<base_domain>` names.
   - Keep empty-response behavior and standard DNS size limits unchanged.

## Testing
- Do not run tests here.

## Execution Notes
- 2026-01-09: Added `dns_flat_stager` helper and delegated stager handling
  from `DnsServer`.
