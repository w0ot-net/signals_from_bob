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
- doc/flatten_manifest.txt (new)
- README.md
- doc/architecture/ (new flattener notes, if needed)
- sfb.py (entrypoint adjustments, if needed)

## Design Proposal
- **Manifest-first ordering**: add `doc/flatten_manifest.txt` as the primary
  source of truth for module ordering, entrypoint, and excludes. The file is
  ASCII, line-oriented, and easy to review in diffs.
- **Optional per-file annotations**: allow lightweight comments to reduce
  manifest churn when adding a new module. Proposed pragma format:
  - `# FLATTEN: after=sfb.compat,sfb.time_provider`
  - `# FLATTEN: before=sfb.cli`
  - `# FLATTEN: skip`
  The flattener treats annotations as constraints layered on top of the
  manifest order (manifest wins on conflicts).
- **Concatenation strategy**: emit a single file that pre-creates module
  objects in `sys.modules`, then executes each module's source into its own
  module dict in manifest order. This avoids custom import hooks while keeping
  package semantics (relative imports, module globals) intact.
- **`__file__` behavior**: bootstrap a temporary on-disk root (configurable via
  `SFB_FLAT_ROOT`) and assign each module `__file__` under that root so code
  that uses `__file__` (TLS bump template generation) has a real path and can
  write alongside a synthesized package tree.
- **Output layout**:
  1. Prologue imports + bootstrap helpers.
  2. Pre-register all modules/packages with `__name__`, `__package__`,
     `__path__` (for packages), and `__file__`.
  3. Module blocks executed in order.
  4. Entrypoint shim calling `entry = module:function`.

## Plan
1. Inventory import patterns and confirm which modules rely on `__file__` or
   package paths so the temp-root behavior is sufficient.
2. Define `doc/flatten_manifest.txt` format (entrypoint, include roots, excludes,
   ordered module list).
3. Define and document the `# FLATTEN:` annotation rules and conflict handling.
4. Specify the bootstrap sequence (pre-register modules, temp root creation,
   exec order) and document expected runtime behavior.
5. Update README with usage and workflow guidelines once the script exists.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
