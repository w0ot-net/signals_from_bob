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
- `module <module.name>`: explicit execution order.
- `allow_late <module.name> <dependency.name>`: allow a module to appear before
  a dependency when the import is intentionally deferred.

The manifest is the sole ordering source. The flattener fails if any module
under `root` is missing from the manifest or if the manifest lists a module
that does not exist.

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
path). By default, only locals are renamed to avoid breaking cross-module
imports.
Use `--strip-logs` to remove `log_event(...)` and logger method calls (for
example, `logging.info(...)` or `self._logger.error(...)`) before
minification/bundling.

## Runtime Bootstrap
The generated bundle:
1. Creates a flat on-disk root (temporary by default, or `SFB_FLAT_ROOT`).
2. Pre-creates module objects in `sys.modules` with `__file__`, `__package__`,
   and `__path__` (for packages).
3. Executes each module source in manifest order.
4. Calls the entrypoint function.

Directories for `__file__` paths are created under the flat root. Module sources
remain in memory; only the directory structure is synthesized.

## __file__ Behavior
`__file__` points into the flat root for each module. This supports code that
computes paths from `__file__` (for example, TLS bump template regeneration)
without relying on the original repository layout.
