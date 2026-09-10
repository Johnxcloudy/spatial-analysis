from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

from spatial_engine.errors import DomainError
from spatial_engine.projects import ProjectStore
from spatial_engine.workspace import WorkspaceStore
from spatial_engine.tasks import TaskManager


def test_save_as_rejects_post_worker_same_size_mutation(tmp_path, monkeypatch):
    from spatial_engine import portability
    from test_portability import _setup, _publish, _wait
    from test_projects import valid_save
    from test_workspace_tasks import _dataset
    projects, project, workspace, manager = _setup(tmp_path)
    dataset = _publish(project, workspace, _dataset())
    original = workspace.managed_path(dataset).read_bytes()
    prepare = portability.prepare_copy_publication
    def mutate(ws, task_id, result):
        prepare(ws, task_id, result)
        _, temporary, _ = portability._journal(ws, task_id)
        target = temporary / dataset['relativePath']
        data = target.read_bytes()
        target.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    monkeypatch.setattr(portability, 'prepare_copy_publication', mutate)
    try:
        task = manager.start_save_as({**valid_save(project), 'directory': str(tmp_path / 'copy')})
        result = _wait(manager, project, task)
        assert result['status'] == 'failed', result
        assert not (tmp_path / 'copy').exists()
        assert workspace.managed_path(dataset).read_bytes() == original
    finally:
        manager.close()
        projects.close()


def test_save_as_registration_failure_retains_verified_copy_for_recovery(tmp_path):
    from test_portability import _setup, _wait
    from test_projects import valid_save
    projects, project, workspace, manager = _setup(tmp_path)
    try:
        with workspace._connect(Path(project['projectPath'])) as connection:
            connection.execute("CREATE TRIGGER reject_copy_registration BEFORE UPDATE ON tasks "
                               "WHEN NEW.kind='save_as' AND NEW.status='completed' "
                               "BEGIN SELECT RAISE(ABORT, 'registration failed'); END")
        task = manager.start_save_as({**valid_save(project), 'directory': str(tmp_path / 'copy')})
        result = _wait(manager, project, task)
        assert result['status'] == 'interrupted' and result['stage'] == 'publication_pending'
        assert (tmp_path / 'copy' / 'project.spa').is_file()
        with workspace._connect(Path(project['projectPath'])) as connection:
            connection.execute('DROP TRIGGER reject_copy_registration')
        fresh = WorkspaceStore(projects)
        fresh.recover()
        deadline = time.monotonic() + 15
        while fresh.task(task['id'])['status'] == 'running' and time.monotonic() < deadline:
            fresh.recover()
            time.sleep(.02)
        assert fresh.task(task['id'])['status'] == 'completed'
        fresh.close_recovery()
    finally:
        manager.close()
        projects.close()


@pytest.mark.parametrize('failure', ['cancel', 'startup'])
def test_save_as_cancel_during_verification_removes_owned_destination(tmp_path, monkeypatch, failure):
    import spatial_engine.tasks as tasks
    from test_portability import _setup
    from test_projects import valid_save
    projects, project, workspace, manager = _setup(tmp_path)
    command = tasks._default_command
    def delayed(request):
        if json.loads(request.read_text())['kind'] == 'verify_recovery':
            if failure == 'startup':
                return [str(tmp_path / 'missing-worker.exe')]
            return [sys.executable, '-c', 'import time; time.sleep(60)']
        return command(request)
    monkeypatch.setattr(tasks, '_default_command', delayed)
    try:
        task = manager.start_save_as({**valid_save(project), 'directory': str(tmp_path / 'copy')})
        deadline = time.monotonic() + 15
        while manager._operation != 'verify_recovery' and workspace.task(task['id'])['status'] == 'running' and time.monotonic() < deadline:
            manager.harvest()
            time.sleep(.02)
        if failure == 'startup':
            assert workspace.task(task['id'])['status'] == 'failed'
            assert not (tmp_path / 'copy').exists()
            assert not manager._copy_leases
            return
        assert manager._operation == 'verify_recovery'
        with pytest.raises(DomainError) as blocked:
            manager.require_mutation_allowed()
        assert blocked.value.kind == 'task_busy'
        assert (tmp_path / 'copy' / '.spa-copy-pending.json').is_file()
        before = time.monotonic()
        result = manager.cancel({'path': project['projectPath'], 'taskId': task['id']})
        assert time.monotonic() - before < 5
        assert result['status'] == 'cancelled'
        assert not (tmp_path / 'copy').exists()
        assert not manager._copy_leases
    finally:
        manager.close()
        projects.close()


