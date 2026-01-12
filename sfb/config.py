# -*- coding: ascii -*-
"""
Centralized configuration for Signals from Bob.

All configurable values in one place with sensible defaults.
"""

from .protocol.constants import PACKET_HEADER_SIZE

DNS_STANDARD_SIZE = 512
DNS_EDNS_MAX_SIZE = 4096


class Config(object):
    """
    Configuration for SFB tunnels.

    Use the same Config instance for both Alice and Bob - each side
    uses the fields relevant to its role.
    """

    # --- DNS Transport ---
    # Base domain for DNS tunneling (required for DNS transport)
    dns_base_domain = ""
    # Default transport for CLI
    transport_default = "dns"
    # Active transport name (None = transport_default)
    transport = None
    # DNS resolver address for Alice (IPv4 only, e.g., '1.1.1.1:53'),
    # None = system default
    dns_resolver = None
    # Listen address for Bob's DNS server (IPv4 only)
    dns_listen_addr = "0.0.0.0:53"
    # EDNS0 buffer size (default 512, 4096 max)
    dns_edns_size = 512
    # Minimum UDP recv buffer size for DNS responses/queries
    dns_recv_bufsize_min = 4096
    # Timeout before considering a DNS query stale (seconds)
    dns_pending_timeout = 5.0
    # Query type for DNS tunneling (currently fixed to 'A')
    dns_query_type = "A"
    # Response type for DNS tunneling
    dns_response_type = "CNAME"
    # TTL for DNS answers (seconds)
    dns_response_ttl = 3600
    # Max label length for tunnel subdomains (1-63, default 50)
    dns_label_max_len = 50
    # CNAME label appended before base domain (short suffix)
    dns_cname_label = "0"
    # IPv4 address returned for CNAME follow-up A queries
    dns_cname_a_addr = "0.0.0.0"
    # DNS flat stager chunks (server-only)
    dns_flat_chunks = None
    # DNS flat stager chunk count (server-only)
    dns_flat_count = None
    # DNS flat stager metadata payload (server-only)
    dns_flat_meta = None
    # DNS flat stager chunk size (server-only)
    dns_flat_chunk_size = None
    # DNS flat stager index seed (server-only, None = random)
    dns_flat_index_seed = 0x1A2B3C4D

    # --- ICMP Transport ---
    # Target host/IPv4 for Alice
    icmp_target = None
    # ICMP packet MTU cap in packet bytes (advanced override)
    icmp_packet_mtu = 1350
    # Timeout before considering an ICMP request stale (seconds)
    icmp_pending_timeout = 10.0
    # Optional ICMP socket receive buffer size (bytes, 0 = default)
    # Double the normal default would be 2 * /proc/sys/net/core/rmem_default.
    icmp_socket_rcvbuf = 425984

    # --- UDP Ephemeral Transport ---
    # Target host:port for Alice (IPv4 only)
    udp_ephemeral_target = None
    # Listen host:port for Bob (IPv4 only)
    udp_ephemeral_listen_addr = "0.0.0.0:53"
    # UDP packet MTU cap in packet bytes (advanced override)
    udp_ephemeral_packet_mtu = 1350
    # Timeout before considering a UDP request stale (seconds)
    udp_ephemeral_pending_timeout = 5.0
    # Seconds before reusing a UDP source port
    udp_ephemeral_source_port_reuse_seconds = 0.0

    # --- TLS ClientHello Transport ---
    # Alice target host:port (IPv4 only)
    tls_target = "127.0.0.1:443"
    # Optional HTTP CONNECT proxy host:port for Alice (IPv4 only)
    tls_http_proxy = None
    # Optional HTTP proxy Basic auth (user:pass) for Alice
    tls_http_proxy_auth = None
    # Bob listen host:port (IPv4 only)
    tls_listen_addr = "0.0.0.0:443"
    # Timeout before considering a TLS request stale (seconds)
    tls_pending_timeout = 5.0
    # TLS connect timeout (seconds)
    tls_connect_timeout = 3.0
    # TLS HTTP proxy handshake timeout (seconds, None = connect timeout)
    tls_proxy_timeout = None
    # TLS handshake timeout (seconds)
    tls_handshake_timeout = 5.0
    # Max on-wire ClientHello record size (bytes, includes 5-byte header)
    tls_max_clienthello_bytes = 1400
    # Max on-wire ServerHello record size (bytes, includes 5-byte header)
    tls_max_serverhello_bytes = 1400
    # Optional SNI cover name
    tls_sni = "example.com"
    # Optional comma-separated ALPN list
    tls_alpn = "h2,http/1.1"
    # Target on-wire ClientHello record size for padding (bytes, 0 = disabled)
    tls_clienthello_padding_target = 0

    # --- TLS Handshake Bump Transport ---
    # Base domain for TLS bump SNI encoding
    tls_bump_base_domain = "example.com"
    # TLS bump proxy host:port for Alice (IPv4 only)
    tls_bump_target = "127.0.0.1:443"
    # Optional HTTP CONNECT proxy host:port for Alice (IPv4 only)
    tls_bump_http_proxy = None
    # Optional HTTP proxy Basic auth (user:pass) for Alice
    tls_bump_http_proxy_auth = None
    # Bob listen host:port (IPv4 only)
    tls_bump_listen_addr = "0.0.0.0:443"
    # TLS bump connect timeout (seconds)
    tls_bump_connect_timeout = 3.0
    # TLS bump HTTP proxy handshake timeout (seconds, None = connect timeout)
    tls_bump_proxy_timeout = None
    # TLS bump handshake timeout (seconds)
    tls_bump_handshake_timeout = 5.0
    # HTTPS request path to trigger proxy error page
    tls_bump_request_path = "/"
    # Max ClientHello record size (bytes, includes 5-byte header)
    tls_bump_max_clienthello_bytes = 4096
    # Optional CN max length override for TLS bump client receive MTU
    tls_bump_cn_max_len = None

    # --- Crypto ---
    # Encryption mode: 'none', 'xor', 'rc4', 'sha256'
    crypto_mode = "none"
    # Pre-shared key for xor/rc4/sha256 (bytes or string)
    crypto_psk = None

    # --- Tunnel ---
    # Alice: seconds between keepalive packets
    tunnel_keepalive_interval = 1.0
    # Alice: immediate poll attempts after keepalive-only responses (legacy "pong")
    tunnel_pong_grace_polls = 5
    # Bob: seconds of inactivity before considering connection dead
    tunnel_idle_timeout = 60.0
    # Initial window size before negotiation (packets)
    tunnel_initial_window = 1
    # Maximum unacknowledged packets in flight (max 256, SACK bitmap limit)
    max_in_flight = 256
    # Handshake/connection timeout (seconds)
    tunnel_connect_timeout = 10.0
    # Alice: seconds without response before giving up
    tunnel_no_response_timeout = 60.0
    # Alice: max retransmits per tick (RTO + fast retransmit)
    tunnel_retransmit_cap = 2
    # Alice: enable fast retransmit for SACK holes
    tunnel_fast_retransmit_enabled = True
    # Alice: minimum age ratio of RTO before fast retransmit
    tunnel_fast_retransmit_min_age_ratio = 0.25
    # Alice: max fast retransmits per sequence number
    tunnel_fast_retransmit_max_per_seq = 2
    # Enable runtime stats tracking (set by -v)
    stats_enabled = False
    # Enable dynamic window growth on Alice
    tunnel_window_growth_enabled = True
    # Window growth mode: 'linear' or 'doubling'
    tunnel_window_growth_mode = "linear"
    # Window growth step (linear mode)
    tunnel_window_growth_step = 1
    # Minimum seconds between window growth requests
    tunnel_window_growth_interval = 2.0
    # Background loop stop timeout (seconds)
    tunnel_bg_stop_timeout = 2.0
    # Bob: poll timeout for serve_forever (seconds)
    tunnel_bob_poll_interval = 1.0
    # Bob: poll timeout for background loop (seconds)
    tunnel_bob_poll_interval_bg = 0.1
    # Bob: min seconds between retransmits of the oldest unacked packet
    tunnel_bob_retransmit_min_interval = 0.02
    # Bob: max seconds between retransmits of the oldest unacked packet
    tunnel_bob_retransmit_max_interval = 3.0
    # Bob: multiplier for poll EWMA to derive retransmit cooldown
    tunnel_bob_retransmit_poll_factor = 2.0
    # Bob: EWMA alpha for poll interval smoothing (0-1)
    tunnel_bob_poll_ewma_alpha = 0.2
    # Alice: sleep between ticks when running (seconds)
    tunnel_tick_sleep = 0.001
    # Alice: max send rate (packets per second, 0 = unlimited)
    tunnel_send_rate = 0.0
    # Alice: burst capacity for send rate (packets, None=rate)
    tunnel_send_burst = None
    # Alice: adaptive pacing enabled
    tunnel_adaptive_pacing_enabled = True
    # Alice: adaptive pacing target inflight ratio
    tunnel_pace_target_inflight_ratio = 1.0
    # Alice: adaptive pacing minimum inflight target
    tunnel_pace_min_inflight = 1
    # Alice: adaptive pacing maximum inflight target (None = cap)
    tunnel_pace_max_inflight = None
    # Alice: adaptive pacing feedback gain
    tunnel_pace_feedback_gain = 1.25
    # Alice: adaptive pacing ACK rate EWMA alpha
    tunnel_pace_ack_ewma_alpha = 0.2
    # Alice: adaptive pacing RTT floor in milliseconds
    tunnel_pace_rtt_floor_ms = 5.0
    # Alice: adaptive pacing ACK idle reset seconds
    tunnel_pace_ack_idle_reset_sec = 2.0
    # Alice: pacing summary log interval (seconds, 0 = disabled)
    tunnel_pacer_summary_interval = 0.0
    # Alice: poll pacing enabled
    tunnel_poll_pacing_enabled = True
    # Alice: minimum seconds between polls
    tunnel_poll_min_interval = 0.0005
    # Alice: maximum seconds between polls
    tunnel_poll_max_interval = 1.0
    # Alice: fraction of RTT to distribute target inflight
    tunnel_poll_rtt_ratio = 0.75
    # Bob: poll interval while waiting for connection (seconds)
    tunnel_connect_poll_interval = 0.1
    # Small timeout for "non-blocking" polls to prevent busy loops (seconds)
    non_blocking_poll_timeout = 0.0001

    # --- Channel ---
    # Maximum bytes to buffer for sending per channel
    channel_max_send_buf = 1048576
    # Maximum bytes to buffer for receiving per channel
    channel_max_recv_buf = 1048576
    # Timeout waiting for channel to open (seconds)
    channel_open_timeout = 5.0
    # Write backoff initial delay (seconds)
    channel_write_backoff_initial = 0.01
    # Write backoff maximum delay (seconds)
    channel_write_backoff_max = 1.0
    # Control channel read chunk size (bytes)
    channel_control_read_chunk = 4096
    # Cooldown before reusing a closed channel ID (seconds, 0 = disabled)
    channel_id_reuse_cooldown = 10.0

    # --- File Transfer ---
    # Maximum file size to transfer (bytes), None = unlimited
    file_transfer_max_size = None
    # Chunk size for file I/O (bytes)
    file_transfer_chunk_size = 4096
    # Timeout waiting for hash verification (seconds)
    file_transfer_hash_timeout = 10.0
    # File transfer root for Bob
    file_transfer_root = "."
    # File transfer command timeout (seconds), None = no timeout
    file_transfer_timeout = None
    # Maximum concurrent active file transfers (per module instance)
    file_transfer_max_active = 1

    # --- NC Linux ---
    # Bind request timeout (seconds)
    nc_linux_bind_timeout = 10.0
    # TCP connect timeout for host:port specs (seconds)
    nc_linux_connect_timeout = 10.0
    # Pump buffer size (bytes)
    nc_linux_buffer_size = 4096
    # Pump poll timeout (seconds)
    nc_linux_poll_timeout = 0.01

    # --- Modules ---
    # Module shutdown join timeout (seconds)
    module_shutdown_timeout = 5.0

    # --- Logging ---
    # SQLite log path (None = disabled)
    db_log_path = None
    # SQLite log flush interval (seconds)
    db_log_flush = 2.0
    # SQLite log queue max size (0 = unbounded)
    db_log_queue = 0
    # Default logging profile name (None = no profile)
    log_profile = None
    # Enable DNS transport logging (stdout + SQLite)
    log_component_transport_dns = False
    # Enable ICMP transport logging (stdout + SQLite)
    log_component_transport_icmp = False
    # Enable TLS transport logging (stdout + SQLite)
    log_component_transport_tls = False
    # Enable tunnel logging (stdout + SQLite)
    log_component_tunnel = True
    # Enable channel logging (stdout + SQLite)
    log_component_channel = False
    # Enable protocol logging (stdout + SQLite)
    log_component_protocol = False
    # Structured event whitelist (empty = allow all events)
    log_event_whitelist = ()
    # Structured event blacklist (empty = deny none)
    # Default blacklist reduces high-volume debug events.
    log_event_blacklist = (
        'tunnel.packet_*',
        'tunnel.ack',
        'tunnel.send_blocked',
        'tunnel.recv_window',
        'tunnel.deliver_segments',
        'tunnel.control_dispatch',
        'tunnel.control_processed',
        'tunnel.command',
        'module.send',
        'module.recv',
        'sock.pump_stats',
        'fwd.pump_stats',
        'channel.drain',
        'channel.pack',
        'channel.send_buf_*',
        'channel.write_wait',
        'dns.send',
        'dns.recv',
        'icmp.send',
        'icmp.recv',
        'tls.send',
        'tls.recv',
    )
    # Enable relay module logging (stdout + SQLite)
    log_component_module_relay = True
    # Enable file transfer module logging (stdout + SQLite)
    log_component_module_file_transfer = True
    # Enable nc_linux module logging (stdout + SQLite)
    log_component_module_nc_linux = True

    # --- Relay ---
    # Relay server listen host
    relay_listen_host = "0.0.0.0"
    # Relay server listen port
    relay_listen_port = 1080
    # Relay server listen backlog
    relay_listen_backlog = 5
    # Relay accept loop timeout (seconds)
    relay_accept_timeout = 0.5
    # Channel open timeout for relay (seconds)
    relay_channel_open_timeout = 10.0
    # Relay connect request timeout (seconds)
    relay_connect_timeout = 30.0
    # Target connect timeout for relay (seconds)
    relay_target_connect_timeout = 30.0
    # Relay socket timeout during handshake/connect (seconds)
    relay_socket_timeout = 5.0
    # Relay channel read poll timeout (seconds)
    relay_channel_timeout = 0.5
    # Relay send stall timeout for non-blocking pumps (seconds)
    relay_write_timeout = None
    # Relay buffer size (bytes)
    relay_buffer_size = 2048
    # Relay pump poll timeout (seconds)
    relay_pump_poll_timeout = 0.0001
    # Maximum poll backoff for relay pump select/wait loops (seconds)
    relay_pump_backoff_max = 0.001
    # Relay thread join timeout (seconds)
    relay_thread_join_timeout = 2.0

    # --- Protocol (rarely need changing) ---
    # Buffer-sizing maximum packet size (bytes), not a transport MTU cap
    protocol_max_packet_mtu = 1450
    # Initial packet MTU before negotiation (bytes)
    protocol_initial_packet_mtu = PACKET_HEADER_SIZE + 100
    # Initial retransmission timeout (milliseconds)
    protocol_initial_rto_ms = 1000
    # Minimum RTO (milliseconds)
    protocol_min_rto_ms = 500
    # Maximum RTO (milliseconds)
    protocol_max_rto_ms = 10000

    _FIELDS = (
        'dns_base_domain',
        'transport_default',
        'transport',
        'dns_resolver',
        'dns_listen_addr',
        'dns_edns_size',
        'dns_recv_bufsize_min',
        'dns_pending_timeout',
        'dns_query_type',
        'dns_response_type',
        'dns_label_max_len',
        'dns_cname_label',
        'dns_cname_a_addr',
        'dns_flat_chunks',
        'dns_flat_count',
        'dns_flat_meta',
        'dns_flat_chunk_size',
        'dns_flat_index_seed',
        'icmp_target',
        'icmp_packet_mtu',
        'icmp_pending_timeout',
        'udp_ephemeral_target',
        'udp_ephemeral_listen_addr',
        'udp_ephemeral_packet_mtu',
        'udp_ephemeral_pending_timeout',
        'udp_ephemeral_source_port_reuse_seconds',
        'tls_target',
        'tls_http_proxy',
        'tls_http_proxy_auth',
        'tls_listen_addr',
        'tls_pending_timeout',
        'tls_connect_timeout',
        'tls_proxy_timeout',
        'tls_handshake_timeout',
        'tls_max_clienthello_bytes',
        'tls_max_serverhello_bytes',
        'tls_sni',
        'tls_alpn',
        'tls_clienthello_padding_target',
        'tls_bump_base_domain',
        'tls_bump_target',
        'tls_bump_http_proxy',
        'tls_bump_http_proxy_auth',
        'tls_bump_listen_addr',
        'tls_bump_connect_timeout',
        'tls_bump_proxy_timeout',
        'tls_bump_handshake_timeout',
        'tls_bump_request_path',
        'tls_bump_max_clienthello_bytes',
        'tls_bump_cn_max_len',
        'crypto_mode',
        'crypto_psk',
        'tunnel_keepalive_interval',
        'tunnel_pong_grace_polls',
        'tunnel_idle_timeout',
        'tunnel_initial_window',
        'max_in_flight',
        'tunnel_connect_timeout',
        'tunnel_no_response_timeout',
        'tunnel_retransmit_cap',
        'tunnel_fast_retransmit_enabled',
        'tunnel_fast_retransmit_min_age_ratio',
        'tunnel_fast_retransmit_max_per_seq',
        'stats_enabled',
        'tunnel_window_growth_enabled',
        'tunnel_window_growth_mode',
        'tunnel_window_growth_step',
        'tunnel_window_growth_interval',
        'tunnel_bg_stop_timeout',
        'tunnel_bob_poll_interval',
        'tunnel_bob_poll_interval_bg',
        'tunnel_bob_retransmit_min_interval',
        'tunnel_bob_retransmit_max_interval',
        'tunnel_bob_retransmit_poll_factor',
        'tunnel_bob_poll_ewma_alpha',
        'tunnel_tick_sleep',
        'tunnel_send_rate',
        'tunnel_send_burst',
        'tunnel_adaptive_pacing_enabled',
        'tunnel_pace_target_inflight_ratio',
        'tunnel_pace_min_inflight',
        'tunnel_pace_max_inflight',
        'tunnel_pace_feedback_gain',
        'tunnel_pace_ack_ewma_alpha',
        'tunnel_pace_rtt_floor_ms',
        'tunnel_pace_ack_idle_reset_sec',
        'tunnel_pacer_summary_interval',
        'tunnel_poll_pacing_enabled',
        'tunnel_poll_min_interval',
        'tunnel_poll_max_interval',
        'tunnel_poll_rtt_ratio',
        'tunnel_connect_poll_interval',
        'non_blocking_poll_timeout',
        'channel_max_send_buf',
        'channel_max_recv_buf',
        'channel_open_timeout',
        'channel_write_backoff_initial',
        'channel_write_backoff_max',
        'channel_control_read_chunk',
        'channel_id_reuse_cooldown',
        'file_transfer_max_size',
        'file_transfer_chunk_size',
        'file_transfer_hash_timeout',
        'file_transfer_root',
        'file_transfer_timeout',
        'file_transfer_max_active',
        'nc_linux_bind_timeout',
        'nc_linux_connect_timeout',
        'nc_linux_buffer_size',
        'nc_linux_poll_timeout',
        'module_shutdown_timeout',
        'db_log_path',
        'db_log_flush',
        'db_log_queue',
        'log_profile',
        'log_component_transport_dns',
        'log_component_transport_icmp',
        'log_component_transport_tls',
        'log_component_tunnel',
        'log_component_channel',
        'log_component_protocol',
        'log_event_whitelist',
        'log_event_blacklist',
        'log_component_module_relay',
        'log_component_module_file_transfer',
        'log_component_module_nc_linux',
        'relay_listen_host',
        'relay_listen_port',
        'relay_listen_backlog',
        'relay_accept_timeout',
        'relay_channel_open_timeout',
        'relay_connect_timeout',
        'relay_target_connect_timeout',
        'relay_socket_timeout',
        'relay_channel_timeout',
        'relay_write_timeout',
        'relay_buffer_size',
        'relay_pump_poll_timeout',
        'relay_pump_backoff_max',
        'relay_thread_join_timeout',
        'protocol_max_packet_mtu',
        'protocol_initial_packet_mtu',
        'protocol_initial_rto_ms',
        'protocol_min_rto_ms',
        'protocol_max_rto_ms',
    )

    def __init__(self, **kwargs):
        for name in self._FIELDS:
            setattr(self, name, getattr(self.__class__, name))
        for key, value in kwargs.items():
            if key not in self._FIELDS:
                raise TypeError('Unknown Config field: %s' % key)
            setattr(self, key, value)
        self.__post_init__()

    def __post_init__(self):
        """Validate configuration values."""
        self.validate()

    def validate(self):
        """Validate configuration values."""
        # DNS validation
        if self.dns_pending_timeout < 1.0:
            raise ValueError("dns_pending_timeout must be >= 1.0")
        if self.dns_query_type != "A":
            raise ValueError("dns_query_type must be 'A'")
        if self.dns_response_type not in ("CNAME",):
            raise ValueError("dns_response_type must be 'CNAME'")
        if self.dns_edns_size > DNS_EDNS_MAX_SIZE:
            raise ValueError("dns_edns_size must be <= %d" % DNS_EDNS_MAX_SIZE)
        if self.dns_recv_bufsize_min < DNS_STANDARD_SIZE:
            raise ValueError("dns_recv_bufsize_min must be >= %d" % DNS_STANDARD_SIZE)
        if self.dns_label_max_len < 4 or self.dns_label_max_len > 63:
            raise ValueError("dns_label_max_len must be 4-63")
        if not self.dns_cname_label or '.' in self.dns_cname_label:
            raise ValueError("dns_cname_label must be a single label")
        if len(self.dns_cname_label) > 63:
            raise ValueError("dns_cname_label must be <= 63 characters")
        if self._is_base32_label(self.dns_cname_label):
            raise ValueError("dns_cname_label must include non-base32 characters")

        # ICMP validation
        if self.icmp_packet_mtu <= 0:
            raise ValueError("icmp_packet_mtu must be > 0")
        if self.icmp_pending_timeout <= 0:
            raise ValueError("icmp_pending_timeout must be > 0")

        # UDP ephemeral validation
        if self.udp_ephemeral_packet_mtu <= 0:
            raise ValueError("udp_ephemeral_packet_mtu must be > 0")
        if self.udp_ephemeral_pending_timeout <= 0:
            raise ValueError("udp_ephemeral_pending_timeout must be > 0")
        if self.udp_ephemeral_source_port_reuse_seconds < 0:
            raise ValueError("udp_ephemeral_source_port_reuse_seconds must be >= 0")

        # Crypto validation
        if self.crypto_mode not in ("none", "xor", "rc4", "sha256"):
            raise ValueError("crypto_mode must be 'none', 'xor', 'rc4', or 'sha256'")
        if self.crypto_mode != "none" and not self.crypto_psk:
            raise ValueError("crypto_psk required for %s mode" % self.crypto_mode)

        # Tunnel validation
        if self.max_in_flight < 1 or self.max_in_flight > 256:
            raise ValueError("max_in_flight must be 1-256")
        if self.tunnel_keepalive_interval <= 0:
            raise ValueError("tunnel_keepalive_interval must be > 0")
        if self.tunnel_pong_grace_polls < 0:
            raise ValueError("tunnel_pong_grace_polls must be >= 0")
        if self.tunnel_idle_timeout <= 0:
            raise ValueError("tunnel_idle_timeout must be > 0")
        if self.tunnel_no_response_timeout <= 0:
            raise ValueError("tunnel_no_response_timeout must be > 0")
        if self.tunnel_initial_window < 1 or self.tunnel_initial_window > 256:
            raise ValueError("tunnel_initial_window must be 1-256")
        if self.tunnel_retransmit_cap < 1:
            raise ValueError("tunnel_retransmit_cap must be >= 1")
        if self.tunnel_fast_retransmit_min_age_ratio <= 0:
            raise ValueError("tunnel_fast_retransmit_min_age_ratio must be > 0")
        if self.tunnel_fast_retransmit_max_per_seq < 1:
            raise ValueError("tunnel_fast_retransmit_max_per_seq must be >= 1")
        if self.tunnel_window_growth_mode not in ("linear", "doubling"):
            raise ValueError("tunnel_window_growth_mode must be 'linear' or 'doubling'")
        if self.tunnel_window_growth_step < 1:
            raise ValueError("tunnel_window_growth_step must be >= 1")
        if self.tunnel_window_growth_interval <= 0:
            raise ValueError("tunnel_window_growth_interval must be > 0")
        if self.tunnel_bg_stop_timeout <= 0:
            raise ValueError("tunnel_bg_stop_timeout must be > 0")
        if self.tunnel_bob_poll_interval <= 0:
            raise ValueError("tunnel_bob_poll_interval must be > 0")
        if self.tunnel_bob_poll_interval_bg <= 0:
            raise ValueError("tunnel_bob_poll_interval_bg must be > 0")
        if self.tunnel_tick_sleep < 0:
            raise ValueError("tunnel_tick_sleep must be >= 0")
        if self.tunnel_send_rate < 0:
            raise ValueError("tunnel_send_rate must be >= 0")
        if (self.tunnel_send_burst is not None and
                self.tunnel_send_burst <= 0):
            raise ValueError("tunnel_send_burst must be > 0 or None")
        if self.tunnel_pace_target_inflight_ratio <= 0:
            raise ValueError("tunnel_pace_target_inflight_ratio must be > 0")
        if self.tunnel_pace_min_inflight < 1 or self.tunnel_pace_min_inflight > 256:
            raise ValueError("tunnel_pace_min_inflight must be 1-256")
        if (self.tunnel_pace_max_inflight is not None and
                (self.tunnel_pace_max_inflight < 1 or
                 self.tunnel_pace_max_inflight > 256)):
            raise ValueError("tunnel_pace_max_inflight must be 1-256 or None")
        if (self.tunnel_pace_max_inflight is not None and
                self.tunnel_pace_max_inflight < self.tunnel_pace_min_inflight):
            raise ValueError("tunnel_pace_max_inflight must be >= tunnel_pace_min_inflight")
        if self.tunnel_pace_feedback_gain <= 0:
            raise ValueError("tunnel_pace_feedback_gain must be > 0")
        if (self.tunnel_pace_ack_ewma_alpha <= 0 or
                self.tunnel_pace_ack_ewma_alpha > 1):
            raise ValueError("tunnel_pace_ack_ewma_alpha must be > 0 and <= 1")
        if self.tunnel_pace_rtt_floor_ms <= 0:
            raise ValueError("tunnel_pace_rtt_floor_ms must be > 0")
        if self.tunnel_pace_ack_idle_reset_sec <= 0:
            raise ValueError("tunnel_pace_ack_idle_reset_sec must be > 0")
        if self.tunnel_pacer_summary_interval < 0:
            raise ValueError("tunnel_pacer_summary_interval must be >= 0")
        if self.tunnel_poll_min_interval <= 0:
            raise ValueError("tunnel_poll_min_interval must be > 0")
        if self.tunnel_poll_max_interval <= 0:
            raise ValueError("tunnel_poll_max_interval must be > 0")
        if self.tunnel_poll_min_interval > self.tunnel_poll_max_interval:
            raise ValueError(
                "tunnel_poll_min_interval must be <= tunnel_poll_max_interval"
            )
        if self.tunnel_poll_rtt_ratio <= 0:
            raise ValueError("tunnel_poll_rtt_ratio must be > 0")
        if self.tunnel_connect_poll_interval <= 0:
            raise ValueError("tunnel_connect_poll_interval must be > 0")

        payload_bytes = self.protocol_max_packet_mtu - PACKET_HEADER_SIZE
        if payload_bytes < 1:
            raise ValueError(
                "protocol_max_packet_mtu must be > %d" % PACKET_HEADER_SIZE
            )
        worst_case_buf = payload_bytes * self.max_in_flight * 4
        if worst_case_buf < 1024:
            worst_case_buf = 1024
        if self.channel_max_send_buf < worst_case_buf:
            self.channel_max_send_buf = worst_case_buf
        if self.channel_max_recv_buf < worst_case_buf:
            self.channel_max_recv_buf = worst_case_buf

        # Channel validation
        if self.channel_max_send_buf < 1024:
            raise ValueError("channel_max_send_buf must be >= 1024")
        if self.channel_max_recv_buf < 1024:
            raise ValueError("channel_max_recv_buf must be >= 1024")
        if self.channel_write_backoff_initial <= 0:
            raise ValueError("channel_write_backoff_initial must be > 0")
        if self.channel_write_backoff_max < self.channel_write_backoff_initial:
            raise ValueError("channel_write_backoff_max must be >= channel_write_backoff_initial")
        if self.channel_control_read_chunk < 1:
            raise ValueError("channel_control_read_chunk must be >= 1")
        if self.channel_id_reuse_cooldown < 0:
            raise ValueError("channel_id_reuse_cooldown must be >= 0")

        # File transfer validation
        if self.file_transfer_chunk_size < 1:
            raise ValueError("file_transfer_chunk_size must be >= 1")
        if self.file_transfer_max_active < 1:
            raise ValueError("file_transfer_max_active must be >= 1")

        # NC Linux validation
        if self.nc_linux_bind_timeout <= 0:
            raise ValueError("nc_linux_bind_timeout must be > 0")
        if self.nc_linux_connect_timeout <= 0:
            raise ValueError("nc_linux_connect_timeout must be > 0")
        if self.nc_linux_buffer_size < 1:
            raise ValueError("nc_linux_buffer_size must be >= 1")
        if self.nc_linux_poll_timeout <= 0:
            raise ValueError("nc_linux_poll_timeout must be > 0")

        # Module validation
        if self.module_shutdown_timeout <= 0:
            raise ValueError("module_shutdown_timeout must be > 0")

        # SOCKS validation
        if self.relay_listen_port < 1 or self.relay_listen_port > 65535:
            raise ValueError("relay_listen_port must be 1-65535")
        if self.relay_listen_backlog < 1:
            raise ValueError("relay_listen_backlog must be >= 1")
        if self.relay_accept_timeout <= 0:
            raise ValueError("relay_accept_timeout must be > 0")
        if self.relay_channel_open_timeout <= 0:
            raise ValueError("relay_channel_open_timeout must be > 0")
        if self.relay_connect_timeout <= 0:
            raise ValueError("relay_connect_timeout must be > 0")
        if self.relay_target_connect_timeout <= 0:
            raise ValueError("relay_target_connect_timeout must be > 0")
        if self.relay_socket_timeout <= 0:
            raise ValueError("relay_socket_timeout must be > 0")
        if self.relay_channel_timeout <= 0:
            raise ValueError("relay_channel_timeout must be > 0")
        if (self.relay_write_timeout is not None and
                self.relay_write_timeout <= 0):
            raise ValueError("relay_write_timeout must be > 0 or None")
        if self.relay_buffer_size < 1:
            raise ValueError("relay_buffer_size must be >= 1")
        if self.relay_pump_poll_timeout <= 0:
            raise ValueError("relay_pump_poll_timeout must be > 0")
        if self.relay_pump_backoff_max <= 0:
            raise ValueError("relay_pump_backoff_max must be > 0")
        if self.relay_thread_join_timeout <= 0:
            raise ValueError("relay_thread_join_timeout must be > 0")

        # Protocol validation
        if self.protocol_min_rto_ms >= self.protocol_max_rto_ms:
            raise ValueError("protocol_min_rto_ms must be < protocol_max_rto_ms")

    @staticmethod
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


def make_cipher(config):
    """
    Create a cipher instance from config.

    Args:
        config: Configuration object

    Returns:
        Cipher instance (Plain, XOR, RC4, or SHA256)
    """
    from .crypto import CIPHER_MODES
    cipher_cls = CIPHER_MODES[config.crypto_mode]
    if config.crypto_mode == "none":
        return cipher_cls()
    return cipher_cls(config.crypto_psk)
