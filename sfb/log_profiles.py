# -*- coding: ascii -*-
"""
Named logging profiles for troubleshooting sessions.
"""

from __future__ import absolute_import


LOG_PROFILES = {
    # SCP stall debugging over ICMP + SOCKS.
    'scp_stalled_icmp_socks': {
        'log_component_transport_icmp': True,
        'log_component_tunnel': True,
        'log_component_channel': True,
        'log_component_module_socks': True,
        'log_event_whitelist': (
            'cli.*',
            'sock.connect*',
            'sock.pump_stats',
            'channel.open*',
            'channel.close*',
            'channel.send_buf_*',
            'channel.drain',
            'tunnel.send_blocked',
            'tunnel.send_window_*',
            'tunnel.retransmit*',
            'tunnel.packet_decode_failed',
            'tunnel.state',
            'tunnel.window_*',
            'tunnel.mtu_*',
            'tunnel.timeout_*',
            'tunnel.connected',
            'tunnel.closed',
            'icmp.*',
        ),
        'log_event_blacklist': (),
    },
    # Focused ICMP retransmit troubleshooting (default during debug).
    'icmp_retransmit_debug': {
        'log_component_transport_icmp': True,
        'log_component_tunnel': True,
        'log_component_channel': True,
        'log_event_whitelist': (
            'cli.*',
            'tunnel.packet_*',
            'tunnel.packet_decode_failed',
            'tunnel.ack',
            'tunnel.retransmit*',
            'tunnel.send_blocked',
            'tunnel.recv_window',
            'tunnel.deliver_segments',
            'tunnel.state',
            'tunnel.window_*',
            'tunnel.mtu_*',
            'tunnel.timeout_*',
            'tunnel.handshake_*',
            'tunnel.ack_send_failed',
            'tunnel.serve_error',
            'tunnel.connected',
            'tunnel.closed',
            'tunnel.response_cap',
            'tunnel.send_window_*',
            'tunnel.request_state_unexpected',
            'tunnel.tick_error',
            'tunnel.bg_error',
            'icmp.*',
            'channel.open*',
            'channel.close*',
            'channel.send_buf_*',
            'channel.pack',
            'channel.drain',
            'sock.connect*',
        ),
        'log_event_blacklist': (),
    },
    # Enable ICMP transport logs (keeps default event blacklist).
    'icmp_transport': {
        'log_component_transport_icmp': True,
    },
    # Enable DNS transport logs (keeps default event blacklist).
    'dns_transport': {
        'log_component_transport_dns': True,
    },
    # SOCKS channel starvation debugging (focused on fairness signals).
    'socks_starvation': {
        'log_component_tunnel': True,
        'log_component_channel': True,
        'log_component_module_socks': True,
        'log_event_whitelist': (
            'cli.*',
            'sock.connect*',
            'channel.open*',
            'channel.close*',
            'channel.drain',
            'tunnel.send_blocked',
            'tunnel.send_window_distance',
            'tunnel.send_window_full',
            'tunnel.send_window_inconsistent',
            'tunnel.retransmit*',
            'tunnel.state',
            'tunnel.window_*',
        ),
        'log_event_blacklist': (
            'tunnel.packet_*',
            'channel.pack',
            'channel.send_buf_*',
            'sock.pump_stats',
        ),
    },
    # Broad tunnel debugging (turns on protocol/channel and clears blacklist).
    'tunnel_verbose': {
        'log_component_tunnel': True,
        'log_component_channel': True,
        'log_component_protocol': True,
        'log_event_blacklist': (),
    },
    # Focused SOCKS+ICMP throughput debugging.
    'socks_throughput_debug': {
        'log_component_transport_icmp': True,
        'log_component_tunnel': True,
        'log_component_module_socks': True,
        'log_event_whitelist': (
            'sock.pump_stats',
            'sock.relay_*',
            'channel.send_buf_*',
            'channel.drain',
            'channel.write_wait',
            'icmp.send',
            'icmp.recv',
            'icmp.send_blocked',
            'tunnel.window_*',
            'tunnel.send_blocked',
            'tunnel.packet_*',
        ),
        'log_event_blacklist': (),
    },
    # Disable all component logging (errors still pass).
    'no_logging': {
        'log_component_transport_dns': False,
        'log_component_transport_icmp': False,
        'log_component_tunnel': False,
        'log_component_channel': False,
        'log_component_protocol': False,
        'log_component_module_socks': False,
        'log_component_module_file_transfer': False,
        'log_event_whitelist': (),
        'log_event_blacklist': ('*',),
    },
    # Adaptive pacing state tracing (minimal noise).
    'tunnel_pacing': {
        'log_component_tunnel': True,
        'log_event_whitelist': (
            'cli.*',
            'tunnel.state',
            'tunnel.wait',
            'tunnel.handshake_*',
            'tunnel.ack_send_failed',
            'tunnel.mtu_*',
            'tunnel.window_*',
            'tunnel.timeout_*',
            'tunnel.connected',
            'tunnel.closed',
            'tunnel.pacer_state',
        ),
        'log_event_blacklist': (),
    },
}


def apply_log_profile(config, name, overrides=None):
    """
    Apply a named logging profile to a Config instance.

    Args:
        config: Config instance to update.
        name: Profile name (must exist in LOG_PROFILES).
        overrides: Optional dict of per-key overrides.

    Returns:
        dict of applied settings.
    """
    if not name:
        return None
    profile = LOG_PROFILES.get(name)
    if profile is None:
        raise ValueError('Unknown log profile: %s' % name)
    settings = {}
    settings.update(profile)
    if overrides:
        settings.update(overrides)
    for key, value in settings.items():
        if not hasattr(config, key):
            raise ValueError('Unknown log config key in profile: %s' % key)
        setattr(config, key, value)
    return settings
