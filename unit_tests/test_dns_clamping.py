# -*- coding: ascii -*-
"""Unit tests for DNS clamping logic."""

from __future__ import absolute_import

import unittest

from sfb.config import DNS_STANDARD_SIZE
from sfb.protocol import (
    PacketHeader,
    FLAG_ACK,
    FLAG_HAS_SEGMENTS,
    FLAG_KEEPALIVE,
    FLAG_POLL_HINT,
    FLAG_SYN,
)
from sfb.protocol.constants import MIN_PACKET_MTU
from sfb.transport.dns import codec
from sfb.transport.dns.dns_client import DnsClient
from sfb.transport.dns.dns_server import DnsServer
from sfb.transport.transport_base import TransportError


def _make_domain(total_len):
    labels = []
    remaining = total_len
    while remaining > 0:
        label_len = min(63, remaining)
        labels.append('a' * label_len)
        remaining -= label_len
        if remaining > 0:
            remaining -= 1
    return '.'.join(labels)


def _max_query_payload_for_min_response(base_domain, label_max_len, edns_size,
                                        cname_suffix, opt_record_len,
                                        send_packet_mtu):
    max_payload = None
    for payload_len in range(MIN_PACKET_MTU, send_packet_mtu + 1):
        try:
            qname_wire_len = codec.calc_qname_wire_len(
                payload_len,
                base_domain,
                label_max_len,
            )
        except ValueError:
            continue
        response_cap, _ = codec.calc_cname_response_payload_cap(
            qname_wire_len,
            edns_size,
            cname_suffix,
            label_max_len,
            opt_record_len,
        )
        if response_cap is None:
            response_cap = 0
        if response_cap >= MIN_PACKET_MTU:
            max_payload = payload_len
    return max_payload


def _encode_flags(flags):
    return PacketHeader(flags=flags).encode()


