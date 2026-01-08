#!/usr/bin/env python
# -*- coding: ascii -*-
"""
Flatten the sfb package into a single Python file with embedded sources.

Usage:
    python3 scripts/flatten.py --output /path/to/sfb_flat.py
    python3 scripts/flatten.py --output /path/to/sfb_flat.py --entry sfb.cli:main
"""

from __future__ import absolute_import

import argparse
import errno
import io
import os
import sys


_DEFAULT_ENTRY = 'sfb.cli:main'
_DEFAULT_PACKAGE = 'sfb'


def _repo_root():
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, os.pardir))


def _fail(message):
    sys.stderr.write('Error: %s\n' % message)
    sys.exit(2)


def _ensure_dir(path):
    if not path:
        return
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno != errno.EEXIST or not os.path.isdir(path):
            raise


def _collect_modules(package_root, package_name):
    modules = {}
    packages = set()
    for dirpath, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(path, package_root)
            parts = rel_path.split(os.sep)
            if parts[-1] == '__init__.py':
                mod_parts = parts[:-1]
                if mod_parts:
                    module_name = package_name + '.' + '.'.join(mod_parts)
                else:
                    module_name = package_name
                packages.add(module_name)
            else:
                mod_parts = list(parts)
                mod_parts[-1] = mod_parts[-1][:-3]
                module_name = package_name + '.' + '.'.join(mod_parts)
            modules[module_name] = path
    if package_name not in packages:
        init_path = os.path.join(package_root, '__init__.py')
        if os.path.isfile(init_path):
            modules[package_name] = init_path
            packages.add(package_name)
    return modules, packages


def _read_source(path):
    try:
        with io.open(path, 'r', encoding='ascii') as handle:
            data = handle.read()
    except (IOError, OSError, UnicodeDecodeError) as exc:
        raise ValueError('Failed to read %s: %s' % (path, exc))
    data = data.replace('\r\n', '\n').replace('\r', '\n')
    return data


def _quote_source(source):
    if "'''" not in source:
        quote = "'''"
    elif '"""' not in source:
        quote = '"""'
    else:
        return repr(source)
    escaped = source.replace('\\', '\\\\')
    if quote == "'''":
        escaped = escaped.replace("'''", "\\'\\'\\'")
    else:
        escaped = escaped.replace('"""', '\\"""')
    return quote + escaped + quote


def _parse_entry(value):
    if ':' in value:
        module_name, func_name = value.split(':', 1)
    else:
        module_name, func_name = value, 'main'
    module_name = module_name.strip()
    func_name = func_name.strip() or 'main'
    if not module_name:
        raise ValueError('entry module is empty')
    return module_name, func_name


