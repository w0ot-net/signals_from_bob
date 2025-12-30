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
    dns_pending_timeout: float = 10.0
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
    # Maximum DNS queries per second (0 = unlimited)
    dns_queries_per_second: float = 600.0

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
    # Alice: sleep between ticks when running (seconds)
    tunnel_tick_sleep: float = 0.001
    # Bob: poll interval while waiting for connection (seconds)
    tunnel_connect_poll_interval: float = 0.1

    # --- Channel ---
    # Maximum bytes to buffer for sending per channel
    channel_max_send_buf: int = 65536
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
    db_log_flush: float = 0.5
    # SQLite log queue max size (0 = unbounded)
    db_log_queue: int = 0

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
    socks_relay_socket_timeout: float = 0.5
    # SOCKS relay channel read timeout (seconds)
    socks_relay_channel_timeout: float = 0.5
    # SOCKS relay channel write timeout (seconds)
    socks_relay_write_timeout: Optional[float] = None
    # SOCKS relay buffer size (bytes)
    socks_relay_buffer_size: int = 8192
    # SOCKS relay sleep when channel buffer is full (seconds)
    socks_relay_full_sleep: float = 0.005
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
        if self.dns_queries_per_second < 0:
            raise ValueError("dns_queries_per_second must be >= 0")
        if self.dns_label_max_len < 4 or self.dns_label_max_len > 63:
            raise ValueError("dns_label_max_len must be 4-63")
        if not self.dns_cname_label or '.' in self.dns_cname_label:
            raise ValueError("dns_cname_label must be a single label")
        if len(self.dns_cname_label) > 63:
            raise ValueError("dns_cname_label must be <= 63 characters")
        if self._is_base32_label(self.dns_cname_label):
            raise ValueError("dns_cname_label must include non-base32 characters")

        # Crypto validation
        if self.crypto_mode not in ("none", "xor", "rc4"):
            raise ValueError("crypto_mode must be 'none', 'xor', or 'rc4'")
        if self.crypto_mode != "none" and not self.crypto_psk:
            raise ValueError("crypto_psk required for %s mode" % self.crypto_mode)

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
        if self.tunnel_bg_stop_timeout <= 0:
            raise ValueError("tunnel_bg_stop_timeout must be > 0")
        if self.tunnel_bob_poll_interval <= 0:
            raise ValueError("tunnel_bob_poll_interval must be > 0")
        if self.tunnel_bob_poll_interval_bg <= 0:
            raise ValueError("tunnel_bob_poll_interval_bg must be > 0")
        if self.tunnel_tick_sleep < 0:
            raise ValueError("tunnel_tick_sleep must be >= 0")
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
        if self.socks_relay_full_sleep < 0:
            raise ValueError("socks_relay_full_sleep must be >= 0")
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
