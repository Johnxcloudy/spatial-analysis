"""One reusable read-only worker, with no unbounded operation queue."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading

from .capacity import QUERY_MEMORY_BYTES, QUERY_SECONDS
from .errors import DomainError
from .query_worker import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, OPERATIONS
from .resource_guard import ResourceGuard, ProcessTreeJob


class QueryRunner:
    def __init__(self, *, command_factory=None, timeout_seconds=QUERY_SECONDS,
                 memory_bytes=QUERY_MEMORY_BYTES, warmup_seconds=30.0):
        self._command = command_factory or self._default_command
        self._timeout = timeout_seconds
        self._memory = memory_bytes
        self._warmup = warmup_seconds
        self._lock = threading.Lock()
        self._process = None
        self._ready = threading.Event()
        self._closed = False
        self._frames = queue.Queue(maxsize=1)
        self._guard = None
        self._job = None
        self._start()

    @staticmethod
    def _default_command():
        if getattr(sys, "frozen", False):
            return [sys.executable, "--query-worker"]
        return [sys.executable, "-m", "spatial_engine", "--query-worker"]

    def _start(self):
        self._ready.clear()
        self._frames = queue.Queue(maxsize=1)
        self._process = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        self._job = ProcessTreeJob(self._process.pid)
        self._guard = ResourceGuard(self._process, timeout_seconds=self._warmup,
                                    memory_bytes=self._memory, prefix="query")
        threading.Thread(target=self._reader, args=(self._process, self._frames, self._guard), daemon=True).start()

    def _reader(self, process, frames, warmup_guard):
        try:
            first = process.stdout.readline(MAX_RESPONSE_BYTES + 1)
            if json.loads(first) != {"ready": True, "protocolVersion": 1}:
                raise ValueError("Invalid query readiness frame")
            warmup_guard.stop()
            if process is self._process and not self._closed:
                self._ready.set()
            while raw := process.stdout.readline(MAX_RESPONSE_BYTES + 1):
                if len(raw) > MAX_RESPONSE_BYTES or not raw.endswith(b"\n"):
                    raise ValueError("Query response frame exceeds limit")
                frames.put_nowait(json.loads(raw))
        except Exception:
            warmup_guard.terminate()
            try:
                frames.put_nowait(None)
            except queue.Full:
                pass
        finally:
            try:
                frames.put_nowait(None)
            except queue.Full:
                pass
            if process is self._process:
                self._ready.clear()

    def execute(self, operation, payload):
        if operation not in OPERATIONS:
            raise DomainError("Unsupported query operation", kind="invalid_query")
        if not self._lock.acquire(blocking=False):
            raise DomainError("Another query is running", kind="query_busy")
        try:
            if self._closed:
                raise DomainError("Query runner is closed", kind="query_unready")
            if not self._ready.is_set():
                if self._process.poll() is not None:
                    self._dispose_process()
                    self._start()
                raise DomainError("Query engine is warming up; retry shortly", kind="query_unready")
            return self._execute_ready(operation, payload)
        finally:
            self._lock.release()

    def _execute_ready(self, operation, payload):
        raw = json.dumps({"operation": operation, "payload": payload}, ensure_ascii=True,
                         allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"
        if len(raw) > MAX_REQUEST_BYTES:
            raise DomainError("Query request exceeds limit", kind="query_limit")
        guard = ResourceGuard(self._process, timeout_seconds=self._timeout, memory_bytes=self._memory, prefix="query")
        self._guard = guard
        try:
            self._process.stdin.write(raw)
            self._process.stdin.flush()
            response = self._frames.get(timeout=self._timeout + 0.15)
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise ValueError("Invalid query response")
            if response["ok"]:
                return response["result"]
            error = response["error"]
            raise DomainError(error["message"], kind=error["kind"], detail=error.get("detail"))
        except (queue.Empty, OSError, ValueError, KeyError) as exc:
            kind = guard.reason or ("query_timeout" if isinstance(exc, queue.Empty) else "query_failed")
            self._dispose_process()
            self._start()
            raise DomainError("Query stopped; project and analysis task are preserved", kind=kind) from exc
        finally:
            guard.stop()

    def _dispose_process(self):
        self._ready.clear()
        if self._job is not None:
            self._job.close()
            self._job = None
        if self._guard is not None:
            self._guard.stop()
            self._guard.terminate()
        if self._process is not None:
            self._process.wait(timeout=2)
            self._process.stdin.close()
            self._process.stdout.close()

    def close(self):
        with self._lock:
            self._closed = True
            self._dispose_process()
