# -*- coding: ascii -*-
"""
Reliability stats tracking (optional).
"""

from __future__ import absolute_import


class ReliabilityStats(object):
    """Counters for reliability-layer events."""

    __slots__ = (
        'sent_packets',
        'retransmit_packets',
        'retransmit_skipped_rate_limit',
        'retransmit_skipped_transport',
        'acked_packets',
        'acked_cumulative_packets',
        'acked_sack_packets',
        'acked_first_tx_packets',
        'rtt_samples',
        'recv_duplicates',
        'recv_out_of_window',
        'recv_buffer_full',
        'recv_buffered',
        'recv_delivered',
    )

    def __init__(self):
        self.sent_packets = 0
        self.retransmit_packets = 0
        self.retransmit_skipped_rate_limit = 0
        self.retransmit_skipped_transport = 0
        self.acked_packets = 0
        self.acked_cumulative_packets = 0
        self.acked_sack_packets = 0
        self.acked_first_tx_packets = 0
        self.rtt_samples = 0
        self.recv_duplicates = 0
        self.recv_out_of_window = 0
        self.recv_buffer_full = 0
        self.recv_buffered = 0
        self.recv_delivered = 0

    def on_send(self):
        self.sent_packets += 1

    def on_retransmit(self):
        self.retransmit_packets += 1

    def on_retransmit_skip_rate_limit(self):
        self.retransmit_skipped_rate_limit += 1

    def on_retransmit_skip_transport(self):
        self.retransmit_skipped_transport += 1

    def on_ack(self, is_sack):
        self.acked_packets += 1
        if is_sack:
            self.acked_sack_packets += 1
        else:
            self.acked_cumulative_packets += 1

    def on_ack_first_tx(self):
        self.acked_first_tx_packets += 1

    def on_rtt_sample(self):
        self.rtt_samples += 1

    def on_recv_duplicate(self):
        self.recv_duplicates += 1

    def on_recv_out_of_window(self):
        self.recv_out_of_window += 1

    def on_recv_buffer_full(self):
        self.recv_buffer_full += 1

    def on_recv_buffered(self):
        self.recv_buffered += 1

    def on_recv_delivered(self, count):
        self.recv_delivered += count


class NoopReliabilityStats(object):
    """No-op stats collector for disabled tracking."""

    __slots__ = ()

    def on_send(self):
        pass

    def on_retransmit(self):
        pass

    def on_retransmit_skip_rate_limit(self):
        pass

    def on_retransmit_skip_transport(self):
        pass

    def on_ack(self, is_sack):
        pass

    def on_ack_first_tx(self):
        pass

    def on_rtt_sample(self):
        pass

    def on_recv_duplicate(self):
        pass

    def on_recv_out_of_window(self):
        pass

    def on_recv_buffer_full(self):
        pass

    def on_recv_buffered(self):
        pass

    def on_recv_delivered(self, count):
        pass