def test_schema_six_keeps_old_tasks_and_backs_up_v5(tmp_path: Path):
    projects = ProjectStore()
    project = projects.create({'directory': str(tmp_path / 'project'), 'name': 'Migration'})
    workspace = WorkspaceStore(projects)
    old = workspace.create_task('import')
    workspace.update_task(old['id'], status='cancelled', stage='cancelled')
    projects.close()
    with sqlite3.connect(project['projectPath']) as db:
        db.execute('PRAGMA user_version=5')
    reopened = projects.open({'path': project['projectPath']})
    try:
        assert reopened['schemaVersion'] == 7
        assert workspace.task(old['id'])['status'] == 'cancelled'
        task = workspace.create_task('analysis')
        assert task['kind'] == 'analysis'
        backups = list((Path(project['projectPath']).parent / 'backups').glob('project-v5-*.spa'))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 5
            assert db.execute('SELECT status FROM tasks').fetchone()[0] == 'cancelled'
    finally:
        projects.close()


def test_publication_attestation_rejects_changed_bytes(tmp_path: Path):
    from spatial_engine import publication
    artifact = tmp_path / 'snapshot.gpkg'
    artifact.write_bytes(b'original immutable bytes')
    proof = publication.verify_artifact({'path': str(artifact)}, tmp_path,
                                       progress=lambda *args: None, cancelled=lambda: False)
    lease = publication.ArtifactLease(artifact)
    sealed = publication.VerifiedArtifact(proof, lease)
    assert publication.check_proof(artifact, sealed) == (24, hashlib.sha256(artifact.read_bytes()).hexdigest())
    lease.close()
    artifact.write_bytes(b'changed immutable bytes!')
    with pytest.raises(DomainError):
        publication.check_proof(artifact, sealed)


def test_publication_hash_checks_cancellation(tmp_path: Path):
    from spatial_engine import publication
    artifact = tmp_path / 'snapshot.gpkg'
    artifact.write_bytes(b'a' * (3 * 1024 * 1024))
    calls = []
    with pytest.raises(DomainError) as error:
        publication.verify_artifact({'path': str(artifact)}, tmp_path,
                                    progress=lambda *args: calls.append(args), cancelled=lambda: bool(calls))
    assert error.value.kind == 'task_cancelled'


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory write sharing')
def test_publication_lease_blocks_same_size_write_and_releases(tmp_path):
    from spatial_engine import publication
    artifact = tmp_path / 'snapshot.gpkg'
    artifact.write_bytes(b'original')
    lease = publication.ArtifactLease(artifact)
    try:
        proof = publication.verify_artifact({'path': str(artifact)}, tmp_path,
                                           progress=lambda *args: None, cancelled=lambda: False)
        with pytest.raises(PermissionError):
            artifact.write_bytes(b'modified')
        sealed = publication.VerifiedArtifact(proof, lease)
        assert publication.check_proof(artifact, sealed)[1] == hashlib.sha256(b'original').hexdigest()
    finally:
        lease.close()
    artifact.write_bytes(b'modified')
    with pytest.raises(DomainError):
        publication.check_proof(artifact, sealed)


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory lease identity')
def test_publication_lease_rejects_path_swapped_while_opening(tmp_path, monkeypatch):
    from spatial_engine import publication
    artifact = tmp_path / 'snapshot.gpkg'
    artifact.write_bytes(b'original')
    original_identity = publication._identity
    def swap(path):
        artifact.rename(tmp_path / 'leased-old.gpkg')
        artifact.write_bytes(b'changed!')
        return original_identity(path)
    monkeypatch.setattr(publication, '_identity', swap)
    with pytest.raises(DomainError, match='identity'):
        publication.ArtifactLease(artifact)


@pytest.mark.skipif(os.name != 'nt', reason='Windows cross-directory lease identity')
def test_publication_lease_tracks_handle_across_rename(tmp_path):
    from spatial_engine import publication
    artifact = tmp_path / 'snapshot.gpkg'
    artifact.write_bytes(b'original')
    target_dir = tmp_path / 'published'
    target_dir.mkdir()
    final = target_dir / 'final.gpkg'
    lease = publication.ArtifactLease(artifact)
    try:
        artifact.rename(final)
        lease.require_identity(final)
        final.rename(target_dir / 'actual.gpkg')
        final.write_bytes(b'changed!')
        with pytest.raises(DomainError, match='identity'):
            lease.require_identity(final)
    finally:
        lease.close()


def _import_params(project, source):
    return {'path': project['projectPath'], 'sourcePath': str(source), 'sourceLayer': source.stem,
            'encoding': None, 'assignedCrs': None}


def _source(tmp_path):
    path = tmp_path / 'parcels.geojson'
    path.write_text(json.dumps({'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'properties': {'code': '001'},
        'geometry': {'type': 'Polygon', 'coordinates': [[[114,27],[114.01,27],[114.01,27.01],[114,27.01],[114,27]]]}
    }]}), encoding='utf-8')
    return path


