#!/usr/bin/env python3
# -*- coding: ascii -*-
"""
Flatten sfb into a single-file bundle via literal concatenation.

Usage:
  python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py
  python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py --minify
  python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py --minify --minify-globals
  python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py --minify --minify-bin /path/to/pyminify
"""

from __future__ import absolute_import, print_function

import argparse
import ast
import io
import inspect
import os
import subprocess
import sys


class ManifestError(Exception):
    pass


class ValidationError(Exception):
    pass


_MINIFY_CLI_ARGS = (
    '--prefer-single-line',
    '--remove-literal-statements',
    '--remove-asserts',
    '--remove-debug',
    '--remove-class-attribute-annotations',
    '--no-remove-object-base',
)


class _ImportCollector(ast.NodeVisitor):
    def __init__(self):
        self.imports = []
        self._in_func = 0

    def visit_Import(self, node):
        if not self._in_func:
            self.imports.append(node)

    def visit_ImportFrom(self, node):
        if not self._in_func:
            self.imports.append(node)

    def visit_FunctionDef(self, node):
        self._in_func += 1
        self._in_func -= 1

    def visit_AsyncFunctionDef(self, node):
        self._in_func += 1
        self._in_func -= 1

    def visit_Lambda(self, node):
        self._in_func += 1
        self._in_func -= 1

    def visit_ClassDef(self, node):
        for stmt in node.body:
            self.visit(stmt)



def _read_text(path):
    try:
        with io.open(path, 'rb') as handle:
            data = handle.read()
    except (IOError, OSError) as exc:
        raise ManifestError('Unable to read %s: %s' % (path, exc))
    try:
        return data.decode('ascii')
    except UnicodeDecodeError:
        raise ManifestError('Non-ASCII content in %s' % path)


def _minify_arg_names(func):
    try:
        argspec = inspect.getfullargspec(func)
        return set(argspec.args)
    except AttributeError:
        argspec = inspect.getargspec(func)
        return set(argspec.args)


def _minify_with_cli(path, minify_bin, rename_globals):
    cmd = [minify_bin]
    cmd.extend(_MINIFY_CLI_ARGS)
    if rename_globals:
        cmd.append('--rename-globals')
    cmd.append(path)
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except OSError as exc:
        raise ManifestError('Unable to run %s: %s' % (minify_bin, exc))
    except subprocess.CalledProcessError as exc:
        detail = exc.output
        if isinstance(detail, bytes):
            detail = detail.decode('ascii', 'replace')
        if detail is None:
            detail = ''
        detail = detail.strip()
        if detail:
            raise ManifestError('minify failed for %s: %s' % (path, detail))
        raise ManifestError('minify failed for %s' % path)
    if isinstance(output, bytes):
        output = output.decode('ascii')
    try:
        output.encode('ascii')
    except UnicodeEncodeError:
        raise ManifestError('Non-ASCII minify output for %s' % path)
    return output


def _minify_source(source, name, path, rename_globals, minify_bin):
    try:
        import python_minifier
    except ImportError:
        return _minify_with_cli(path, minify_bin, rename_globals)
    minify = getattr(python_minifier, 'minify', None)
    if minify is None:
        return _minify_with_cli(path, minify_bin, rename_globals)

    args = _minify_arg_names(minify)
    options = {
        'remove_literal_statements': True,
        'rename_locals': True,
        'rename_globals': rename_globals,
        'remove_annotations': True,
        'remove_pass': True,
        'remove_object_base': True,
        'remove_asserts': True,
        'remove_debug': True,
        'hoist_literals': True,
        'combine_imports': True,
    }
    kwargs = {}
    for key, value in options.items():
        if key in args:
            kwargs[key] = value
    if 'filename' in args:
        kwargs['filename'] = name

    try:
        output = minify(source, **kwargs)
    except Exception as exc:
        raise ManifestError('minify failed for %s: %s' % (name, exc))
    if isinstance(output, bytes):
        output = output.decode('ascii')
    try:
        output.encode('ascii')
    except UnicodeEncodeError:
        raise ManifestError('Non-ASCII minify output for %s' % name)
    return output


