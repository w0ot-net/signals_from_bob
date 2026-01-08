# CLI LOC Reduction Plan

## Goal
- Reduce `sfb/cli.py` LOC by at least 10% (baseline * 0.90 or lower), with a
  target of 20% (baseline * 0.80 or lower).
- Preserve behavior, performance, and readability while avoiding any bulk line
  shifts into other files.

## Non-Goals
- Changing CLI semantics, defaults, or help text content beyond formatting.
- Moving CLI logic into new modules or other existing files.
- Running tests (user will run as needed).

## Affected Components
- sfb/cli.py

## Design Proposal
- Replace repetitive `parser.add_argument` blocks with declarative specs and
  small helpers that expand them into the same argparse calls.
- Map argument names to config fields via compact tables and a helper that
  filters `None` values, preserving special-case logic in small, explicit
  helpers.
- Consolidate shared runtime scaffolding (signal handlers, shutdown logging)
  into single helpers to remove repeated blocks.
- Keep all new helpers inside `sfb/cli.py` to avoid moving LOC elsewhere.

## Plan
1. Record the baseline LOC with `wc -l sfb/cli.py` and note the target ranges
   (<= 90% baseline, target <= 80% baseline).
2. Convert CLI argument definitions to spec tables plus a small helper that
   emits `add_argument` calls, including a shared helper for the loss/dup/
   corrupt triplets.
3. Replace config builders with mapping-driven helpers, keeping transport
   special cases (DNS listen parsing, TLS listen alias, max record bytes)
   explicit and minimal.
4. Factor shared signal/shutdown logic into helper functions and rewire
   `run_client`, `run_server`, and shutdown paths accordingly.
5. Verify the new LOC count meets the minimum 10% reduction and aims for
   20% reduction without increasing LOC in other files.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
