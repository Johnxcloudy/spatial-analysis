"""Synthetic barriers exercise monitor retirement without scheduler timing claims."""
import threading
from types import SimpleNamespace

import psutil
import pytest

from spatial_engine import resource_guard as module


@pytest.mark.parametrize('blocked_at', ['tree', 'sample', 'error', 'missing', 'exit'])
def test_retired_monitor_cannot_publish_or_kill(monkeypatch, blocked_at):
    entered, release = threading.Event(), threading.Event()
    kills = []

    def block():
        if threading.current_thread().name == 'gis-resource-guard':
            entered.set()
            assert release.wait(5)

    class Member:
        pid = 123
        def children(self, recursive):
            if blocked_at == 'tree':
                block()
            return []
        def is_running(self):
            return True
        def memory_info(self):
            if blocked_at in ('sample', 'error', 'missing'):
                block()
            if blocked_at == 'error':
                raise psutil.AccessDenied(self.pid)
            if blocked_at == 'missing':
                raise psutil.NoSuchProcess(self.pid)
            return SimpleNamespace(rss=100)
        def kill(self):
            kills.append(self.pid)

    def poll():
        if blocked_at == 'exit' and threading.current_thread().name == 'gis-resource-guard':
            block()
            return 0
        return None

    monkeypatch.setattr(module.psutil, 'Process', lambda pid: Member())
    monkeypatch.setattr(module.psutil, 'wait_procs', lambda *a, **k: ([], []))
    guard = module.ResourceGuard(SimpleNamespace(pid=123, poll=poll), prefix='query',
                                 memory_bytes=1, interval=0.001)
    join = guard._thread.join
    try:
        assert entered.wait(5)
        # Simulate the bounded join expiring while the sampler remains blocked.
        monkeypatch.setattr(guard._thread, 'join', lambda **kw: None)
        guard.stop()
        release.set()
        join(5)
        assert not guard._thread.is_alive()
        assert guard.reason is None
        assert kills == []
        guard.terminate()  # Explicit shutdown remains valid after retirement.
        assert kills == [123]
    finally:
        release.set()
        join(5)


def test_stop_during_enforcement_discovery_prevents_later_kill(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    kills = []
    calls = []

    class Member:
        pid = 123
        def children(self, recursive):
            calls.append(True)
            if len(calls) == 2:
                entered.set()
                assert release.wait(5)
            return []
        def is_running(self):
            return True
        def memory_info(self):
            return SimpleNamespace(rss=100)
        def kill(self):
            kills.append(self.pid)

    monkeypatch.setattr(module.psutil, 'Process', lambda pid: Member())
    monkeypatch.setattr(module.psutil, 'wait_procs', lambda *a, **k: ([], []))
    guard = module.ResourceGuard(SimpleNamespace(pid=123, poll=lambda: None),
                                 prefix='query', memory_bytes=1, interval=0.001)
    join = guard._thread.join
    try:
        assert entered.wait(5)
        assert guard.reason == 'query_memory_limit'
        monkeypatch.setattr(guard._thread, 'join', lambda **kw: None)
        guard.stop()
        release.set()
        join(5)
        assert not guard._thread.is_alive()
        assert kills == []
    finally:
        release.set()
        join(5)
