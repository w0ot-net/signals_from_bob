# -*- coding: ascii -*-
"""
Transport MTU resolution helpers.
"""

from __future__ import absolute_import

from .transport_base import TransportError
from ..protocol.constants import DEFAULT_MAX_PACKET_SIZE, MIN_PACKET_MTU
_IPV4_MAX_TOTAL_LEN = 65535
_IPV4_HEADER_LEN = 20
_UDP_HEADER_LEN = 8
_ICMP_HEADER_LEN = 8

_UDP_MAX_PAYLOAD = _IPV4_MAX_TOTAL_LEN - _IPV4_HEADER_LEN - _UDP_HEADER_LEN
_ICMP_MAX_PAYLOAD = _IPV4_MAX_TOTAL_LEN - _IPV4_HEADER_LEN - _ICMP_HEADER_LEN


def resolve_mtu_limits(transport, config, role, send_packet_mtu=None,
                       recv_packet_mtu=None, validated=None):
    """
    Resolve transport MTU limits.

    Args:
        transport: transport name
        config: Config instance
        role: 'client' or 'server'
        send_packet_mtu: optional override for in-memory transport
        recv_packet_mtu: optional override for in-memory transport
        validated: optional validated config (TLS/TLS bump)

    Returns:
        tuple: (send_packet_mtu, recv_packet_mtu, min_packet_mtu, constraints)
    """
    if role not in ('client', 'server'):
        raise TransportError('Invalid transport role: %s' % role)
    if transport == 'dns':
        return _resolve_dns_limits(config, role)
    if transport == 'dns_txt':
        return _resolve_dns_txt_limits(config, role)
    if transport == 'icmp':
        return _resolve_icmp_limits(config)
    if transport == 'udp_ephemeral':
        return _resolve_udp_ephemeral_limits(config)
    if transport == 'tls_handshake':
        return _resolve_tls_handshake_limits(config, role, validated)
    if transport == 'tls_handshake_bump':
        return _resolve_tls_bump_limits(config, role, validated)
    if transport == 'memory':
        return _resolve_memory_limits(send_packet_mtu, recv_packet_mtu)
    raise TransportError('Unsupported transport for MTU resolution: %s' %
                         transport)


def _resolve_dns_limits(config, role):
    from .dns import dns_codec

    base_domain = config.dns_base_domain.lower().rstrip('.')
    label_max_len = config.dns_label_max_len
    cname_label = config.dns_cname_label.strip('.')
    cname_suffix = '%s.%s' % (cname_label, base_domain)
    rtype = dns_codec.RECORD_TYPES[config.dns_response_type]

    query_mtu = dns_codec.calc_query_mtu(base_domain, label_max_len)
    response_mtu = dns_codec.calc_response_mtu(
        rtype,
        config.dns_edns_size,
        cname_suffix,
        label_max_len,
    )
    if query_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'DNS query MTU %d below minimum %d (base_domain=%s, '
            'label_max_len=%d)' % (
                query_mtu,
                MIN_PACKET_MTU,
                base_domain,
                label_max_len,
            )
        )
    if response_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'DNS response MTU %d below minimum %d (base_domain=%s, '
            'label_max_len=%d, edns_size=%d)' % (
                response_mtu,
                MIN_PACKET_MTU,
                base_domain,
                label_max_len,
                config.dns_edns_size,
            )
        )

    if role == 'client':
        send_packet_mtu = query_mtu
        recv_packet_mtu = response_mtu
    else:
        send_packet_mtu = response_mtu
        recv_packet_mtu = query_mtu

    constraints = {
        'base_domain_len': len(base_domain),
        'label_max_len': label_max_len,
        'cname_label_len': len(cname_label),
        'cname_suffix_len': len(cname_suffix),
        'edns_size': config.dns_edns_size,
        'qtype': config.dns_query_type,
        'rtype': config.dns_response_type,
    }
    return send_packet_mtu, recv_packet_mtu, MIN_PACKET_MTU, constraints


