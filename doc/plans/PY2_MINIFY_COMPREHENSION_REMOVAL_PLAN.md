# Python2 Minify Comprehension Removal Plan

## Goal
- Make minified `sfb_flat.py` compatible with Python 2 while keeping
  `rename_locals=True`.
- Eliminate list/dict/set comprehensions and generator expressions in code
  that ships in the flat build to avoid Python 2 scope leakage bugs.

## Non-Goals
- Changing protocol behavior or transport semantics.
- Disabling `rename_locals` or minification.
- Adding new runtime dependencies.

## Affected Components
- sfb/ (all modules included by doc/flatten_manifest.txt that use
  comprehensions or generator expressions).
- doc/flatten_manifest.txt (only if module coverage needs adjustment for
  scanning).
- scripts/flatten.py (optional: add a guard to fail if comprehensions remain).
- doc/architecture/FLATTENER.md (document mandatory AST guard).

## Design Proposal
- Replace list/dict/set comprehensions and generator expressions with explicit
  loops and temporary containers or helper functions.
- Prefer small, local helper functions when a comprehension feeds a function
  call, to keep call sites readable without changing behavior.
- Keep variable naming stable and readable; avoid introducing new shadowed
  locals that minifiers could collapse.
- Add a Python 3 AST scan in scripts/flatten.py to reject comprehensions in
  modules selected for the flat build, preventing regressions.

## Plan
1. Inventory comprehensions and generator expressions in `sfb/` modules used
   by the flat build (AST scan; record file and line locations).
2. Rewrite each comprehension into explicit loops or helper functions while
   keeping semantics, ordering, and performance characteristics.
3. Add a flatten-time AST guard in `scripts/flatten.py` that fails the build
   when comprehensions are detected in selected modules.
4. Update `doc/architecture/FLATTENER.md` to document the mandatory AST guard.
5. Regenerate `sfb_flat.py` with `--minify` and verify Python 2 startup logs
   no longer trigger `UnboundLocalError`.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
