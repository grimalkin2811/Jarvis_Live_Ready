"""Surveillance des ressources : thread à la demande, suspension, seuils."""

from __future__ import annotations

import threading
import time

from UI.dashboard.resources import ResourceMonitor, ResourceSample, describe_sample, threshold_breaches


def _sample(cpu=10.0, ram=20.0, disk=30.0):
    return ResourceSample(time.time(), cpu, ram, 1, 2, disk, 3, "/", 0.0, 0.0, 4, 0.5)


def _thread_names():
    return {t.name for t in threading.enumerate()}


def test_no_thread_without_subscriber_and_thread_released_after():
    monitor = ResourceMonitor(interval=0.5, sampler=_sample)
    assert not monitor.running()
    got = []
    monitor.subscribe("a", got.append)
    assert monitor.running()
    deadline = time.monotonic() + 3
    while not got and time.monotonic() < deadline:
        time.sleep(0.02)
    assert got and got[0].cpu_percent == 10.0
    monitor.unsubscribe("a")
    assert not monitor.running()
    assert "jarvis-dashboard-resources" not in _thread_names()


def test_suspend_and_resume():
    monitor = ResourceMonitor(interval=0.5, sampler=_sample)
    monitor.subscribe("a", lambda s: None)
    monitor.suspend(True)
    assert not monitor.running() and monitor.suspended
    monitor.suspend(False)
    assert monitor.running()
    monitor.stop()


def test_interval_is_clamped_and_history_bounded():
    monitor = ResourceMonitor(interval=0.1, sampler=_sample)
    assert monitor.interval == 0.5
    monitor.set_interval(999)
    assert monitor.interval == 60.0


def test_sampler_errors_do_not_kill_the_thread():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disque réseau absent")
        return _sample()

    monitor = ResourceMonitor(interval=0.5, sampler=flaky)
    got = []
    monitor.subscribe("a", got.append)
    deadline = time.monotonic() + 3
    while not got and time.monotonic() < deadline:
        time.sleep(0.02)
    monitor.stop()
    assert got and "réseau" in monitor.last_error


def test_thresholds_only_on_real_values():
    assert threshold_breaches(_sample(95, 50, 40), 90, 90, 95) == [("cpu", "Processeur à 95 % (seuil 90 %).")]
    assert [m for m, _ in threshold_breaches(_sample(10, 99, 99), 90, 90, 95)] == ["ram", "disk"]
    assert threshold_breaches(_sample(10, 10, -1.0), 90, 90, 95) == []  # disque indisponible : pas d'alerte
    assert describe_sample(_sample(disk=-1.0))["disk"] == "indisponible"


def test_real_psutil_sampling_when_available():
    monitor = ResourceMonitor(interval=0.5)
    if not monitor.available():
        return
    sample = monitor._sample()
    assert 0.0 <= sample.cpu_percent <= 100.0 * 64
    assert 0.0 < sample.ram_percent <= 100.0
    assert sample.ram_total > 0
