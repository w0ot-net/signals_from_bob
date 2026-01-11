# DNS Flat Phase 3: Documentation

Status: completed

## Summary
Document server and stager one-liner usage for the DNS flat loader workflow.

## Goals
- Add README guidance for `--stager` server usage.
- Document the OS-specific stager one-liners, hardcoded settings, and
  required DNS base domain/CNAME label alignment.
 - Document the `--passthrough` flag and how its args are embedded into
   generated stagers.

## Non-Goals
- Architecture deep dive.
- Tests or automation.

## Affected Components
- `README.md`

## Plan
1. Add a short README section for DNS flat stagers.
   - Example server invocation with `--stager`.
   - Example stager one-liner usage for Linux and Windows.
   - Note that the stagers use system resolver detection only.
   - Note the required `base_domain` and `dns_cname_label` alignment and
     that the stager base domain is derived from Bob's `--domain`.

## Testing
- Do not run tests here.

## Execution Notes
- Expanded `README.md` with DNS stager one-liner usage, passthrough guidance,
  resolver detection behavior, and base domain/CNAME label alignment notes.
- Tests not run (per instructions).