def _normalize_path(path):
    return os.path.normpath(path.replace('/', os.sep).replace('\\', os.sep))



def _is_excluded(rel_path, excludes):
    for ex in excludes:
        if rel_path == ex or rel_path.startswith(ex + os.sep):
            return True
    return False



def _parse_manifest(path):
    entry = None
    roots = []
    excludes = []
    modules = []
    allow_late = set()
    seen_modules = set()

    text = _read_text(path)
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0]
        if key == 'entry':
            if len(parts) != 2:
                raise ManifestError('entry expects 1 value at line %d' % line_no)
            if entry is not None:
                raise ManifestError('entry duplicated at line %d' % line_no)
            entry = parts[1]
        elif key == 'root':
            if len(parts) != 2:
                raise ManifestError('root expects 1 value at line %d' % line_no)
            roots.append(parts[1])
        elif key == 'exclude':
            if len(parts) != 2:
                raise ManifestError('exclude expects 1 value at line %d' % line_no)
            excludes.append(parts[1])
        elif key == 'module':
            if len(parts) != 2:
                raise ManifestError('module expects 1 value at line %d' % line_no)
            module = parts[1]
            if module in seen_modules:
                raise ManifestError('duplicate module %s at line %d' % (
                    module,
                    line_no,
                ))
            seen_modules.add(module)
            modules.append(module)
        elif key == 'allow_late':
            if len(parts) != 3:
                raise ManifestError('allow_late expects 2 values at line %d' % line_no)
            allow_late.add((parts[1], parts[2]))
        else:
            raise ManifestError('unknown directive %s at line %d' % (key, line_no))

    if entry is None:
        raise ManifestError('manifest missing entry directive')
    if ':' not in entry:
        raise ManifestError('entry must be module:function')
    if not roots:
        raise ManifestError('manifest missing root directive(s)')
    if not modules:
        raise ManifestError('manifest missing module entries')

    return {
        'entry': entry,
        'roots': roots,
        'excludes': excludes,
        'modules': modules,
        'allow_late': allow_late,
    }



def _collect_modules(repo_root, roots, excludes):
    module_paths = {}
    is_package = {}

    for root in roots:
        root_path = os.path.join(repo_root, _normalize_path(root))
        if not os.path.isdir(root_path):
            raise ManifestError('root not found: %s' % root)
        for dirpath, dirnames, filenames in os.walk(root_path):
            rel_dir = _normalize_path(os.path.relpath(dirpath, repo_root))
            if _is_excluded(rel_dir, excludes):
                dirnames[:] = []
                continue
            filtered = []
            for name in dirnames:
                if name == '__pycache__':
                    continue
                rel_sub = _normalize_path(os.path.join(rel_dir, name))
                if _is_excluded(rel_sub, excludes):
                    continue
                filtered.append(name)
            dirnames[:] = filtered

            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                rel_path = _normalize_path(os.path.join(rel_dir, filename))
                if _is_excluded(rel_path, excludes):
                    continue
                mod = rel_path[:-3].replace(os.sep, '.')
                if mod.endswith('.__init__'):
                    mod = mod[:-len('.__init__')]
                if mod in module_paths:
                    raise ManifestError('duplicate module path for %s' % mod)
                module_paths[mod] = os.path.join(repo_root, rel_path)
                is_package[mod] = (filename == '__init__.py')

    return module_paths, is_package



def _module_package(name, is_package):
    if is_package:
        return name
    if '.' in name:
        return name.rsplit('.', 1)[0]
    return ''



def _resolve_base(pkg, level, module):
    if level is None:
        level = 0
    parts = pkg.split('.') if pkg else []
    if level == 0:
        base_parts = parts
    else:
        if level > len(parts):
            return None
        base_parts = parts[:len(parts) - level + 1]
    base = '.'.join(base_parts)
    if module:
        if base:
            return base + '.' + module
        return module
    return base



