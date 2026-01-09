# Flattener Role/Transport Filter Plan

## Goal
- Add `--alice` and `--transport <name>` to `scripts/flatten.py` to emit a
  filtered bundle: common + Alice-only modules, optionally restricted to a
  single transport (for example, dns).
- Allow `scripts/flatten.py` to import `python_minifier` as an explicit
  exception to the standard-library-only rule.

## Non-Goals
- Change protocol behavior or runtime logic beyond import/registry refactors
  needed for filtering.
- Add new transports or module types.
- Run tests here.

## Affected Components
- scripts/flatten.py
- doc/flatten_manifest.txt
- doc/architecture/FLATTENER.md
- README.md
- sfb/cli.py
- sfb/transport/__init__.py
- sfb/modules/__init__.py
- sfb/tunnel/module_loader.py

## Design Proposal
- **Manifest tagging**: extend `module` lines with optional key/value tags:
  `role=<common|alice|bob>` and `transport=<dns|icmp|udp_ephemeral|tls_handshake|
  tls_handshake_bump|memory>`. Unspecified tags default to `common`.
- **Filtering rules**:
  - No flags: include all modules (current behavior).
  - `--alice`: include modules tagged `role=common` or `role=alice`.
  - `--transport X`: include modules tagged `transport=common` or
    `transport=X`.
  - Both flags: include modules that satisfy both filters.
- **Validation updates**: apply completeness and order validation to the
  filtered set only; ignore `allow_late` pairs when either module is filtered
  out.
- **Lazy registries**:
  - Replace eager imports in `sfb/transport/__init__.py` with a registry of
    transport names -> module/class path strings, imported on demand by
    `get_transport_class`.
  - Replace eager imports in `sfb/modules/__init__.py` with a registry of
    module name -> module/class path strings, imported on demand by CLI and
    `ModuleLoader`.
  - Update `sfb/cli.py` and `sfb/tunnel/module_loader.py` to use these lazy
    registries so unused transports/modules are not required at import time.
- **Minifier exception**: document that `scripts/flatten.py` is allowed to
  import `python_minifier` directly (while the rest of the repo remains
  stdlib-only).

## Plan
1. Update manifest parsing to accept tagged `module` lines and add `--alice`
   and `--transport` flags that filter the module list before validation.
2. Tag all modules in `doc/flatten_manifest.txt` with appropriate `role` and
   `transport` values; keep untagged entries as `common`.
3. Refactor `sfb/transport/__init__.py` to a lazy registry and update
   `sfb/cli.py` to use it without importing all transports at startup.
4. Refactor `sfb/modules/__init__.py` to a lazy registry and update
   `sfb/cli.py` and `sfb/tunnel/module_loader.py` to use it.
5. Update `README.md` and `doc/architecture/FLATTENER.md` to document the new
   flags and the `python_minifier` exception for `scripts/flatten.py`.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
