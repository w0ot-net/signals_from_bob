# -*- coding: ascii -*-
"""
Centralized configuration for Signals from Bob.

All configurable values in one place with sensible defaults.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

DNS_STANDARD_SIZE = 512

if TYPE_CHECKING:
    from .crypto import Plain, RC4, XOR


@dataclass
class Config:
    """
    Configuration for SFB tunnels.

    Use the same Config instance for both Alice and Bob - each side
    uses the fields relevant to its role.
    """

    # --- DNS Transport ---
    # Base domain for DNS tunneling (required for DNS transport)
    dns_base_domain: str = ""
    # Default transport for CLI
    transport_default: str = "dns"
    # Active transport name (None = transport_default)
    transport: Optional[str] = None
    # DNS resolver address for Alice (e.g., '1.1.1.1:53'), None = system default
    dns_resolver: Optional[str] = None
    # Listen address for Bob's DNS server
    dns_listen_addr: str = "0.0.0.0:53"
    # EDNS0 buffer size (DNS_STANDARD_SIZE standard, 4096 for larger payloads)
    dns_edns_size: int = DNS_STANDARD_SIZE
    # Minimum UDP recv buffer size for DNS responses/queries
    dns_recv_bufsize_min: int = 4096
    # Maximum concurrent DNS queries in flight
    dns_max_pending: int = 32
    # Timeout before considering a DNS query stale (seconds)
    dns_pending_timeout: float = 5.0
    # Query type for DNS tunneling (currently fixed to 'A')
    dns_query_type: str = "A"
    # Response type for DNS tunneling
    dns_response_type: str = "CNAME"
    # Max label length for tunnel subdomains (1-63, default 50)
    dns_label_max_len: int = 50
    # CNAME label appended before base domain (short suffix)
    dns_cname_label: str = "0"
    # IPv4 address returned for CNAME follow-up A queries
    dns_cname_a_addr: str = "0.0.0.0"

    # --- ICMP Transport ---
    # Target host/IP for Alice
    icmp_target: Optional[str] = None
    # Max SFB packet size to send/receive in ICMP payload
    icmp_payload_mtu: int = 1200
    # Maximum concurrent ICMP requests in flight
    icmp_max_pending: int = 64
    # Timeout before considering an ICMP request stale (seconds)
    icmp_pending_timeout: float = 1.0

    # --- Crypto ---
    # Encryption mode: 'none', 'xor', 'rc4'
    crypto_mode: str = "none"
    # Pre-shared key for xor/rc4 (bytes or string)
    crypto_psk: Optional[bytes] = None

    # --- Tunnel ---
    # Alice: seconds between keepalive packets
    tunnel_keepalive_interval: float = 1.0
    # Alice: immediate poll attempts after pong-only responses
    tunnel_pong_grace_polls: int = 5
    # Bob: seconds of inactivity before considering connection dead
    tunnel_idle_timeout: float = 60.0
    # Initial window size before negotiation (packets)
    tunnel_initial_window: int = 1
    # Maximum unacknowledged packets in flight (max 64, SACK bitmap limit)
    tunnel_max_in_flight: int = 64
    # Handshake/connection timeout (seconds)
    tunnel_connect_timeout: float = 10.0
    # Alice: packets sent without response before giving up
    tunnel_timeout_packets: int = 100
    # Enable reliability stats tracking
    tunnel_stats_enabled: bool = False
    # Enable dynamic window growth on Alice
    tunnel_window_growth_enabled: bool = True
    # Window growth mode: 'linear' or 'doubling'
    tunnel_window_growth_mode: str = "linear"
    # Window growth step (linear mode)
    tunnel_window_growth_step: int = 1
    # Minimum seconds between window growth requests
    tunnel_window_growth_interval: float = 2.0
    # Background loop stop timeout (seconds)
    tunnel_bg_stop_timeout: float = 2.0
    # Bob: poll timeout for serve_forever (seconds)
    tunnel_bob_poll_interval: float = 1.0
    # Bob: poll timeout for background loop (seconds)
    tunnel_bob_poll_interval_bg: float = 0.1
    # Bob: coalesce small responses to fill MTU (seconds, 0 disables)
    tunnel_bob_coalesce_delay: float = 0.01
    # Bob: minimum queued data bytes to target before responding (0 disables)
    tunnel_bob_coalesce_min_bytes: int = 128
    # Bob: min seconds between retransmits of the oldest unacked packet
    tunnel_bob_retransmit_min_interval: float = 0.05
    # Bob: max seconds between retransmits of the oldest unacked packet
    tunnel_bob_retransmit_max_interval: float = 1.0
    # Bob: multiplier for poll EWMA to derive retransmit cooldown
    tunnel_bob_retransmit_poll_factor: float = 4.0
    # Bob: EWMA alpha for poll interval smoothing (0-1)
    tunnel_bob_poll_ewma_alpha: float = 0.2
    # Alice: sleep between ticks when running (seconds)
    tunnel_tick_sleep: float = 0.001
    # Alice: max send rate (packets per second, 0 = unlimited)
    tunnel_send_rate: float = 0.0
    # Alice: burst capacity for send rate (packets, None=rate)
    tunnel_send_burst: Optional[float] = None
    # Alice: adaptive pacing enabled
    tunnel_adaptive_pacing_enabled: bool = True
    # Alice: adaptive pacing target inflight ratio
    tunnel_pace_target_inflight_ratio: float = 1.0
    # Alice: adaptive pacing minimum inflight target
    tunnel_pace_min_inflight: int = 1
    # Alice: adaptive pacing maximum inflight target (None = cap)
    tunnel_pace_max_inflight: Optional[int] = None
    # Alice: adaptive pacing fast-start after real data
    tunnel_pace_fast_start: bool = True
    # Alice: adaptive pacing RTT floor in milliseconds
    tunnel_pace_rtt_floor_ms: float = 5.0
    # Alice: adaptive pacing time-based spacing gate
    tunnel_pace_time_based: bool = False
    # Bob: poll interval while waiting for connection (seconds)
    tunnel_connect_poll_interval: float = 0.1
    # Small timeout for "non-blocking" polls to prevent busy loops (seconds)
    non_blocking_poll_timeout: float = 0.0001

    # --- Channel ---
    # Maximum bytes to buffer for sending per channel
    channel_max_send_buf: int = 32768
    # Timeout waiting for channel to open (seconds)
    channel_open_timeout: float = 5.0
    # Write backoff initial delay (seconds)
    channel_write_backoff_initial: float = 0.01
    # Write backoff maximum delay (seconds)
    channel_write_backoff_max: float = 1.0
    # Control channel read chunk size (bytes)
    channel_control_read_chunk: int = 4096

    # --- File Transfer ---
    # Maximum file size to transfer (bytes), None = unlimited
    file_transfer_max_size: Optional[int] = None
    # Chunk size for file I/O (bytes)
    file_transfer_chunk_size: int = 8192
    # Timeout waiting for hash verification (seconds)
    file_transfer_hash_timeout: float = 10.0
    # File transfer root for Bob
    file_transfer_root: str = "."
    # File transfer command timeout (seconds), None = no timeout
    file_transfer_timeout: Optional[float] = None

    # --- Modules ---
    # Module shutdown join timeout (seconds)
    module_shutdown_timeout: float = 5.0

    # --- Logging ---
    # SQLite log path (None = disabled)
    db_log_path: Optional[str] = None
    # SQLite log flush interval (seconds)
    db_log_flush: float = 2.0
    # SQLite log queue max size (0 = unbounded)
    db_log_queue: int = 0
    # Default logging profile name (None = no profile)
    log_profile: Optional[str] = 'dns_troubleshoot'
    # Enable DNS transport logging (stdout + SQLite)
    log_component_transport_dns: bool = False
    # Enable ICMP transport logging (stdout + SQLite)
    log_component_transport_icmp: bool = False
    # Enable tunnel logging (stdout + SQLite)
    log_component_tunnel: bool = True
    # Enable channel logging (stdout + SQLite)
    log_component_channel: bool = False
    # Enable protocol logging (stdout + SQLite)
    log_component_protocol: bool = False
    # Structured event whitelist (empty = allow all events)
    log_event_whitelist: tuple = ()
    # Structured event blacklist (empty = deny none)
    # Default blacklist reduces high-volume debug events.
    log_event_blacklist: tuple = (
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
        'channel.drain',
        'channel.pack',
        'channel.send_buf_*',
        'channel.write_wait',
        'dns.send',
        'dns.recv',
        'icmp.send',
        'icmp.recv',
    )
    # Enable SOCKS module logging (stdout + SQLite)
    log_component_module_socks: bool = True
    # Enable file transfer module logging (stdout + SQLite)
    log_component_module_file_transfer: bool = True

    # --- SOCKS ---
    # SOCKS server listen host
    socks_listen_host: str = "0.0.0.0"
    # SOCKS server listen port
    socks_listen_port: int = 1080
    # SOCKS server listen backlog
    socks_listen_backlog: int = 5
    # SOCKS accept loop timeout (seconds)
    socks_accept_timeout: float = 0.5
    # Channel open timeout for SOCKS (seconds)
    socks_channel_open_timeout: float = 10.0
    # SOCKS connect request timeout (seconds)
    socks_connect_timeout: float = 30.0
    # Target connect timeout for SOCKS relay (seconds)
    socks_connect_target_timeout: float = 30.0
    # SOCKS relay socket timeout (seconds)
    socks_relay_socket_timeout: float = 5.0
    # SOCKS relay channel read timeout (seconds)
    socks_relay_channel_timeout: float = 0.5
    # SOCKS relay channel write timeout (seconds)
    socks_relay_write_timeout: Optional[float] = None
    # SOCKS relay buffer size (bytes)
    socks_relay_buffer_size: int = 2048
    # Maximum backoff for SOCKS pump when channel buffer is full (seconds)
    socks_pump_backoff_max: float = 0.05
    # SOCKS thread join timeout (seconds)
    socks_thread_join_timeout: float = 2.0

    # --- Protocol (rarely need changing) ---
    # Maximum packet size (bytes)
    protocol_max_packet_size: int = 1450
    # Initial MTU before negotiation (bytes)
    protocol_initial_mtu: int = 100
    # Initial retransmission timeout (milliseconds)
    protocol_initial_rto_ms: int = 1000
    # Minimum RTO (milliseconds)
    protocol_min_rto_ms: int = 500
    # Maximum RTO (milliseconds)
    protocol_max_rto_ms: int = 10000

    def __post_init__(self):
        """Validate configuration values."""
        # DNS validation
        if self.dns_pending_timeout < 1.0:
            raise ValueError("dns_pending_timeout must be >= 1.0")
        if self.dns_query_type != "A":
            raise ValueError("dns_query_type must be 'A'")
        if self.dns_response_type not in ("CNAME",):
            raise ValueError("dns_response_type must be 'CNAME'")
        if self.dns_edns_size > DNS_STANDARD_SIZE:
            raise ValueError("dns_edns_size must be <= %d" % DNS_STANDARD_SIZE)
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
        if self.icmp_payload_mtu <= 0:
            raise ValueError("icmp_payload_mtu must be > 0")
        if self.icmp_max_pending < 1:
            raise ValueError("icmp_max_pending must be >= 1")
        if self.icmp_pending_timeout <= 0:
            raise ValueError("icmp_pending_timeout must be > 0")

        # Crypto validation
        if self.crypto_mode not in ("none", "xor", "rc4"):
            raise ValueError("crypto_mode must be 'none', 'xor', or 'rc4'")
        if self.crypto_mode != "none" and not self.crypto_psk:
            raise ValueError("crypto_psk required for %s mode" % self.crypto_mode)

        # Transport-aware defaults
        effective_transport = self.transport or self.transport_default
        if effective_transport:
            try:
                transport_name = effective_transport.lower()
            except AttributeError:
                transport_name = effective_transport
            if transport_name != "dns":
                self.tunnel_max_in_flight = 64

        # Tunnel validation
        if self.tunnel_max_in_flight < 1 or self.tunnel_max_in_flight > 64:
            raise ValueError("tunnel_max_in_flight must be 1-64")
        if self.tunnel_keepalive_interval <= 0:
            raise ValueError("tunnel_keepalive_interval must be > 0")
        if self.tunnel_pong_grace_polls < 0:
            raise ValueError("tunnel_pong_grace_polls must be >= 0")
        if self.tunnel_idle_timeout <= 0:
            raise ValueError("tunnel_idle_timeout must be > 0")
        if self.tunnel_initial_window < 1 or self.tunnel_initial_window > 64:
            raise ValueError("tunnel_initial_window must be 1-64")
        if self.tunnel_window_growth_mode not in ("linear", "doubling"):
            raise ValueError("tunnel_window_growth_mode must be 'linear' or 'doubling'")
        if self.tunnel_window_growth_step < 1:
            raise ValueError("tunnel_window_growth_step must be >= 1")
        if self.tunnel_window_growth_interval <= 0:
            raise ValueError("tunnel_window_growth_interval must be > 0")
        if self.tunnel_bob_coalesce_delay < 0:
            raise ValueError("tunnel_bob_coalesce_delay must be >= 0")
        if self.tunnel_bob_coalesce_min_bytes < 0:
            raise ValueError("tunnel_bob_coalesce_min_bytes must be >= 0")
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
        if self.tunnel_pace_min_inflight < 1 or self.tunnel_pace_min_inflight > 64:
            raise ValueError("tunnel_pace_min_inflight must be 1-64")
        if (self.tunnel_pace_max_inflight is not None and
                (self.tunnel_pace_max_inflight < 1 or
                 self.tunnel_pace_max_inflight > 64)):
            raise ValueError("tunnel_pace_max_inflight must be 1-64 or None")
        if (self.tunnel_pace_max_inflight is not None and
                self.tunnel_pace_max_inflight < self.tunnel_pace_min_inflight):
            raise ValueError("tunnel_pace_max_inflight must be >= tunnel_pace_min_inflight")
        if self.tunnel_pace_rtt_floor_ms <= 0:
            raise ValueError("tunnel_pace_rtt_floor_ms must be > 0")
        if self.tunnel_connect_poll_interval <= 0:
            raise ValueError("tunnel_connect_poll_interval must be > 0")

        # Channel validation
        if self.channel_max_send_buf < 1024:
            raise ValueError("channel_max_send_buf must be >= 1024")
        if self.channel_write_backoff_initial <= 0:
            raise ValueError("channel_write_backoff_initial must be > 0")
        if self.channel_write_backoff_max < self.channel_write_backoff_initial:
            raise ValueError("channel_write_backoff_max must be >= channel_write_backoff_initial")
        if self.channel_control_read_chunk < 1:
            raise ValueError("channel_control_read_chunk must be >= 1")

        # File transfer validation
        if self.file_transfer_chunk_size < 1:
            raise ValueError("file_transfer_chunk_size must be >= 1")

        # Module validation
        if self.module_shutdown_timeout <= 0:
            raise ValueError("module_shutdown_timeout must be > 0")

        # SOCKS validation
        if self.socks_listen_port < 1 or self.socks_listen_port > 65535:
            raise ValueError("socks_listen_port must be 1-65535")
        if self.socks_listen_backlog < 1:
            raise ValueError("socks_listen_backlog must be >= 1")
        if self.socks_accept_timeout <= 0:
            raise ValueError("socks_accept_timeout must be > 0")
        if self.socks_channel_open_timeout <= 0:
            raise ValueError("socks_channel_open_timeout must be > 0")
        if self.socks_connect_timeout <= 0:
            raise ValueError("socks_connect_timeout must be > 0")
        if self.socks_connect_target_timeout <= 0:
            raise ValueError("socks_connect_target_timeout must be > 0")
        if self.socks_relay_socket_timeout <= 0:
            raise ValueError("socks_relay_socket_timeout must be > 0")
        if self.socks_relay_channel_timeout <= 0:
            raise ValueError("socks_relay_channel_timeout must be > 0")
        if (self.socks_relay_write_timeout is not None and
                self.socks_relay_write_timeout <= 0):
            raise ValueError("socks_relay_write_timeout must be > 0 or None")
        if self.socks_relay_buffer_size < 1:
            raise ValueError("socks_relay_buffer_size must be >= 1")
        if self.socks_pump_backoff_max <= 0:
            raise ValueError("socks_pump_backoff_max must be > 0")
        if self.socks_thread_join_timeout <= 0:
            raise ValueError("socks_thread_join_timeout must be > 0")

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

def make_cipher(config: Config):
    """
    Create a cipher instance from config.

    Args:
        config: Configuration object

    Returns:
        Cipher instance (Plain, XOR, or RC4)
    """
    from .crypto import CIPHER_MODES
    cipher_cls = CIPHER_MODES[config.crypto_mode]
    if config.crypto_mode == "none":
        return cipher_cls()
    return cipher_cls(config.crypto_psk)