def _wait(manager, project, task, timeout=20):
    deadline = time.monotonic() + timeout
    while task['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(0.03)
        task = manager.get({'path': project['projectPath'], 'taskId': task['id']})
    return task


def test_task_watchdog_without_polling_then_next_task_succeeds(tmp_path):
    projects = ProjectStore()
    project = projects.create({'directory': str(tmp_path / 'project'), 'name': 'Guard'})
    workspace = WorkspaceStore(projects)
    manager = TaskManager(projects, workspace, timeout_seconds=0.2,
                          command_factory=lambda request: [sys.executable, '-c', 'import time; time.sleep(60)'])
    source = _source(tmp_path)
    try:
        task = manager.start_import(_import_params(project, source))
        # Waiting on the child, without any task.get/harvest RPC, proves autonomy.
        manager._process.wait(timeout=5)
        result = manager.get({'path': project['projectPath'], 'taskId': task['id']})
        assert result['status'] == 'failed' and 'task_timeout' in result['error']
        assert workspace.get({'path': project['projectPath']})['datasets'] == []
        manager.close()
        manager = TaskManager(projects, workspace)
        following = _wait(manager, project, manager.start_import(_import_params(project, source)))
        assert following['status'] == 'completed', following
    finally:
        manager.close()
        projects.close()


def test_cancel_during_publication_verification_is_bounded(tmp_path, monkeypatch):
    import spatial_engine.tasks as tasks_module
    projects = ProjectStore()
    project = projects.create({'directory': str(tmp_path / 'project'), 'name': 'Cancel verification'})
    workspace = WorkspaceStore(projects)
    manager = TaskManager(projects, workspace)
    source = _source(tmp_path)
    try:
        task = manager.start_import(_import_params(project, source))
        with monkeypatch.context() as patch:
            patch.setattr(tasks_module, '_default_command', lambda request: [sys.executable, '-c', 'import time; time.sleep(60)'])
            deadline = time.monotonic() + 15
            while manager._operation != 'verify_publication' and time.monotonic() < deadline:
                time.sleep(0.03)
                task = manager.get({'path': project['projectPath'], 'taskId': task['id']})
            assert manager._operation == 'verify_publication'
            child = manager._process
            start = time.monotonic()
            result = manager.cancel({'path': project['projectPath'], 'taskId': task['id']})
            assert time.monotonic() - start <= 5
            assert result['status'] == 'cancelled' and child.poll() is not None
            assert not workspace.has_pending(task['id'])
            assert workspace.get({'path': project['projectPath']})['datasets'] == []
        following = _wait(manager, project, manager.start_import(_import_params(project, source)))
        assert following['status'] == 'completed', following
    finally:
        manager.close()
        projects.close()


@pytest.mark.parametrize('failure', ['domain', 'monitor_start'])
def test_failed_export_verifier_removes_owned_pending_file(tmp_path, monkeypatch, failure):
    import spatial_engine.tasks as tasks_module
    projects = ProjectStore()
    project = projects.create({'directory': str(tmp_path / 'project'), 'name': 'Export failure'})
    workspace = WorkspaceStore(projects)
    manager = TaskManager(projects, workspace)
    try:
        imported = _wait(manager, project, manager.start_import(_import_params(project, _source(tmp_path))))
        assert imported['status'] == 'completed', imported
        destination = tmp_path / 'failed.gpkg'
        task = manager.start_export({'path': project['projectPath'], 'datasetId': imported['datasetId'], 'destination': str(destination)})
        code = ("import pathlib,json,sys; p=pathlib.Path(sys.argv[1]).parent; "
                "(p/'result.json').write_text(json.dumps({'ok':False,'error':{'kind':'invalid_worker_result','message':'Injected verification failure'}}))")
        with monkeypatch.context() as patch:
            if failure == 'domain':
                patch.setattr(tasks_module, '_default_command', lambda request: [sys.executable, '-c', code, str(request)])
            else:
                import spatial_engine.resource_guard as resource_guard
                def fail_monitor(*args, **kwargs):
                    raise OSError('Injected verification failure')
                patch.setattr(resource_guard, 'ResourceGuard', fail_monitor)
            failed = _wait(manager, project, task)
        assert failed['status'] == 'failed' and 'Injected verification failure' in failed['error']
        assert not destination.exists()
        assert not destination.with_name(f'.{destination.name}.{task["id"]}.pending').exists()
        assert not (Path(project['projectPath']).parent / 'staging' / 'tasks' / task['id']).exists()
        following = manager.start_export({'path': project['projectPath'], 'datasetId': imported['datasetId'], 'destination': str(destination)})
        assert _wait(manager, project, following)['status'] == 'completed'
    finally:
        manager.close()
        projects.close()