def _resolve_dns_txt_limits(config, role):
    from .dns_txt import dns_txt_codec

    base_domain = config.dns_base_domain.lower().rstrip('.')
    label_max_len = config.dns_label_max_len

    query_mtu = dns_txt_codec.calc_query_mtu(base_domain, label_max_len)
    response_mtu = dns_txt_codec.calc_response_mtu(
        dns_txt_codec.QTYPE_TXT,
        config.dns_edns_size,
    )
    user_cap = getattr(config, 'dns_txt_response_cap', None)
    if user_cap is not None:
        try:
            user_cap = int(user_cap)
        except (TypeError, ValueError):
            raise TransportError('dns_txt_response_cap must be an integer')
    opt_record_len = 0
    if config.dns_edns_size > dns_txt_codec.DNS_STANDARD_SIZE:
        opt_record_len = len(
            dns_txt_codec.build_opt_record(config.dns_edns_size)
        )
    auto_cap = None
    qname_wire_len = dns_txt_codec.calc_qname_wire_len(
        query_mtu,
        base_domain,
        label_max_len,
    )
    auto_cap, _ = dns_txt_codec.calc_txt_response_payload_cap(
        qname_wire_len,
        config.dns_edns_size,
        opt_record_len,
    )
    if auto_cap is not None and auto_cap < response_mtu:
        response_mtu = auto_cap
    if user_cap is not None and user_cap < response_mtu:
        response_mtu = user_cap
    if query_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'DNS TXT query MTU %d below minimum %d (base_domain=%s, '
            'label_max_len=%d)' % (
                query_mtu,
                MIN_PACKET_MTU,
                base_domain,
                label_max_len,
            )
        )
    if response_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'DNS TXT response MTU %d below minimum %d (base_domain=%s, '
            'label_max_len=%d, edns_size=%d)' % (
                response_mtu,
                MIN_PACKET_MTU,
                base_domain,
                label_max_len,
                config.dns_edns_size,
            )
        )

    if role == 'client':
        send_packet_mtu = query_mtu
        recv_packet_mtu = response_mtu
    else:
        send_packet_mtu = response_mtu
        recv_packet_mtu = query_mtu

    constraints = {
        'base_domain_len': len(base_domain),
        'label_max_len': label_max_len,
        'edns_size': config.dns_edns_size,
        'qtype': 'TXT',
        'rtype': 'TXT',
    }
    cap = auto_cap
    if user_cap is not None:
        cap = user_cap
    if cap is not None:
        constraints['dns_txt_response_cap'] = cap
    if user_cap is not None and auto_cap is not None and auto_cap != cap:
        constraints['dns_txt_response_cap_auto'] = auto_cap
    return send_packet_mtu, recv_packet_mtu, MIN_PACKET_MTU, constraints


def _resolve_icmp_limits(config):
    cap = int(config.icmp_packet_mtu)
    packet_mtu = min(cap, _ICMP_MAX_PAYLOAD)
    if packet_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'ICMP packet MTU %d below minimum %d' %
            (packet_mtu, MIN_PACKET_MTU)
        )
    constraints = {
        'icmp_packet_mtu_cap': cap,
        'transport_max_mtu': _ICMP_MAX_PAYLOAD,
    }
    return packet_mtu, packet_mtu, MIN_PACKET_MTU, constraints


def _resolve_udp_ephemeral_limits(config):
    cap = int(config.udp_ephemeral_packet_mtu)
    packet_mtu = min(cap, _UDP_MAX_PAYLOAD)
    if packet_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'UDP packet MTU %d below minimum %d' %
            (packet_mtu, MIN_PACKET_MTU)
        )
    constraints = {
        'udp_ephemeral_packet_mtu_cap': cap,
        'transport_max_mtu': _UDP_MAX_PAYLOAD,
    }
    return packet_mtu, packet_mtu, MIN_PACKET_MTU, constraints


