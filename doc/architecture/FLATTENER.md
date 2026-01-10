# Flattener

## Overview
The flattener produces a single-file bundle by concatenating module sources and
executing them in a manifest-defined order. It does not use import hooks or
embedded archives; order is explicit and validated.

## Manifest Format
The manifest lives at `doc/flatten_manifest.txt` and is line-oriented ASCII.
Directives:
- `entry <module:function>`: entrypoint function invoked after loading.
- `root <path>`: directory roots (relative to repo) to scan for completeness.
- `exclude <path>`: paths (relative to repo) excluded from completeness checks.
- `module <module.name> [role=ROLE transport=TRANSPORT]`: explicit execution
  order with optional tags. `role` is one of `common`, `alice`, or `bob`.
  `transport` is one of `common`, `dns`, `icmp`, `udp_ephemeral`,
  `tls_handshake`, or `tls_handshake_bump`. Omitted tags default to `common`.
- `allow_late <module.name> <dependency.name>`: allow a module to appear before
  a dependency when the import is intentionally deferred.

The manifest is the sole ordering source. The flattener fails if any module
under `root` is missing from the manifest or if the manifest lists a module
that does not exist.

## Filtering
Use `--alice` to include only modules tagged `role=common` or `role=alice`.
Use `--transport <name>` to include only modules tagged `transport=common` or
`transport=<name>`. When both flags are set, a module must satisfy both
filters. Validation runs against the filtered set, and `allow_late` pairs are
ignored when either module is filtered out.
The default manifest intentionally excludes the in-memory and lossy
transports plus profiling/log profile helpers, so flat bundles cannot use
those features.
Module packages under `sfb.modules` are tagged `role=common` because their
`__init__` modules import both server and relay classes.

## Order Validation
The flattener parses top-level import statements using `ast` and checks that
required dependencies appear earlier in the manifest. Imports inside function
bodies are ignored because all modules execute before the entrypoint, and those
imports run only after startup. Use `allow_late` for intentional out-of-order
relationships or dynamic imports.

## Minify
Use `--minify` to run `python-minifier` (external dependency) on each module
before bundling. The flattener tries the `python_minifier` module first and
falls back to the `pyminify` CLI if needed (`--minify-bin` controls the binary
path). `scripts/flatten.py` is the only file allowed to import
`python_minifier` directly. By default, only locals are renamed to avoid
breaking cross-module imports.
To keep Python 2 compatibility with local renaming enabled, avoid list/dict/set
comprehensions and generator expressions in modules that ship in the flat
build; use explicit loops instead.
The flattener enforces this with an AST scan and fails the build if any
comprehension or generator expression is found in selected modules. Template
placeholders are replaced with literals for parsing.
Use `--strip-logs` to remove `log_event(...)` and logger method calls (for
example, `logging.info(...)` or `self._logger.error(...)`) before
minification/bundling.

## Runtime Bootstrap
The generated bundle:
1. Chooses a virtual root path string from `SFB_FLAT_ROOT` or a fixed fallback.
2. Pre-creates module objects in `sys.modules` with `__file__`, `__package__`,
   and `__path__` (for packages).
3. Executes each module source in manifest order.
4. Calls the entrypoint function.

The bootstrap does not create directories. Module sources remain in memory, and
any runtime features that write to disk are responsible for creating parent
directories.

## __file__ Behavior
`__file__` points into the virtual root for each module. This supports code that
computes paths from `__file__` (for example, TLS bump template regeneration)
without relying on the original repository layout. Paths derived from
`__file__` may not exist until a runtime feature explicitly creates them.
