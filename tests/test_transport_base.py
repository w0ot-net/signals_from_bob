# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.transport.transport_base import Transport, TransportError, SendPermit
from sfb import time_provider


class DummyTransport(Transport):
    def __init__(self, max_in_flight=1):
        super(DummyTransport, self).__init__()
        self._max_in_flight = max_in_flight
        self._pending = []

    def reserve_send(self, now=None):
        pending_before = self.pending_count()
        self._ensure_reserved()
        reserved = len(self._reserved)
        if pending_before + reserved >= self._max_in_flight:
            return None
        return self._reserve_permit(now=now, pending_before=pending_before)

    def _send_impl(self, data, permit):
        if data == b'fail':
            raise TransportError('send failed')
        corr_id = len(self._pending)
        self._pending.append(data)
        return corr_id

    def recv(self, timeout=None):
        if self._pending:
            data = self._pending.pop(0)
            return (0, data)
        return (None, None)

    def pending_count(self):
        return len(self._pending)

    @property
    def max_in_flight(self):
        return self._max_in_flight

    @property
    def send_mtu(self):
        return 1024

    @property
    def recv_mtu(self):
        return 1024


class TransportBaseTests(unittest.TestCase):
    def test_send_requires_permit(self):
        transport = DummyTransport()
        with self.assertRaises(TransportError):
            transport.send(b'data', None)

    def test_send_rejects_foreign_permit(self):
        transport = DummyTransport()
        other = DummyTransport()
        permit = transport.reserve_send()
        self.assertIsNotNone(permit)
        with self.assertRaises(TransportError):
            other.send(b'data', permit)

    def test_send_rejects_unreserved_permit(self):
        transport = DummyTransport()
        permit = SendPermit(transport, time_provider.now())
        with self.assertRaises(TransportError):
            transport.send(b'data', permit)

    def test_send_rejects_reuse(self):
        transport = DummyTransport()
        permit = transport.reserve_send()
        self.assertIsNotNone(permit)
        transport.send(b'data', permit)
        with self.assertRaises(TransportError):
            transport.send(b'data', permit)

    def test_reservations_count_toward_capacity(self):
        transport = DummyTransport(max_in_flight=1)
        permit = transport.reserve_send()
        self.assertIsNotNone(permit)
        self.assertIsNone(transport.reserve_send())

    def test_release_send_restores_capacity(self):
        transport = DummyTransport(max_in_flight=1)
        permit = transport.reserve_send()
        self.assertIsNotNone(permit)
        transport.release_send(permit)
        self.assertIsNotNone(transport.reserve_send())

    def test_send_clears_reservation_on_error(self):
        transport = DummyTransport(max_in_flight=1)
        permit = transport.reserve_send()
        self.assertIsNotNone(permit)
        with self.assertRaises(TransportError):
            transport.send(b'fail', permit)
        self.assertIsNotNone(transport.reserve_send())

    def test_send_rejects_released_permit(self):
        transport = DummyTransport()
        permit = transport.reserve_send()
        self.assertIsNotNone(permit)
        transport.release_send(permit)
        with self.assertRaises(TransportError):
            transport.send(b'data', permit)


if __name__ == '__main__':
    unittest.main()
