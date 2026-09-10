"""Autonomous process-tree budget enforcement, independent of RPC polling."""
from __future__ import annotations

import threading
import time
import os
import ctypes

import psutil

from .capacity import TASK_MEMORY_BYTES, TASK_SECONDS


class ProcessTreeJob:
    """A worker-lifetime Windows Job; nested under the desktop Job when present."""
    def __init__(self, pid):
        self._handle = None
        self._lock = threading.Lock()
        if os.name != "nt":
            return
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("user", ctypes.c_int64), ("job_user", ctypes.c_int64), ("flags", w.DWORD),
                ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("processes", w.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes, api.CreateJobObjectW.restype = [ctypes.c_void_p, w.LPCWSTR], w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.OpenProcess.argtypes, api.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        api.CloseHandle.argtypes = [w.HANDLE]
        self._api = api
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not api.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            process_handle = api.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process_handle:
                if not psutil.pid_exists(pid):
                    self.close()
                    return
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not api.AssignProcessToJobObject(handle, process_handle):
                    if psutil.pid_exists(pid):
                        raise ctypes.WinError(ctypes.get_last_error())
            finally:
                api.CloseHandle(process_handle)
        except Exception:
            self.close()
            raise

    def close(self):
        with self._lock:
            if self._handle is not None:
                self._api.CloseHandle(self._handle)
                self._handle = None


class ResourceGuard:
    def __init__(self, process, *, timeout_seconds=TASK_SECONDS, memory_bytes=TASK_MEMORY_BYTES,
                 prefix="task", interval=0.05):
        self.process = process
        self.reason = None
        self.peak_memory_bytes = 0
        self._timeout = timeout_seconds
        self._memory = memory_bytes
        self._prefix = prefix
        self._interval = interval
        self._started = time.monotonic()
        self._stopped = threading.Event()
        self._job = ProcessTreeJob(process.pid) if prefix == "task" else None
        try:
            self._root = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            self._root = None
        self._known = {}
        self._tree_lock = threading.Lock()
        self._thread = threading.Thread(target=self._monitor, daemon=True, name="gis-resource-guard")
        self._thread.start()

    def _tree(self):
        with self._tree_lock:
            if self._root is not None:
                try:
                    for item in [self._root, *self._root.children(recursive=True)]:
                        self._known[item.pid] = item
                except psutil.NoSuchProcess:
                    pass
            return [item for item in self._known.values() if item.is_running()]

    def _monitor(self):
        while not self._stopped.wait(self._interval):
            if self.process.poll() is not None:
                self.terminate()
                return
            try:
                samples = [item.memory_info() for item in self._tree()]
                # Windows private committed bytes also account for paged-out allocations.
                memory = sum(getattr(sample, "private", sample.rss) for sample in samples)
                self.peak_memory_bytes = max(self.peak_memory_bytes, memory)
                if memory > self._memory:
                    self.reason = f"{self._prefix}_memory_limit"
                elif time.monotonic() - self._started > self._timeout:
                    self.reason = f"{self._prefix}_timeout"
                if self.reason:
                    self.terminate()
                    return
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                self.reason = f"{self._prefix}_monitor_failed"
                self.terminate()
                return

    def terminate(self):
        if self._job is not None:
            self._job.close()
        members = self._tree()
        for member in reversed(members):
            try:
                member.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(members, timeout=1.0)

    def stop(self):
        self._stopped.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.5)
        if self.process.poll() is not None:
            self.terminate()
