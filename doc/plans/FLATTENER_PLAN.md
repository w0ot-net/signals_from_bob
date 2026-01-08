# Flattener Design Plan

## Goal
- Define a design for producing a single-file Python bundle via literal
  concatenation (no import hooks or embedded archives).
- Agree on required annotations/manifest rules to make concatenation
  deterministic and maintainable.

## Non-Goals
- Implement the flattener script or modify runtime code here.
- Run tests or validate a bundled output here.
- Change protocol behavior or module behavior.

## Affected Components
- scripts/flatten.py (new)
- README.md
- doc/architecture/ (new flattener notes, if needed)
- sfb.py (entrypoint adjustments, if needed)

## Plan
1. Inventory import patterns and identify risky cases for concatenation
   (relative imports, dynamic imports, `__file__` usage).
2. Choose a source-ordering strategy (package-first, topological sort, or
   explicit manifest) and define the rule set for annotations, if any.
3. Define the concatenation output format:
   - prologue (compat/imports)
   - embedded modules (ordered blocks)
   - entrypoint shim
4. Specify how `__file__`, module `__package__`, and package globals are
   represented in a single-file environment.
5. Document the chosen rules and expected workflow for maintainers.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