def _collect_import_deps(source, name, is_pkg, module_set):
    tree = ast.parse(source, filename=name)
    collector = _ImportCollector()
    collector.visit(tree)
    pkg = _module_package(name, is_pkg)
    deps = set()

    for node in collector.imports:
        if isinstance(node, ast.Import):
            for alias in node.names:
                dep = alias.name
                if dep in module_set:
                    deps.add(dep)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_base(pkg, node.level, node.module)
            if node.module is not None:
                if base in module_set:
                    deps.add(base)
            for alias in node.names:
                if base:
                    candidate = base + '.' + alias.name
                else:
                    candidate = alias.name
                if candidate in module_set:
                    deps.add(candidate)
    return deps



def _validate_manifest(manifest, module_paths, allow_late):
    missing = [name for name in module_paths if name not in manifest]
    unknown = [name for name in manifest if name not in module_paths]
    if missing:
        raise ManifestError('manifest missing module(s): %s' % ', '.join(sorted(missing)))
    if unknown:
        raise ManifestError('manifest lists unknown module(s): %s' %
                            ', '.join(sorted(unknown)))
    for module, dep in sorted(allow_late):
        if module not in manifest:
            raise ManifestError('allow_late module not in manifest: %s' % module)
        if dep not in manifest:
            raise ManifestError('allow_late dependency not in manifest: %s' % dep)



def _validate_order(entries, allow_late):
    module_set = set([entry[0] for entry in entries])
    index = {name: idx for idx, (name, _, _) in enumerate(entries)}
    errors = []
    for name, is_pkg, source in entries:
        deps = _collect_import_deps(source, name, is_pkg, module_set)
        for dep in sorted(deps):
            if dep == name:
                continue
            if index[dep] > index[name] and (name, dep) not in allow_late:
                errors.append('%s requires %s' % (name, dep))
    if errors:
        raise ValidationError(
            'manifest order violations:\n' + '\n'.join(errors)
        )



def _write_output(path, entry, entries, manifest_path):
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    with io.open(path, 'w', encoding='ascii', newline='\n') as handle:
        handle.write('#!/usr/bin/env python\n')
        handle.write('# -*- coding: ascii -*-\n')
        handle.write('"""\n')
        handle.write('Generated by scripts/flatten.py from %s.\n' % manifest_path)
        handle.write('Do not edit by hand.\n')
        handle.write('"""\n\n')
        handle.write('from __future__ import absolute_import\n\n')
        handle.write('import os\n')
        handle.write('import sys\n')
        handle.write('import tempfile\n')
        handle.write('import types\n\n')
        handle.write('_ENTRY = %r\n\n' % entry)
        handle.write('_MODULES = [\n')
        for name, is_pkg, source in entries:
            handle.write('    (%r, %s, %r),\n' % (
                name,
                'True' if is_pkg else 'False',
                source,
            ))
        handle.write(']\n\n')
        handle.write('def _ensure_dir(path):\n')
        handle.write('    if not path:\n')
        handle.write('        return\n')
        handle.write('    if not os.path.isdir(path):\n')
        handle.write('        os.makedirs(path)\n\n')
        handle.write('def _module_path(root, name, is_pkg):\n')
        handle.write('    parts = name.split(".")\n')
        handle.write('    if is_pkg:\n')
        handle.write('        parts.append("__init__.py")\n')
        handle.write('    else:\n')
        handle.write('        parts[-1] = parts[-1] + ".py"\n')
        handle.write('    return os.path.join(root, *parts)\n\n')
        handle.write('def _bootstrap_root():\n')
        handle.write('    root = os.environ.get("SFB_FLAT_ROOT")\n')
        handle.write('    if root:\n')
        handle.write('        root = os.path.abspath(root)\n')
        handle.write('        _ensure_dir(root)\n')
        handle.write('        return root\n')
        handle.write('    return tempfile.mkdtemp(prefix="sfb_flat_")\n\n')
        handle.write('def _prepare_root(root, modules):\n')
        handle.write('    for name, is_pkg, _ in modules:\n')
        handle.write('        path = _module_path(root, name, is_pkg)\n')
        handle.write('        _ensure_dir(os.path.dirname(path))\n\n')
        handle.write('def _load_modules(root, modules):\n')
        handle.write('    for name, is_pkg, _ in modules:\n')
        handle.write('        mod = types.ModuleType(name)\n')
        handle.write('        mod.__file__ = _module_path(root, name, is_pkg)\n')
        handle.write('        if is_pkg:\n')
        handle.write('            mod.__package__ = name\n')
        handle.write('            mod.__path__ = [os.path.dirname(mod.__file__)]\n')
        handle.write('        else:\n')
        handle.write('            mod.__package__ = name.rsplit(".", 1)[0] if "." in name else ""\n')
        handle.write('        sys.modules[name] = mod\n')
        handle.write('    for name, _, source in modules:\n')
        handle.write('        mod = sys.modules[name]\n')
        handle.write('        code = compile(source, mod.__file__, "exec")\n')
        handle.write('        exec(code, mod.__dict__)\n\n')
        handle.write('def _run_entry(entry_spec):\n')
        handle.write('    module_name, func_name = entry_spec.split(":", 1)\n')
        handle.write('    mod = sys.modules.get(module_name)\n')
        handle.write('    if mod is None:\n')
        handle.write('        raise SystemExit("entry module not loaded: %s" % module_name)\n')
        handle.write('    func = getattr(mod, func_name, None)\n')
        handle.write('    if func is None:\n')
        handle.write('        raise SystemExit("entry function not found: %s" % entry_spec)\n')
        handle.write('    return func()\n\n')
        handle.write('def main():\n')
        handle.write('    root = _bootstrap_root()\n')
        handle.write('    _prepare_root(root, _MODULES)\n')
        handle.write('    _load_modules(root, _MODULES)\n')
        handle.write('    result = _run_entry(_ENTRY)\n')
        handle.write('    if isinstance(result, int):\n')
        handle.write('        return result\n')
        handle.write('    return 0\n\n')
        handle.write('if __name__ == "__main__":\n')
        handle.write('    sys.exit(main())\n')



