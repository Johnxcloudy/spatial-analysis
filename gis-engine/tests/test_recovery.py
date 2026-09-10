import hashlib
import time
import uuid
import sys
from pathlib import Path

import pytest

from spatial_engine.projects import ProjectStore
from spatial_engine.workspace import WorkspaceStore
from test_workspace_tasks import _dataset


def pending_import(tmp_path):
    projects = ProjectStore()
    project = projects.create({'directory': str(tmp_path / 'project'), 'name': 'Recovery'})
    workspace = WorkspaceStore(projects)
    dataset = _dataset()
    task_id = str(uuid.uuid4())
    directory = Path(project['projectPath']).parent / 'staging' / 'tasks' / task_id
    directory.mkdir(parents=True)
    artifact = directory / 'snapshot.gpkg'
    artifact.write_bytes(b'x' * (2 * 1024 * 1024))
    dataset['version'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    workspace.create_task('import', task_id=task_id, dataset_id=dataset['id'])
    workspace.prepare_import_publication(task_id, dataset, artifact)
    return projects, project, dataset, task_id


def test_inline_hash_budget_bounds_bytes_even_if_planned_file_grows(tmp_path):
    from spatial_engine.recovery import INLINE_HASH_REMAINING
    from spatial_engine.workspace import _sha256
    from spatial_engine.errors import DomainError
    artifact = tmp_path / 'grown.bin'
    artifact.write_bytes(b'x' * (2 * 1024 * 1024))
    token = INLINE_HASH_REMAINING.set(1024 * 1024)
    try:
        with pytest.raises(DomainError, match='read budget'):
            _sha256(artifact)
        assert INLINE_HASH_REMAINING.get() == 0
    finally:
        INLINE_HASH_REMAINING.reset(token)


def test_large_recovery_never_hashes_in_management_process(tmp_path, monkeypatch):
    import spatial_engine.workspace as module
    projects, project, dataset, task_id = pending_import(tmp_path)
    workspace = WorkspaceStore(projects)
    def forbidden(*args, **kwargs):
        pytest.fail('Large recovery must not hash in the RPC process')
    monkeypatch.setattr(module, '_sha256', forbidden)
    try:
        before = time.perf_counter()
        snapshot = workspace.get({'path': project['projectPath']})
        assert time.perf_counter() - before < 0.5
        assert snapshot['datasets'] == []
        assert workspace.task(task_id)['stage'] == 'verifying_recovery'
        deadline = time.monotonic() + 10
        while workspace.task(task_id)['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
            snapshot = workspace.get({'path': project['projectPath']})
        assert snapshot['datasets'] == [dataset]
        assert workspace.task(task_id)['status'] == 'completed'
    finally:
        if hasattr(workspace, 'close_recovery'):
            workspace.close_recovery()
        projects.close()


@pytest.mark.parametrize('column,value', [
    ('staged_relative_path', 'datasets/other.gpkg'),
    ('final_relative_path', 'datasets/other.gpkg'),
    ('artifact_sha256', '0' * 64),
    ('artifact_size', -1),
    ('artifact_size', 512 * 1024 * 1024 + 1),
])
def test_import_recovery_rejects_unbound_journal_before_hash(tmp_path, monkeypatch, column, value):
    import spatial_engine.workspace as module
    from spatial_engine.errors import DomainError
    projects, project, dataset, task_id = pending_import(tmp_path)
    workspace = WorkspaceStore(projects)
    try:
        with workspace._connect(Path(project['projectPath'])) as connection:
            connection.execute(f'UPDATE pending_publications SET {column} = ? WHERE task_id = ?', (value, task_id))
        monkeypatch.setattr(module, '_sha256', lambda *args: pytest.fail('Invalid journal must be rejected before hashing'))
        with pytest.raises(DomainError):
            workspace.complete_import_publication(task_id, recovering=True)
        assert workspace.has_pending(task_id)
        assert not (Path(project['projectPath']).parent / dataset['relativePath']).exists()
    finally:
        projects.close()


def _finish(workspace, project, task_id):
    deadline = time.monotonic() + 15
    while workspace.task(task_id)['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(.02)
        workspace.recover()
    return workspace.task(task_id)


def test_cancelled_large_recovery_keeps_journal_and_retries_on_open(tmp_path, monkeypatch):
    import spatial_engine.tasks as tasks
    projects, project, dataset, task_id = pending_import(tmp_path)
    workspace = WorkspaceStore(projects)
    command = tasks._default_command
    monkeypatch.setattr(tasks, '_default_command', lambda request: [sys.executable, '-c', 'import time;time.sleep(60)'])
    try:
        workspace.recover()
        before = time.perf_counter()
        result = workspace.cancel_recovery(task_id)
        assert time.perf_counter() - before < 5
        assert result['status'] == 'interrupted' and result['stage'] == 'publication_pending'
        assert workspace.has_pending(task_id)
        workspace.close_recovery()
        monkeypatch.setattr(tasks, '_default_command', command)
        fresh = WorkspaceStore(projects)
        fresh.recover()
        assert _finish(fresh, project, task_id)['status'] == 'completed'
        assert fresh.get({'path': project['projectPath']})['datasets'] == [dataset]
        fresh.close_recovery()
    finally:
        workspace.close_recovery()
        projects.close()


def test_large_recovery_watchdog_preserves_journal_without_polling(tmp_path, monkeypatch):
    import spatial_engine.recovery as recovery
    import spatial_engine.tasks as tasks
    projects, project, dataset, task_id = pending_import(tmp_path)
    workspace = WorkspaceStore(projects)
    original = recovery.RecoveryVerifier
    monkeypatch.setattr(recovery, 'RecoveryVerifier', lambda ws, paths: original(ws, paths, timeout_seconds=.15))
    monkeypatch.setattr(tasks, '_default_command', lambda request: [sys.executable, '-c', 'import time;time.sleep(60)'])
    try:
        workspace.recover()
        workspace._recovery.verifier.process.wait(timeout=5)
        workspace.recover()
        task = workspace.task(task_id)
        assert task['status'] == 'interrupted' and workspace.has_pending(task_id)
        assert 'budget' in task['error']
    finally:
        workspace.close_recovery()
        projects.close()


def test_large_export_recovery_verifies_without_parent_hash(tmp_path, monkeypatch):
    import spatial_engine.workspace as module
    projects = ProjectStore()
    project = projects.create({'directory': str(tmp_path / 'project'), 'name': 'Export recovery'})
    workspace = WorkspaceStore(projects)
    destination = tmp_path / 'export.gpkg'
    task = workspace.create_task('export', destination=str(destination))
    pending = destination.with_name(f'.{destination.name}.{task["id"]}.pending')
    pending.write_bytes(b'e' * (2 * 1024 * 1024))
    digest = hashlib.sha256(pending.read_bytes()).hexdigest()
    workspace.prepare_export_publication(task['id'], pending, pending.stat().st_size, digest)
    fresh = WorkspaceStore(projects)
    monkeypatch.setattr(module, '_sha256', lambda *a: pytest.fail('parent hash'))
    try:
        fresh.recover()
        assert _finish(fresh, project, task['id'])['status'] == 'completed'
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
        assert not fresh.has_pending(task['id'])
    finally:
        fresh.close_recovery()
        projects.close()


def test_large_copy_recovery_uses_worker_hashes(tmp_path, monkeypatch):
    import spatial_engine.portability as portability
    projects, project, dataset, task_id = pending_import(tmp_path)
    workspace = WorkspaceStore(projects)
    # Publish the already verified synthetic snapshot to form a registered copy input.
    workspace.complete_import_publication(task_id)
    workspace.get({'path': project['projectPath']})
    from test_projects import valid_save
    options = portability.validate_save_as({**valid_save(project), 'directory': str(tmp_path / 'copy')}, Path(project['projectPath']))
    copy_id = str(uuid.uuid4())
    payload = portability.prepare_copy(workspace, copy_id, str(uuid.uuid4()), options)
    work_dir = Path(project['projectPath']).parent / 'staging' / 'tasks' / copy_id
    work_dir.mkdir(parents=True)
    result = portability.copy_project(payload, work_dir, progress=lambda *a: None, cancelled=lambda: False)
    portability.prepare_copy_publication(workspace, copy_id, result)
    fresh = WorkspaceStore(projects)
    monkeypatch.setattr(portability, '_hash', lambda *a, **k: pytest.fail('parent copy hash'))
    try:
        fresh.recover()
        recovered = _finish(fresh, project, copy_id)
        assert recovered['status'] == 'completed', recovered['error']
        assert (tmp_path / 'copy' / 'project.spa').is_file()
        assert (tmp_path / 'copy' / dataset['relativePath']).stat().st_size == 2 * 1024 * 1024
    finally:
        fresh.close_recovery()
        projects.close()
