"""Bounded, serial recovery verification while project RPC remains responsive."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from contextvars import ContextVar
from pathlib import Path

from .capacity import TASK_MEMORY_BYTES, TASK_SECONDS
from .errors import DomainError
from .publication import ArtifactLease, VerifiedArtifact, verify_artifact
from .resource_guard import ResourceGuard
from .validation import require_exact_keys, require_path

INLINE_RECOVERY_BYTES = 1024 * 1024
MAX_RECOVERY_ITEMS = 100
MAX_RECOVERY_FILES = 65
INLINE_HASH_REMAINING = ContextVar('inline_recovery_hash_remaining', default=None)


def recovery_hash_read(stream):
    remaining = INLINE_HASH_REMAINING.get()
    if remaining is None:
        return stream.read(1024 * 1024)
    if remaining == 0:
        if os.fstat(stream.fileno()).st_size > stream.tell():
            raise DomainError('Inline recovery exceeds its read budget', kind='recovery_limit')
        return b''
    chunk = stream.read(min(remaining, 1024 * 1024))
    INLINE_HASH_REMAINING.set(remaining - len(chunk))
    return chunk


def bind_proofs(result, leases):
    if not isinstance(result, dict) or set(result) != {'proofs'} or not isinstance(result['proofs'], list):
        raise DomainError('Invalid recovery proofs', kind='invalid_worker_result')
    proofs = result['proofs']
    if len(proofs) != len(leases) or any(not isinstance(item, dict) for item in proofs):
        raise DomainError('Incomplete recovery proofs', kind='invalid_worker_result')
    by_path = {item.get('path'): item for item in proofs}
    if set(by_path) != set(leases):
        raise DomainError('Recovery proof paths differ', kind='invalid_worker_result')
    return {path: VerifiedArtifact(by_path[path], lease) for path, lease in leases.items()}


def verify_files(payload, work_dir, *, progress, cancelled):
    require_exact_keys(payload, {'paths'})
    paths = payload['paths']
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_RECOVERY_FILES:
        raise DomainError('Recovery file list exceeds its budget', kind='invalid_worker_request')
    proofs = []
    for index, value in enumerate(paths):
        path = require_path(value, 'recovery file')
        proofs.append(verify_artifact({'path': str(path)}, work_dir, progress=lambda *args: None, cancelled=cancelled))
        progress('verifying_recovery', index + 1, len(paths))
    return {'proofs': proofs}


def copy_paths(workspace, task_id, *, relocate=False):
    from . import portability
    from .tasks import _bounded_json, MAX_RESULT_BYTES
    row, temporary, destination = portability._journal(workspace, task_id)
    if workspace.task(task_id)['status'] in {'cancelled', 'failed'}:
        raise DomainError('Cancelled or failed copies cannot be recovered into published projects', kind='copy_cancelled')
    if row['manifest_json'] is None:
        result_path = workspace._project_root() / 'staging' / 'tasks' / task_id / 'result.json'
        result = _bounded_json(result_path, MAX_RESULT_BYTES)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise DomainError('Copy recovery has no verified manifest', kind='copy_incomplete')
        portability.prepare_copy_publication(workspace, task_id, result['result'])
        row, temporary, destination = portability._journal(workspace, task_id)
    manifest = json.loads(row['manifest_json'])
    portability._validate_manifest(manifest, json.loads(row['plan_json']))
    if destination.exists() and temporary.exists():
        raise DomainError('Both copy destinations exist', kind='publication_conflict')
    root = destination if destination.exists() else temporary
    portability._validate_copy(root, row, manifest, recovering=False)
    if relocate and root == temporary:
        # Windows cannot rename a directory containing leased children. The ownership
        # marker keeps this destination unopenable until all leased hashes pass.
        portability._rename_directory_no_replace(temporary, destination)
        root = destination
    return [root / relative for relative in manifest]


def _journal_paths(workspace, kind, row):
    root = workspace._project_root().resolve()
    if kind == 'copy':
        return copy_paths(workspace, row['task_id'])
    if kind == 'import':
        _, _, staged, final = workspace.validate_import_journal(row)
        return [final if final.exists() else staged]
    destination = Path(row['destination_path']).resolve()
    temporary = Path(row['temporary_path']).resolve()
    task = workspace.task(row['task_id'])
    if (task['kind'] != 'export' or task['destination'] != str(destination)
            or temporary != destination.with_name(f'.{destination.name}.{row["task_id"]}.pending')):
        raise DomainError('Export recovery journal is invalid', kind='invalid_project')
    return [destination if destination.exists() else temporary]


class RecoveryVerifier:
    def __init__(self, workspace, paths, *, timeout_seconds=TASK_SECONDS, memory_bytes=TASK_MEMORY_BYTES):
        from .tasks import _atomic_json, _default_command
        self.leases = {}
        self.process = None
        self.guard = None
        self.stderr = None
        self.directory = workspace._project_root() / 'staging' / 'recovery' / str(uuid.uuid4())
        try:
            for path in paths:
                self.leases[str(path.resolve())] = ArtifactLease(path)
            self.directory.mkdir(parents=True, exist_ok=False)
            request = self.directory / 'request.json'
            _atomic_json(request, {'protocolVersion': 5, 'taskId': self.directory.name, 'kind': 'verify_recovery',
                'payload': {'paths': list(self.leases)}, 'workDir': str(self.directory.resolve()), 'publishPath': None})
            self.stderr = (self.directory / 'stderr.log').open('ab')
            self.process = subprocess.Popen(_default_command(request), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self.stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
            self.guard = ResourceGuard(self.process, timeout_seconds=timeout_seconds, memory_bytes=memory_bytes)
        except Exception:
            self.close()
            raise

    def result(self):
        from .tasks import _bounded_json, MAX_RESULT_BYTES
        if self.process.poll() is None:
            return None
        if self.guard.reason:
            raise DomainError('Recovery worker exceeded its budget', kind=self.guard.reason, detail=self.guard.reason)
        response = _bounded_json(self.directory / 'result.json', MAX_RESULT_BYTES)
        if self.process.returncode != 0 or not isinstance(response, dict) or response.get('ok') is not True:
            error = response.get('error', {}) if isinstance(response, dict) else {}
            raise DomainError(str(error.get('message', 'Recovery verification failed')),
                              kind=str(error.get('kind', 'invalid_worker_result')))
        return bind_proofs(response.get('result'), self.leases)

    def close(self):
        if self.process is not None and self.process.poll() is None:
            try:
                (self.directory / 'cancel.flag').touch(exist_ok=True)
                self.process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.guard is not None:
            self.guard.stop()
            self.guard.terminate()
        elif self.process is not None and self.process.poll() is None:
            self.process.kill()
        if self.process is not None:
            self.process.wait(timeout=2)
        for lease in self.leases.values():
            lease.close()
        if self.stderr is not None:
            self.stderr.close()
        if self.directory.is_dir():
            shutil.rmtree(self.directory, ignore_errors=True)


class RecoveryCoordinator:
    def __init__(self, workspace):
        self.workspace = workspace
        self.queue = []
        self.current = None
        self.verifier = None
        self.total_bytes = 0
        self.errors = False
        with workspace._connect(workspace.projects._session.path) as connection:
            rows = [(kind, dict(row)) for kind, table in (('copy', 'pending_copies'), ('import', 'pending_publications'), ('export', 'pending_exports'))
                    for row in connection.execute(f'SELECT * FROM {table} LIMIT {MAX_RECOVERY_ITEMS + 1}')]
        if len(rows) > MAX_RECOVERY_ITEMS:
            raise DomainError('Too many pending recovery jobs', kind='invalid_project')
        for kind, row in rows:
            item = {'kind': kind, 'task_id': row['task_id'], 'paths': [], 'error': None}
            try:
                item['paths'] = _journal_paths(workspace, kind, row)
                if len(item['paths']) > MAX_RECOVERY_FILES:
                    raise DomainError('Recovery file list exceeds limit', kind='invalid_project')
                self.total_bytes += sum(path.stat().st_size for path in item['paths'])
            except (OSError, DomainError, ValueError, KeyError, TypeError) as exc:
                item['error'] = str(exc)
                self.errors = True
            self.queue.append(item)

    @property
    def active(self):
        return self.current is not None or bool(self.queue)

    def _failed(self, item, error):
        previous = self.workspace.task(item['task_id'])['status']
        status = previous if previous in {'cancelled', 'failed'} else 'interrupted'
        message = str(error)
        if isinstance(error, DomainError) and error.detail:
            message += ': ' + error.detail
        self.workspace.update_task(item['task_id'], status=status, stage='publication_pending', error=message[:8192])

    def tick(self):
        if self.verifier is not None:
            if self.verifier.process.poll() is None:
                return
            try:
                proofs = self.verifier.result()
                if proofs is None:
                    return
                self._publish(self.current, proofs)
            except Exception as exc:
                self._failed(self.current, exc)
            finally:
                if self.verifier is not None:
                    self.verifier.close()
                    self.verifier = None
                    self.current = None
        if self.verifier is not None or not self.queue:
            return
        item = self.queue.pop(0)
        if item['error']:
            self._failed(item, item['error'])
            return
        self.current = item
        self.workspace.update_task(item['task_id'], status='running', stage='verifying_recovery', completed=None, total=None, error=None)
        try:
            if item['kind'] == 'copy':
                item['paths'] = copy_paths(self.workspace, item['task_id'], relocate=True)
            self.verifier = RecoveryVerifier(self.workspace, item['paths'])
        except Exception as exc:
            self._failed(item, exc)
            self.current = None

    def _publish(self, item, proofs):
        task_id = item['task_id']
        if item['kind'] == 'copy':
            from .portability import complete_copy_publication
            complete_copy_publication(self.workspace, task_id, recovering=True, proofs=proofs)
        else:
            proof = proofs[str(item['paths'][0].resolve())]
            operation = self.workspace.complete_import_publication if item['kind'] == 'import' else self.workspace.complete_export_publication
            operation(task_id, recovering=True, proof=proof)
        self.verifier.close()
        if item['kind'] != 'export' or self.workspace._remove_owned_export_pending(task_id, self.workspace.task(task_id)['destination']):
            self.workspace._cleanup_task_directory(task_id)

    def cancel(self, task_id):
        if self.current is not None and self.current['task_id'] == task_id:
            self.verifier.close()
            self._failed(self.current, 'Recovery cancelled; publication journal retained for the next open')
            self.current = self.verifier = None
            return self.workspace.task(task_id)
        for item in self.queue:
            if item['task_id'] == task_id:
                self.queue.remove(item)
                self._failed(item, 'Recovery cancelled; publication journal retained for the next open')
                return self.workspace.task(task_id)
        return None

    def close(self):
        if self.current is not None:
            self.cancel(self.current['task_id'])
        for item in self.queue:
            self._failed(item, 'Recovery interrupted; publication journal retained')
        self.queue.clear()
