"""Deterministic parent-deadline and startup ownership regressions."""
import sys

import pytest

from spatial_engine import query_runner as module
from spatial_engine.errors import DomainError


def echo_command():
    return [sys.executable, '-u', '-c',
            'import json,sys\n'
            'print(json.dumps({"ready":True,"protocolVersion":1}),flush=True)\n'
            'for line in sys.stdin:\n'
            ' print(json.dumps({"ok":True,"result":{"synthetic":True}}),flush=True)\n']


@pytest.mark.parametrize('elapsed,reason,expected', [
    (1.99, None, None),
    (2.0, None, 'query_timeout'),
    (2.1, None, 'query_timeout'),
    (0.1, 'query_memory_limit', 'query_memory_limit'),
])
def test_parent_rejects_late_or_guard_rejected_success_and_next_worker_recovers(monkeypatch, elapsed, reason, expected):
    clock = [100.0]
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0], raising=False)
    runner = module.QueryRunner(command_factory=echo_command)
    try:
        assert runner._ready.wait(10), 'Synthetic worker readiness'
        old = runner._process
        receive = runner._frames.get
        def controlled_receive(*args, **kwargs):
            response = receive(*args, **kwargs)
            clock[0] += elapsed
            runner._guard.reason = reason
            return response
        monkeypatch.setattr(runner._frames, 'get', controlled_receive)
        if expected is None:
            assert runner.execute('vector.page', {}) == {'synthetic': True}
            assert runner._process is old
        else:
            with pytest.raises(DomainError) as error:
                runner.execute('vector.page', {})
            assert error.value.kind == expected
            assert old.poll() is not None
            assert runner._process is not old
            assert runner._ready.wait(10), 'Replacement worker readiness'
            assert runner.execute('vector.page', {}) == {'synthetic': True}
    finally:
        runner.close()


@pytest.mark.parametrize('stage', ['job', 'guard', 'reader'])
def test_startup_failure_reaps_owned_process_closes_pipes_and_allows_next_start(monkeypatch, stage):
    processes, jobs = [], []
    popen, job_factory, guard_factory, thread_factory = module.subprocess.Popen, module.ProcessTreeJob, module.ResourceGuard, module.threading.Thread
    def captured_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    def job(pid):
        if stage == 'job':
            raise OSError('injected job assignment failure')
        result = job_factory(pid)
        jobs.append(result)
        return result
    def guard(*args, **kwargs):
        if stage == 'guard':
            raise OSError('injected guard construction failure')
        return guard_factory(*args, **kwargs)
    class FailedReader:
        def start(self):
            raise RuntimeError('injected reader start failure')
    def thread(*args, **kwargs):
        if stage == 'reader' and getattr(kwargs.get('target'), '__name__', '') == '_reader':
            return FailedReader()
        return thread_factory(*args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module.subprocess, 'Popen', captured_process)
            patch.setattr(module, 'ProcessTreeJob', job)
            patch.setattr(module, 'ResourceGuard', guard)
            patch.setattr(module.threading, 'Thread', thread)
            with pytest.raises((OSError, RuntimeError)):
                module.QueryRunner(command_factory=echo_command)
            assert len(processes) == 1
            assert processes[0].poll() is not None, 'Startup failure left an owned child alive'
            assert processes[0].stdin.closed and processes[0].stdout.closed
            assert all(item._handle is None for item in jobs)
        runner = module.QueryRunner(command_factory=echo_command)
        try:
            assert runner._ready.wait(10)
            assert runner.execute('vector.page', {}) == {'synthetic': True}
        finally:
            runner.close()
    finally:
        # Failure diagnostics must not leak the pre-fix child into subsequent tests.
        for item in jobs:
            item.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream and not stream.closed:
                    stream.close()


def test_failed_restart_leaves_same_runner_recoverable(monkeypatch):
    runner = module.QueryRunner(command_factory=echo_command)
    try:
        assert runner._ready.wait(10)
        runner._dispose_process()
        with monkeypatch.context() as patch:
            def failed_job(pid):
                raise OSError('injected restart job failure')
            patch.setattr(module, 'ProcessTreeJob', failed_job)
            with pytest.raises(OSError):
                runner.execute('vector.page', {})
        assert runner._process is None and runner._job is None and runner._guard is None
        with pytest.raises(DomainError) as unready:
            runner.execute('vector.page', {})
        assert unready.value.kind == 'query_unready'
        assert runner._ready.wait(10)
        assert runner.execute('vector.page', {}) == {'synthetic': True}
    finally:
        runner.close()