def main(argv):
    parser = argparse.ArgumentParser(
        description='Generate a single-file bundle from a manifest.'
    )
    parser.add_argument(
        '--manifest',
        default='doc/flatten_manifest.txt',
        help='Path to manifest file (default: doc/flatten_manifest.txt)',
    )
    parser.add_argument(
        '--output',
        default='sfb_flat.py',
        help='Output path for the flattened file (default: sfb_flat.py)',
    )
    parser.add_argument(
        '--repo-root',
        default=None,
        help='Repository root (default: inferred from scripts/)',
    )
    parser.add_argument(
        '--minify',
        action='store_true',
        help='Minify module sources before bundling (requires python-minifier)',
    )
    parser.add_argument(
        '--minify-bin',
        default='pyminify',
        help='pyminify executable (default: pyminify)',
    )
    parser.add_argument(
        '--minify-globals',
        action='store_true',
        help='Allow minifier to rename module-level globals (unsafe across modules)',
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root
    if repo_root is None:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    else:
        repo_root = os.path.abspath(repo_root)

    manifest = _parse_manifest(args.manifest)
    excludes = [_normalize_path(p) for p in manifest['excludes']]
    module_paths, is_package = _collect_modules(
        repo_root,
        manifest['roots'],
        excludes,
    )
    _validate_manifest(manifest['modules'], module_paths, manifest['allow_late'])

    entry_module = manifest['entry'].split(':', 1)[0]
    if entry_module not in manifest['modules']:
        raise ManifestError('entry module not listed in manifest: %s' % entry_module)

    entries = []
    for name in manifest['modules']:
        path = module_paths[name]
        source = _read_text(path)
        entries.append((name, is_package[name], source, path))

    _validate_order(
        [(name, is_pkg, source) for name, is_pkg, source, _ in entries],
        manifest['allow_late'],
    )
    if args.minify:
        minified = []
        for name, is_pkg, source, path in entries:
            source = _minify_source(
                source,
                name,
                path,
                args.minify_globals,
                args.minify_bin,
            )
            minified.append((name, is_pkg, source))
        entries = minified
    else:
        entries = [(name, is_pkg, source) for name, is_pkg, source, _ in entries]

    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(repo_root, output_path)
    _write_output(output_path, manifest['entry'], entries, args.manifest)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ManifestError, ValidationError) as exc:
        sys.stderr.write(str(exc) + '\n')
        sys.exit(1)
