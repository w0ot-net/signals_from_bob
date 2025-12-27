# -*- coding: ascii -*-
"""
Centralized configuration for Signals from Bob.

All configurable values in one place with sensible defaults.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

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
    # DNS resolver address for Alice (e.g., '1.1.1.1:53'), None = system default
    dns_resolver: Optional[str] = None
    # Listen address for Bob's DNS server
    dns_listen_addr: str = "0.0.0.0:53"
    # EDNS0 buffer size (512 standard, 4096 for larger payloads)
    dns_edns_size: int = 512
    # Maximum concurrent DNS queries in flight
    dns_max_pending: int = 16
    # Timeout before considering a DNS query stale (seconds)
    dns_pending_timeout: float = 10.0
    # Query/response type: 'TXT' or 'NULL'
    dns_record_type: str = "TXT"

    # --- Crypto ---
    # Encryption mode: 'none', 'xor', 'rc4'
    crypto_mode: str = "none"
    # Pre-shared key for xor/rc4 (bytes or string)
    crypto_psk: Optional[bytes] = None

    # --- Tunnel ---
    # Alice: seconds between keepalive packets
    tunnel_keepalive_interval: float = 1.0
    # Bob: seconds of inactivity before considering connection dead
    tunnel_idle_timeout: float = 60.0
    # Maximum unacknowledged packets in flight (max 64, SACK bitmap limit)
    tunnel_max_in_flight: int = 16
    # Handshake/connection timeout (seconds)
    tunnel_connect_timeout: float = 10.0
    # Alice: packets sent without response before giving up
    tunnel_timeout_packets: int = 30
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

    # --- Channel ---
    # Maximum bytes to buffer for sending per channel
    channel_max_send_buf: int = 65536
    # Timeout waiting for channel to open (seconds)
    channel_open_timeout: float = 5.0

    # --- File Transfer ---
    # Maximum file size to transfer (bytes), None = unlimited
    file_transfer_max_size: Optional[int] = None
    # Chunk size for file I/O (bytes)
    file_transfer_chunk_size: int = 8192
    # Timeout waiting for hash verification (seconds)
    file_transfer_hash_timeout: float = 10.0

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
        if self.dns_record_type not in ("TXT", "NULL"):
            raise ValueError("dns_record_type must be 'TXT' or 'NULL'")
        if self.dns_edns_size < 512:
            raise ValueError("dns_edns_size must be >= 512")

        # Crypto validation
        if self.crypto_mode not in ("none", "xor", "rc4"):
            raise ValueError("crypto_mode must be 'none', 'xor', or 'rc4'")
        if self.crypto_mode != "none" and not self.crypto_psk:
            raise ValueError(f"crypto_psk required for {self.crypto_mode} mode")

        # Tunnel validation
        if self.tunnel_max_in_flight < 1 or self.tunnel_max_in_flight > 64:
            raise ValueError("tunnel_max_in_flight must be 1-64")
        if self.tunnel_keepalive_interval <= 0:
            raise ValueError("tunnel_keepalive_interval must be > 0")
        if self.tunnel_idle_timeout <= 0:
            raise ValueError("tunnel_idle_timeout must be > 0")
        if self.tunnel_window_growth_mode not in ("linear", "doubling"):
            raise ValueError("tunnel_window_growth_mode must be 'linear' or 'doubling'")
        if self.tunnel_window_growth_step < 1:
            raise ValueError("tunnel_window_growth_step must be >= 1")
        if self.tunnel_window_growth_interval <= 0:
            raise ValueError("tunnel_window_growth_interval must be > 0")

        # Channel validation
        if self.channel_max_send_buf < 1024:
            raise ValueError("channel_max_send_buf must be >= 1024")

        # File transfer validation
        if self.file_transfer_chunk_size < 1:
            raise ValueError("file_transfer_chunk_size must be >= 1")

        # Protocol validation
        if self.protocol_min_rto_ms >= self.protocol_max_rto_ms:
            raise ValueError("protocol_min_rto_ms must be < protocol_max_rto_ms")


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