def _emit_output(output_path, package_name, entry_module, entry_func,
                 modules, packages):
    output_dir = os.path.dirname(os.path.abspath(output_path))
    _ensure_dir(output_dir)

    sources = {}
    for module_name, path in sorted(modules.items()):
        sources[module_name] = _read_source(path)

    with io.open(output_path, 'w', encoding='ascii', newline='\n') as out:
        out.write('#!/usr/bin/env python\n')
        out.write('# -*- coding: ascii -*-\n')
        out.write('from __future__ import absolute_import\n')
        out.write('\n')
        out.write('import errno\n')
        out.write('import linecache\n')
        out.write('import os\n')
        out.write('import sys\n')
        out.write('import tempfile\n')
        out.write('import types\n')
        out.write('\n')
        out.write('_FLAT_PACKAGE = %r\n' % package_name)
        out.write('_FLAT_PACKAGES = set(%r)\n' % sorted(packages))
        out.write('_FLAT_SOURCES = {\n')
        for module_name in sorted(sources.keys()):
            out.write('    %r: ' % module_name)
            out.write(_quote_source(sources[module_name]))
            out.write(',\n')
        out.write('}\n')
        out.write('\n')
        out.write('def _ensure_dir(path):\n')
        out.write('    if not path:\n')
        out.write('        return\n')
        out.write('    try:\n')
        out.write('        os.makedirs(path)\n')
        out.write('    except OSError as exc:\n')
        out.write('        if exc.errno != errno.EEXIST or not os.path.isdir(path):\n')
        out.write('            raise\n')
        out.write('\n')
        out.write('def _flat_root():\n')
        out.write('    root = os.environ.get(\'SFB_FLAT_ROOT\')\n')
        out.write('    if root:\n')
        out.write('        root = os.path.abspath(root)\n')
        out.write('        _ensure_dir(root)\n')
        out.write('        return root\n')
        out.write('    return tempfile.mkdtemp(prefix=\'sfb_flat_\')\n')
        out.write('\n')
        out.write('def _build_file_map(root):\n')
        out.write('    file_map = {}\n')
        out.write('    for name in _FLAT_SOURCES:\n')
        out.write('        parts = name.split(\'.\')\n')
        out.write('        if name in _FLAT_PACKAGES:\n')
        out.write('            rel_path = os.path.join(*(parts + [\'__init__.py\']))\n')
        out.write('        else:\n')
        out.write('            rel_path = os.path.join(*parts) + \'.py\'\n')
        out.write('        file_path = os.path.join(root, rel_path)\n')
        out.write('        file_map[name] = file_path\n')
        out.write('        _ensure_dir(os.path.dirname(file_path))\n')
        out.write('    return file_map\n')
        out.write('\n')
        out.write('class _FlatImporter(object):\n')
        out.write('    def __init__(self, sources, packages, file_map):\n')
        out.write('        self._sources = sources\n')
        out.write('        self._packages = packages\n')
        out.write('        self._file_map = file_map\n')
        out.write('\n')
        out.write('    def find_module(self, fullname, path=None):\n')
        out.write('        if fullname in self._sources:\n')
        out.write('            return self\n')
        out.write('        return None\n')
        out.write('\n')
        out.write('    def load_module(self, fullname):\n')
        out.write('        if fullname in sys.modules:\n')
        out.write('            return sys.modules[fullname]\n')
        out.write('        source = self._sources.get(fullname)\n')
        out.write('        if source is None:\n')
        out.write('            raise ImportError(\'No module named %s\' % fullname)\n')
        out.write('        module = types.ModuleType(fullname)\n')
        out.write('        module.__file__ = self._file_map.get(fullname, fullname)\n')
        out.write('        if fullname in self._packages:\n')
        out.write('            module.__package__ = fullname\n')
        out.write('            module.__path__ = [os.path.dirname(module.__file__)]\n')
        out.write('        else:\n')
        out.write('            module.__package__ = fullname.rpartition(\'.\')[0]\n')
        out.write('        module.__loader__ = self\n')
        out.write('        sys.modules[fullname] = module\n')
        out.write('        linecache.cache[module.__file__] = (\n')
        out.write('            len(source), None, source.splitlines(True), module.__file__\n')
        out.write('        )\n')
        out.write('        code = compile(source, module.__file__, \'exec\')\n')
        out.write('        exec(code, module.__dict__)\n')
        out.write('        return module\n')
        out.write('\n')
        out.write('def _install_flat_importer():\n')
        out.write('    for existing in sys.meta_path:\n')
        out.write('        if isinstance(existing, _FlatImporter):\n')
        out.write('            return\n')
        out.write('    root = _flat_root()\n')
        out.write('    file_map = _build_file_map(root)\n')
        out.write('    sys.meta_path.insert(0, _FlatImporter(_FLAT_SOURCES, _FLAT_PACKAGES, file_map))\n')
        out.write('\n')
        out.write('_install_flat_importer()\n')
        out.write('\n')
        out.write('_ENTRY_MODULE = %r\n' % entry_module)
        out.write('_ENTRY_FUNC = %r\n' % entry_func)
        out.write('\n')
        out.write('def _run_entry():\n')
        out.write('    module = __import__(_ENTRY_MODULE, fromlist=[\'*\'])\n')
        out.write('    func = getattr(module, _ENTRY_FUNC)\n')
        out.write('    return func()\n')
        out.write('\n')
        out.write('if __name__ == \'__main__\':\n')
        out.write('    sys.exit(_run_entry())\n')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Flatten the sfb package into a single Python file.'
    )
    parser.add_argument(
        '--output',
        required=True,
        help='Path for the generated flat file.',
    )
    parser.add_argument(
        '--entry',
        default=_DEFAULT_ENTRY,
        help='Entrypoint as module[:func] (default: %s)' % _DEFAULT_ENTRY,
    )
    parser.add_argument(
        '--package',
        default=_DEFAULT_PACKAGE,
        help='Root package to flatten (default: %s)' % _DEFAULT_PACKAGE,
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    package_root = os.path.join(repo_root, args.package)
    if not os.path.isdir(package_root):
        _fail('Package root not found: %s' % package_root)

    try:
        entry_module, entry_func = _parse_entry(args.entry)
    except ValueError as exc:
        _fail(str(exc))

    modules, packages = _collect_modules(package_root, args.package)
    if not modules:
        _fail('No modules found under %s' % package_root)

    try:
        _emit_output(
            args.output,
            args.package,
            entry_module,
            entry_func,
            modules,
            packages,
        )
    except (IOError, OSError, ValueError) as exc:
        _fail(str(exc))


if __name__ == '__main__':
    main()
