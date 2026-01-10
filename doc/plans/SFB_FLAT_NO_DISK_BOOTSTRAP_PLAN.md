# Sfb Flat No Disk Bootstrap Plan

## Goal
- Bootstrap `sfb_flat.py` without creating directories or temp files.
- Keep module execution order and entrypoint behavior unchanged.
- Preserve Python 2.7/3 compatibility and ASCII-only bundle output.

## Non-Goals
- Remove disk I/O from runtime features that explicitly write to disk (logs,
  cprofile, TLS bump template generation, DNS stager output).
- Change manifest ordering, module selection, or transport behavior.

## Affected Components
- scripts/flatten.py
- sfb_flat.py
- sfb/cli.py
- sfb/stagers/dns_stager.py
- doc/architecture/FLATTENER.md
- README.md

## Plan
1. Update the flattener bootstrap template to use a virtual root.
   - Drop `_ensure_dir`, `_prepare_root`, and `tempfile` usage from the
     generated stub.
   - Add a lightweight `_virtual_root` that returns `SFB_FLAT_ROOT` (string
     only) or a fixed fallback.
   - Keep `_module_path` purely string-based to populate `__file__`/`__path__`
     without touching disk.

2. Adjust module loading to use virtual paths.
   - Set `__file__` and `__path__` from the virtual root and module name.
   - Keep `compile()` filenames aligned with the virtual paths for tracebacks.

3. Make disk-writing helpers create directories on demand.
   - In `sfb/cli.py`, ensure `_write_tls_bump_cert_template` creates parent
     dirs before writing.
   - In `sfb/stagers/dns_stager.py`, ensure `output_dir` exists before writing
     stager files (explicit mkdir instead of relying on bootstrap).

4. Update docs to reflect the new behavior.
   - `README.md`: replace the on-disk root note with the virtual-root behavior
     and `SFB_FLAT_ROOT` semantics.
   - `doc/architecture/FLATTENER.md`: update Runtime Bootstrap and `__file__`
     behavior to note that no directories are created.

5. Regenerate `sfb_flat.py` via `scripts/flatten.py` (python3) and confirm the
   bootstrap no longer calls `os.makedirs` or `tempfile.mkdtemp`.
