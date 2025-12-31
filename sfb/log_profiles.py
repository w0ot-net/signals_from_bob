# -*- coding: ascii -*-
"""
Named logging profiles for troubleshooting sessions.
"""

from __future__ import absolute_import


LOG_PROFILES = {
    # Enable ICMP transport logs (keeps default event blacklist).
    'icmp_transport': {
        'log_component_transport_icmp': True,
    },
    # Enable DNS transport logs (keeps default event blacklist).
    'dns_transport': {
        'log_component_transport_dns': True,
    },
    # Broad tunnel debugging (turns on protocol/channel and clears blacklist).
    'tunnel_verbose': {
        'log_component_tunnel': True,
        'log_component_channel': True,
        'log_component_protocol': True,
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
