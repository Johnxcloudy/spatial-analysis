import importlib.util
import hashlib
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from spatial_engine import vectors, vector_queries
from spatial_engine.errors import DomainError
from test_vectors import import_path
from vector_fixtures import create_fixture_bundle


def test_import_and_export_do_not_materialize_whole_arrow_table(tmp_path, monkeypatch):
    source = create_fixture_bundle(tmp_path / 'sources')['gpkg']
    monkeypatch.setattr(vectors, 'BATCH_SIZE', 2)
    def forbidden(*args, **kwargs):
        pytest.fail('Full snapshot Arrow materialization is forbidden')
    monkeypatch.setattr(vectors.pyogrio, 'read_arrow', forbidden)
    result = import_path(source, tmp_path / 'work')
    vectors.verify_snapshot(Path(result['artifactPath']), result['dataset'])


def test_last_page_offset_is_available(tmp_path):
    source = create_fixture_bundle(tmp_path / 'sources')['gpkg']
    result = import_path(source, tmp_path / 'work')
    page = vector_queries.attribute_page(result['dataset'], Path(result['artifactPath']), {'offset': 499_999, 'limit': 1})
    assert page['rows'] == [] and page['offset'] == 499_999


def test_resource_guard_is_available():
    assert importlib.util.find_spec('spatial_engine.resource_guard') is not None


def test_query_runner_is_available():
    assert importlib.util.find_spec('spatial_engine.query_runner') is not None


def test_guard_terminates_without_polling(tmp_path):
    from spatial_engine.resource_guard import ResourceGuard
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    guard = ResourceGuard(process, timeout_seconds=0.15)
    try:
        process.wait(timeout=3)
        assert guard.reason == 'task_timeout'
    finally:
        guard.terminate()
        guard.stop()


def test_guard_memory_limit_is_autonomous():
    from spatial_engine.resource_guard import ResourceGuard
    process = subprocess.Popen([sys.executable, '-c', 'import time; data=bytearray(32*1024*1024); time.sleep(60)'])
    guard = ResourceGuard(process, memory_bytes=1024 * 1024)
    try:
        process.wait(timeout=3)
        assert guard.reason == 'task_memory_limit'
    finally:
        guard.terminate()
        guard.stop()


def test_guard_accepts_already_exited_process():
    from spatial_engine.resource_guard import ResourceGuard
    process = subprocess.Popen([sys.executable, '-c', 'pass'])
    process.wait(timeout=3)
    guard = ResourceGuard(process)
    guard.stop()
    assert guard.reason is None


def test_guard_reaps_descendant_after_worker_exits(tmp_path):
    import psutil
    from spatial_engine.resource_guard import ResourceGuard
    pid_file = tmp_path / 'child.pid'
    script = 'import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); Path(sys.argv[1]).write_text(str(child.pid));time.sleep(0.3)'
    process = subprocess.Popen([sys.executable, '-c', script, str(pid_file)])
    guard = ResourceGuard(process)
    try:
        process.wait(timeout=3)
        child_pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not psutil.pid_exists(child_pid)
    finally:
        guard.terminate()
        guard.stop()


def _ready(runner):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if runner._ready.wait(0.02):
            return
    pytest.fail('Query worker did not become ready')


def test_query_timeout_recovers_and_allowlist_rejects_mutation(tmp_path):
    from spatial_engine.query_runner import QueryRunner
    script = tmp_path / 'query.py'
    script.write_text('import sys,json,time\nprint(json.dumps({"ready":True,"protocolVersion":1}),flush=True)\nfor line in sys.stdin:\n time.sleep(60)\n')
    runner = QueryRunner(command_factory=lambda: [sys.executable, '-u', str(script)], timeout_seconds=0.1)
    try:
        with pytest.raises(DomainError) as rejected:
            runner.execute('project.create', {})
        assert rejected.value.kind == 'invalid_query'
        _ready(runner)
        old_pid = runner._process.pid
        with pytest.raises(DomainError) as timeout:
            runner.execute('source.inspect', {'params': {}})
        assert timeout.value.kind == 'query_timeout'
        _ready(runner)
        assert runner._process.pid != old_pid
    finally:
        runner.close()


def test_query_cold_start_returns_unready(tmp_path):
    from spatial_engine.query_runner import QueryRunner
    runner = QueryRunner(command_factory=lambda: [sys.executable, '-c', 'import time; time.sleep(60)'], warmup_seconds=0.15)
    try:
        with pytest.raises(DomainError) as error:
            runner.execute('source.inspect', {'params': {}})
        assert error.value.kind == 'query_unready'
        runner._process.wait(timeout=3)
    finally:
        runner.close()


def test_attribute_sql_deadline(tmp_path, monkeypatch):
    source = create_fixture_bundle(tmp_path / 'sources')['gpkg']
    result = import_path(source, tmp_path / 'work')
    monkeypatch.setattr(vector_queries, 'SQL_SECONDS', -1)
    connection = vector_queries._connect(Path(result['artifactPath']))
    try:
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            connection.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n').fetchone()
    finally:
        connection.close()


def test_native_query_process_inspects_and_reads_page(tmp_path):
    from spatial_engine.query_runner import QueryRunner
    source = create_fixture_bundle(tmp_path / 'sources')['gpkg']
    imported = import_path(source, tmp_path / 'work')
    runner = QueryRunner()
    try:
        _ready(runner)
        inspection = runner.execute('source.inspect', {'params': {'sourcePath': source, 'encoding': None}})
        assert inspection['driver'] == 'GPKG'
        page = runner.execute('vector.page', {'dataset': imported['dataset'],
            'managedPath': imported['artifactPath'], 'params': {'offset': 0, 'limit': 2}})
        assert page['total'] == 4 and len(page['rows']) == 2
    finally:
        runner.close()


@pytest.mark.parametrize('budget,value', [('MAX_GEOMETRY_VERTICES', 3), ('MAX_SNAPSHOT_BYTES', 1)])
def test_streamed_import_rejects_geometry_and_file_budgets(tmp_path, monkeypatch, budget, value):
    source = create_fixture_bundle(tmp_path / 'sources')['gpkg']
    before = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    monkeypatch.setattr(vectors, budget, value)
    with pytest.raises(DomainError) as error:
        import_path(source, tmp_path / 'work')
    assert error.value.kind == 'source_limit'
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == before


def test_query_recovers_after_ready_worker_exits():
    from spatial_engine.query_runner import QueryRunner
    runner = QueryRunner()
    try:
        _ready(runner)
        old_pid = runner._process.pid
        runner._process.kill()
        runner._process.wait(timeout=3)
        deadline = time.monotonic() + 3
        while runner._ready.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(DomainError) as error:
            runner.execute('source.inspect', {'params': {}})
        assert error.value.kind == 'query_unready'
        _ready(runner)
        assert runner._process.pid != old_pid
    finally:
        runner.close()


def test_query_memory_overrun_is_distinct_from_timeout(tmp_path):
    from spatial_engine.query_runner import QueryRunner
    script = tmp_path / 'query.py'
    script.write_text('import sys,json,time\nprint(json.dumps({"ready":True,"protocolVersion":1}),flush=True)\nfor line in sys.stdin:\n data=bytearray(128*1024*1024)\n time.sleep(60)\n')
    runner = QueryRunner(command_factory=lambda: [sys.executable, '-u', str(script)], memory_bytes=64*1024*1024)
    try:
        _ready(runner)
        with pytest.raises(DomainError) as error:
            runner.execute('source.inspect', {'params': {}})
        assert error.value.kind == 'query_memory_limit'
    finally:
        runner.close()
