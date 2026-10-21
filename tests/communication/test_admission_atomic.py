"""Atomic 64-connection admission tests (Objective 1).

Deterministic: threading.Barrier/Event, no timing sleeps for proof.
"""

import socket
import threading
import time

from core.communication.connection import ConnectionSession
from core.communication.tcp import MAX_CONNECTIONS, TcpTransport


def test_64_simultaneous_admissions_succeed():
    t = TcpTransport(host="127.0.0.1", port=0)
    barrier = threading.Barrier(64)
    results = [None] * 64

    def worker(i):
        barrier.wait(timeout=10)
        results[i] = t.try_reserve_slot()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(64)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)
    assert all(r is True for r in results)
    assert t.reserved_slots() == 64
    # Commit all reservations as sessions.
    for _ in range(64):
        s = ConnectionSession(remote_address="x")
        assert t._register_session(s, has_reservation=True) is True
    assert t.connection_count() == 64
    assert t.reserved_slots() == 0


def test_65th_simultaneous_admission_rejected():
    t = TcpTransport(host="127.0.0.1", port=0)
    barrier = threading.Barrier(65)
    results = [None] * 65

    def worker(i):
        barrier.wait(timeout=10)
        results[i] = t.try_reserve_slot()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(65)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)
    assert sum(1 for r in results if r) == 64
    assert sum(1 for r in results if not r) == 1
    assert t.rejected_connections() == 1


def test_100_simultaneous_admissions_cap_64():
    t = TcpTransport(host="127.0.0.1", port=0)
    barrier = threading.Barrier(100)
    results = [None] * 100

    def worker(i):
        barrier.wait(timeout=15)
        results[i] = t.try_reserve_slot()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=15)
    admitted = sum(1 for r in results if r)
    assert admitted == 64
    assert t.reserved_slots() + t.connection_count() <= 64
    assert t.rejected_connections() == 36


def test_rejected_connections_counted():
    t = TcpTransport(host="127.0.0.1", port=0)
    for _ in range(MAX_CONNECTIONS):
        assert t.try_reserve_slot() is True
    assert t.try_reserve_slot() is False
    assert t.try_reserve_slot() is False
    assert t.rejected_connections() == 2


def test_failed_tls_releases_slot(monkeypatch):

    t = TcpTransport(host="127.0.0.1", port=0)
    assert t.try_reserve_slot() is True
    assert t.reserved_slots() == 1
    # Simulate accept-loop TLS failure path.
    t.release_reservation(count_rejection=True)
    assert t.reserved_slots() == 0
    assert t.rejected_connections() == 1
    # Slot reusable.
    assert t.try_reserve_slot() is True


def test_closed_connections_release_slots():
    t = TcpTransport(host="127.0.0.1", port=0)
    sessions = []
    for _ in range(64):
        assert t.try_reserve_slot() is True
        s = ConnectionSession(remote_address="x")
        assert t._register_session(s, has_reservation=True) is True
        sessions.append(s)
    assert t.try_reserve_slot() is False
    t._remove_session(sessions[0].connection_id)
    assert t.connection_count() == 63
    assert t.try_reserve_slot() is True
    s = ConnectionSession(remote_address="new")
    assert t._register_session(s, has_reservation=True) is True
    assert t.connection_count() == 64


def test_no_reservation_leak_after_shutdown():
    t = TcpTransport(host="127.0.0.1", port=0)
    for _ in range(10):
        assert t.try_reserve_slot() is True
    t.stop()  # stop() closes all connections and clears reservations
    assert t.reserved_slots() == 0
    assert t.connection_count() == 0


def test_live_burst_never_exceeds_64():
    port = _free_port()
    t = TcpTransport(host="127.0.0.1", port=port)
    t.register("service:echo", lambda m: m.create_response(source="s", payload={}))
    t.start()
    time.sleep(0.2)
    try:
        # Occupy 60 slots directly, then burst 20 live sockets.
        holders = []
        for _ in range(60):
            assert t.try_reserve_slot() is True
            s = ConnectionSession(remote_address="hold")
            assert t._register_session(s, has_reservation=True) is True
            holders.append(s)
        assert t.connection_count() == 60
        peak = [60]

        def burst_one():
            try:
                s = socket.socket()
                s.settimeout(2)
                s.connect(("127.0.0.1", port))
                time.sleep(0.3)
                s.close()
            except Exception:
                pass

        threads = [threading.Thread(target=burst_one) for _ in range(20)]
        for th in threads:
            th.start()
        time.sleep(0.15)
        peak.append(t.connection_count() + t.reserved_slots())
        for th in threads:
            th.join(timeout=5)
        time.sleep(0.5)
        assert max(peak) <= 64
        assert t.connection_count() + t.reserved_slots() <= 64
    finally:
        t.stop()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