def _resolve_tls_handshake_limits(config, role, validated):
    if validated is None:
        from .tls_handshake.tls_handshake_config import validate_tls_config
        validated = validate_tls_config(config, role)

    client_payload_cap = validated['client_payload_cap']
    server_payload_cap = validated['server_payload_cap']
    if role == 'client':
        send_packet_mtu = client_payload_cap
        recv_packet_mtu = server_payload_cap
    else:
        send_packet_mtu = server_payload_cap
        recv_packet_mtu = client_payload_cap

    if send_packet_mtu < MIN_PACKET_MTU or recv_packet_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'TLS packet MTU below minimum %d (client_cap=%d, server_cap=%d)' %
            (MIN_PACKET_MTU, client_payload_cap, server_payload_cap)
        )

    sni = validated.get('sni')
    alpn_list = validated.get('alpn_list')
    constraints = {
        'max_clienthello_bytes': validated['max_clienthello_bytes'],
        'max_serverhello_bytes': validated['max_serverhello_bytes'],
        'client_payload_cap': client_payload_cap,
        'server_payload_cap': server_payload_cap,
        'sni_len': len(sni) if sni else 0,
        'alpn_count': len(alpn_list) if alpn_list else 0,
        'clienthello_padding_target': validated['clienthello_padding_target'],
    }
    return send_packet_mtu, recv_packet_mtu, MIN_PACKET_MTU, constraints


def _resolve_tls_bump_limits(config, role, validated):
    from .tls_handshake_bump import (
        tls_handshake_bump_cert_template as bump_cert_template,
    )

    if validated is None:
        from .tls_handshake_bump.tls_handshake_bump_config import (
            validate_tls_bump_config,
        )
        validated = validate_tls_bump_config(config, role)

    sni_payload_cap = validated['sni_payload_cap']
    cn_payload_cap = validated['cn_payload_cap']
    if role == 'client':
        send_packet_mtu = sni_payload_cap
        recv_packet_mtu = cn_payload_cap
    else:
        send_packet_mtu = cn_payload_cap
        recv_packet_mtu = sni_payload_cap

    if send_packet_mtu < MIN_PACKET_MTU or recv_packet_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'TLS bump packet MTU below minimum %d (sni_cap=%d, cn_cap=%d)' %
            (MIN_PACKET_MTU, sni_payload_cap, cn_payload_cap)
        )

    base_domain = validated['base_domain']
    constraints = {
        'base_domain_len': len(base_domain),
        'sni_payload_cap': sni_payload_cap,
        'cn_payload_cap': cn_payload_cap,
        'cn_max_len': validated['cn_max_len'],
        'cn_template_len': bump_cert_template.CN_LEN,
        'tls_bump_max_clienthello_bytes': validated['max_clienthello_bytes'],
    }
    return send_packet_mtu, recv_packet_mtu, MIN_PACKET_MTU, constraints


def _resolve_memory_limits(send_packet_mtu, recv_packet_mtu):
    if not send_packet_mtu:
        send_packet_mtu = DEFAULT_MAX_PACKET_SIZE
    else:
        send_packet_mtu = int(send_packet_mtu)
    if not recv_packet_mtu:
        recv_packet_mtu = DEFAULT_MAX_PACKET_SIZE
    else:
        recv_packet_mtu = int(recv_packet_mtu)

    if send_packet_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'In-memory send MTU %d below minimum %d' %
            (send_packet_mtu, MIN_PACKET_MTU)
        )
    if recv_packet_mtu < MIN_PACKET_MTU:
        raise TransportError(
            'In-memory recv MTU %d below minimum %d' %
            (recv_packet_mtu, MIN_PACKET_MTU)
        )

    constraints = {
        'default_packet_mtu': DEFAULT_MAX_PACKET_SIZE,
    }
    return send_packet_mtu, recv_packet_mtu, MIN_PACKET_MTU, constraints
