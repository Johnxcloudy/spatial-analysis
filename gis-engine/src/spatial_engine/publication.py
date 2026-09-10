"""Hash publication artifacts in a killable worker; commit only unchanged files.

Proofs are private worker messages, never accepted in public RPC parameters.
Recovery re-hashes from the durable journal because proofs are not persisted.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import DomainError
from .validation import require_exact_keys, require_path

MAX_ARTIFACT_BYTES = 32 * 1024**3


class ArtifactLease:
    """Keep Windows writers out while a child verifies and the parent publishes.

    Delete sharing permits the controlled no-replace rename. Path identities are
    checked again before publication; a replacement path cannot reuse the lease.
    On POSIX ctime in the signature detects content/mtime changes.
    """
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.closed = False
        self._handle = None
        self._stream = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            # GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_DELETE, OPEN_EXISTING.
            handle = kernel.CreateFileW(str(self.path), 0x80000000, 0x5, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle, self._kernel = handle, kernel
        else:
            self._stream = self.path.open('rb')
        try:
            self.identity = _identity(self.path)
            self.require_identity(self.path)
        except Exception:
            self.close()
            raise

    def current_file_id(self) -> tuple[int, int]:
        if self.closed:
            raise DomainError('Publication lease is closed', kind='invalid_worker_result')
        if os.name != 'nt':
            info = os.fstat(self._stream.fileno())
            return info.st_dev, info.st_ino
        import ctypes
        from ctypes import wintypes as w
        class FileIdInfo(ctypes.Structure):
            _fields_ = [('volume', ctypes.c_ulonglong), ('file_id', ctypes.c_ubyte * 16)]
        self._kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
        self._kernel.GetFileInformationByHandleEx.restype = w.BOOL
        full_id = FileIdInfo()
        if self._kernel.GetFileInformationByHandleEx(self._handle, 18, ctypes.byref(full_id), ctypes.sizeof(full_id)):
            return full_id.volume, int.from_bytes(bytes(full_id.file_id), 'little')
        # exFAT may expose only the legacy 64-bit file ID / 32-bit volume serial.
        class FileInfo(ctypes.Structure):
            _fields_ = [('attributes', w.DWORD), ('creation', w.FILETIME), ('access', w.FILETIME),
                        ('write', w.FILETIME), ('volume', w.DWORD), ('size_high', w.DWORD),
                        ('size_low', w.DWORD), ('links', w.DWORD), ('id_high', w.DWORD), ('id_low', w.DWORD)]
        self._kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
        self._kernel.GetFileInformationByHandle.restype = w.BOOL
        info = FileInfo()
        if not self._kernel.GetFileInformationByHandle(self._handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.volume, (info.id_high << 32) | info.id_low

    def require_identity(self, path: Path) -> None:
        info = path.stat()
        if self.current_file_id() != (info.st_dev, info.st_ino):
            raise DomainError('Publication artifact identity changed', kind='invalid_worker_result')

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
        if self._stream is not None:
            self._stream.close()


@dataclass(frozen=True)
class VerifiedArtifact:
    data: dict
    lease: ArtifactLease


def _identity(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def verify_artifact(payload: dict, work_dir: Path, *, progress, cancelled) -> dict:
    require_exact_keys(payload, {'path'})
    path = require_path(payload['path'], 'artifact path')
    before = _identity(path)
    if not path.is_file() or before[2] > MAX_ARTIFACT_BYTES:
        raise DomainError('Publication artifact exceeds its budget', kind='publication_limit')
    digest = hashlib.sha256()
    size = 0
    progress('verifying_publication', 0, before[2])
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if cancelled():
                raise DomainError('Task was cancelled', kind='task_cancelled')
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise DomainError('Publication artifact exceeds its budget', kind='publication_limit')
            digest.update(chunk)
            progress('verifying_publication', size, max(size, before[2]))
    if cancelled():
        raise DomainError('Task was cancelled', kind='task_cancelled')
    if _identity(path) != before or size != before[2]:
        raise DomainError('Publication artifact changed during verification', kind='invalid_worker_result')
    return {'path': str(path), 'identity': before, 'size': size, 'sha256': digest.hexdigest()}


def check_proof(path: Path, sealed: VerifiedArtifact) -> tuple[int, str]:
    if (not isinstance(sealed, VerifiedArtifact) or not isinstance(sealed.lease, ArtifactLease) or sealed.lease.closed
            or sealed.lease.path != path.resolve()):
        raise DomainError('Publication verification lease is missing or closed', kind='invalid_worker_result')
    proof = sealed.data
    sealed.lease.require_identity(path)
    if (not isinstance(proof, dict) or set(proof) != {'path', 'identity', 'size', 'sha256'}
            or proof['path'] != str(path.resolve())
            or not isinstance(proof['identity'], list) or len(proof['identity']) != 5
            or any(isinstance(v, bool) or not isinstance(v, int) for v in proof['identity'])
            or isinstance(proof['size'], bool) or not isinstance(proof['size'], int)
            or not 0 <= proof['size'] <= MAX_ARTIFACT_BYTES
            or proof['size'] != proof['identity'][2]
            or not isinstance(proof['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', proof['sha256'])):
        raise DomainError('Publication verification is invalid', kind='invalid_worker_result')
    if (not path.is_file() or _identity(path) != proof['identity']
            or proof['identity'] != sealed.lease.identity):
        raise DomainError('Publication artifact changed after verification', kind='invalid_worker_result')
    return proof['size'], proof['sha256']