class DnsClampingTests(unittest.TestCase):
    def _make_client(self, base_domain, label_max_len=50, edns_size=512):
        client = DnsClient.__new__(DnsClient)
        client._base_domain = base_domain
        client._label_max_len = label_max_len
        client._edns_size = edns_size
        client._cname_suffix = '0.%s' % base_domain
        client._opt_record_len = 0
        client._min_query_packet_mtu = MIN_PACKET_MTU
        client._min_response_packet_mtu = MIN_PACKET_MTU
        client._send_packet_mtu = codec.calc_query_mtu(
            base_domain,
            label_max_len,
        )
        return client

    def _make_clamp_client(self):
        client = DnsClient.__new__(DnsClient)
        client._alice_has_data_pending = False
        client._bob_has_data_polls = 2
        client._bob_has_data_remaining = 0
        client._poll_hint_budget = 0
        client._retransmit_guard = False
        client._recv_window_sack = 0
        client._max_in_flight = 1
        return client

    def test_init_response_caps_clamps_query_mtu(self):
        base_domain = 'example.com'
        client = self._make_client(base_domain)
        original_mtu = client._send_packet_mtu
        expected_mtu = _max_query_payload_for_min_response(
            base_domain,
            client._label_max_len,
            client._edns_size,
            client._cname_suffix,
            client._opt_record_len,
            original_mtu,
        )
        self.assertIsNotNone(expected_mtu)
        self.assertLess(expected_mtu, original_mtu)

        client._init_response_caps()

        self.assertEqual(client._send_packet_mtu, expected_mtu)
        self.assertGreater(client._max_response_payload_cap, 0)
        self.assertGreater(client._max_response_packet_mtu, 0)
        self.assertIsNotNone(client._response_cap_lookup)
        self.assertEqual(
            len(client._response_cap_lookup),
            client._max_response_packet_mtu + 1,
        )

    def test_init_response_caps_raises_when_response_too_small(self):
        base_domain = _make_domain(90)
        client = self._make_client(base_domain)
        with self.assertRaises(TransportError):
            client._init_response_caps()

    def test_select_payload_cap_clamps_to_send_mtu(self):
        client = self._make_clamp_client()
        client._response_cap_lookup = [0, 20, 40, 100]
        client._max_response_payload_cap = 200
        client._send_packet_mtu = 50
        client._min_query_packet_mtu = 10
        client._poll_hint_budget = 1
        payload_cap = client._select_payload_cap()
        self.assertEqual(payload_cap, 50)

    def test_select_payload_cap_clamps_to_min_query_mtu(self):
        client = self._make_clamp_client()
        client._response_cap_lookup = [0, 1, 2, 3]
        client._max_response_payload_cap = 3
        client._send_packet_mtu = 50
        client._min_query_packet_mtu = 10
        client._poll_hint_budget = 1
        payload_cap = client._select_payload_cap()
        self.assertEqual(payload_cap, 10)

    def test_query_payload_for_target_caps_out_of_range(self):
        client = self._make_clamp_client()
        client._response_cap_lookup = [0, 7, 14]
        self.assertEqual(client._query_payload_for_target(99), 14)

    def test_query_payload_for_target_returns_none_on_empty_lookup(self):
        client = self._make_clamp_client()
        client._response_cap_lookup = []
        self.assertIsNone(client._query_payload_for_target(1))

    def test_select_payload_cap_returns_none_without_poll_hint(self):
        client = self._make_clamp_client()
        client._response_cap_lookup = [0, 5, 10]
        client._max_response_payload_cap = 10
        client._send_packet_mtu = 10
        client._min_query_packet_mtu = 4
        client._poll_hint_budget = 0
        self.assertIsNone(client._select_payload_cap())

    def test_select_payload_cap_returns_none_with_empty_lookup(self):
        client = self._make_clamp_client()
        client._response_cap_lookup = []
        client._max_response_payload_cap = 10
        client._send_packet_mtu = 10
        client._min_query_packet_mtu = 4
        client._poll_hint_budget = 1
        self.assertIsNone(client._select_payload_cap())

    def test_calc_cname_response_payload_cap_clamps_edns_size(self):
        base_domain = 'example.com'
        qname_wire_len = codec.calc_qname_wire_len(
            MIN_PACKET_MTU,
            base_domain,
            50,
        )
        payload_cap, max_packet_size = codec.calc_cname_response_payload_cap(
            qname_wire_len,
            200,
            '0.%s' % base_domain,
            50,
            0,
        )
        self.assertEqual(max_packet_size, DNS_STANDARD_SIZE)
        self.assertGreaterEqual(payload_cap, 0)

    def test_calc_cname_response_payload_cap_accounts_for_opt_record(self):
        base_domain = 'example.com'
        qname_wire_len = codec.calc_qname_wire_len(
            80,
            base_domain,
            50,
        )
        cname_suffix = '0.%s' % base_domain
        edns_size = 1232
        opt_record_len = len(codec.build_opt_record(edns_size))
        payload_no_opt, max_packet_no_opt = codec.calc_cname_response_payload_cap(
            qname_wire_len,
            edns_size,
            cname_suffix,
            50,
            0,
        )
        payload_with_opt, max_packet_with_opt = codec.calc_cname_response_payload_cap(
            qname_wire_len,
            edns_size,
            cname_suffix,
            50,
            opt_record_len,
        )
        self.assertEqual(max_packet_no_opt, edns_size)
        self.assertEqual(max_packet_with_opt, edns_size)
        self.assertLessEqual(payload_with_opt, payload_no_opt)

    def test_calc_cname_response_payload_cap_returns_none_for_missing_qname(self):
        payload_cap, max_packet_size = codec.calc_cname_response_payload_cap(
            None,
            512,
            '0.example.com',
            50,
            0,
        )
        self.assertIsNone(payload_cap)
        self.assertIsNone(max_packet_size)

    def test_compute_max_response_packet_mtu_drops_below_min(self):
        base_domain = _make_domain(90)
        server = DnsServer.__new__(DnsServer)
        server._base_domain = base_domain
        server._label_max_len = 50
        server._edns_size = DNS_STANDARD_SIZE
        server._cname_suffix = '0.%s' % base_domain
        server._opt_record_len = 0
        server._recv_packet_mtu = codec.calc_query_mtu(
            base_domain,
            server._label_max_len,
        )
        max_response_packet_mtu = server._compute_max_response_packet_mtu()
        self.assertLess(max_response_packet_mtu, MIN_PACKET_MTU)

    def test_update_bob_data_ignores_empty_payload(self):
        client = self._make_clamp_client()
        client._bob_has_data_remaining = 1
        client._poll_hint_budget = 2
        client._retransmit_guard = True
        client._update_bob_data_from_payload(b'')
        self.assertEqual(client._bob_has_data_remaining, 1)
        self.assertEqual(client._poll_hint_budget, 2)
        self.assertTrue(client._retransmit_guard)

    def test_update_bob_data_ignores_short_header(self):
        client = self._make_clamp_client()
        client._bob_has_data_remaining = 1
        client._poll_hint_budget = 2
        client._retransmit_guard = True
        client._update_bob_data_from_payload(b'\x00')
        self.assertEqual(client._bob_has_data_remaining, 1)
        self.assertEqual(client._poll_hint_budget, 2)
        self.assertTrue(client._retransmit_guard)

    def test_update_bob_data_ignores_handshake_flags(self):
        client = self._make_clamp_client()
        client._bob_has_data_remaining = 1
        client._poll_hint_budget = 2
        payload = _encode_flags(FLAG_SYN)
        client._update_bob_data_from_payload(payload)
        self.assertEqual(client._bob_has_data_remaining, 1)
        self.assertEqual(client._poll_hint_budget, 2)

    def test_update_bob_data_ignores_missing_content_flags(self):
        client = self._make_clamp_client()
        client._bob_has_data_remaining = 1
        payload = _encode_flags(0)
        client._update_bob_data_from_payload(payload)
        self.assertEqual(client._bob_has_data_remaining, 1)

    def test_update_bob_data_ignores_poll_hint_only(self):
        client = self._make_clamp_client()
        client._poll_hint_budget = 1
        payload = _encode_flags(FLAG_POLL_HINT)
        client._update_bob_data_from_payload(payload)
        self.assertEqual(client._poll_hint_budget, 1)

    def test_update_bob_data_ignores_multiple_content_flags(self):
        client = self._make_clamp_client()
        client._bob_has_data_remaining = 1
        payload = _encode_flags(FLAG_KEEPALIVE | FLAG_HAS_SEGMENTS)
        client._update_bob_data_from_payload(payload)
        self.assertEqual(client._bob_has_data_remaining, 1)

    def test_update_bob_data_sets_poll_hint_budget_on_keepalive(self):
        client = self._make_clamp_client()
        client._max_in_flight = 4
        client._bob_has_data_remaining = 1
        payload = _encode_flags(FLAG_KEEPALIVE | FLAG_POLL_HINT)
        client._update_bob_data_from_payload(payload)
        self.assertEqual(client._poll_hint_budget, 4)
        self.assertTrue(client._retransmit_guard)
        self.assertEqual(client._bob_has_data_remaining, 0)

    def test_update_bob_data_sets_poll_hint_budget_on_segments(self):
        client = self._make_clamp_client()
        client._max_in_flight = 3
        client._bob_has_data_polls = 3
        payload = _encode_flags(FLAG_HAS_SEGMENTS | FLAG_POLL_HINT)
        client._update_bob_data_from_payload(payload)
        self.assertEqual(client._poll_hint_budget, 3)
        self.assertTrue(client._retransmit_guard)
        self.assertEqual(client._bob_has_data_remaining, 3)

    def test_reset_poll_hint_budget_clamps_invalid_max_in_flight(self):
        client = self._make_clamp_client()
        client._max_in_flight = 'bad'
        client._poll_hint_budget = 0
        client._retransmit_guard = False
        client._reset_poll_hint_budget()
        self.assertEqual(client._poll_hint_budget, 1)
        self.assertTrue(client._retransmit_guard)

    def test_consume_poll_hint_budget_clears_retransmit_guard(self):
        client = self._make_clamp_client()
        client._poll_hint_budget = 1
        client._retransmit_guard = True
        client._consume_poll_hint_budget()
        self.assertEqual(client._poll_hint_budget, 0)
        self.assertFalse(client._retransmit_guard)


if __name__ == '__main__':
    unittest.main()
