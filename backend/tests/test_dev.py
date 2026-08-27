from __future__ import annotations

import signal

from backend.app import dev


def test_backend_root_is_proven_through_listener_parent(monkeypatch):
    details = {
        300: (200, "python -c multiprocessing.spawn", dev.ROOT_DIR),
        200: (1, "/repo/.venv/bin/python -m backend.app.dev", dev.ROOT_DIR),
    }
    monkeypatch.setattr(dev, "_process_details", lambda pid: details[pid])

    assert dev._backend_root_for(300) == 200


def test_backend_root_rejects_an_unrelated_listener(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dev,
        "_process_details",
        lambda _pid: (1, "python -m unrelated.server", tmp_path),
    )

    assert dev._backend_root_for(400) is None


def test_stale_recovery_stops_only_the_proven_backend(monkeypatch):
    signalled: list[tuple[set[int], signal.Signals]] = []
    availability = iter([False, True])
    monkeypatch.setattr(dev, "_listener_pids", lambda: [300])
    monkeypatch.setattr(dev, "_backend_root_for", lambda _pid: 200)
    monkeypatch.setattr(
        dev,
        "_signal_processes",
        lambda pids, sig: signalled.append((pids, sig)),
    )
    monkeypatch.setattr(dev, "_port_is_available", lambda: next(availability))

    assert dev._recover_stale_console() is True
    assert signalled == [({200}, signal.SIGTERM)]


def test_stale_recovery_refuses_unproven_process(monkeypatch):
    monkeypatch.setattr(dev, "_listener_pids", lambda: [400])
    monkeypatch.setattr(dev, "_backend_root_for", lambda _pid: None)

    assert dev._recover_stale_console() is False
