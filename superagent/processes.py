"""Ownership and bounded shutdown for agent processes and background tasks."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import signal
import subprocess


class _WindowsJob:
    """Kernel-owned process tree; descendants die even after the wrapper exits."""
    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process_handle = None
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            process_handle = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process_handle or not kernel.AssignProcessToJobObject(self.handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process_handle:
                kernel.CloseHandle(process_handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ProcessSupervisor:
    def __init__(self, grace_seconds: float = 1.0):
        self.grace_seconds = grace_seconds
        self._processes = {}
        self._background = set()
        self._closed = False

    def track(self, coroutine):
        if self._closed:
            coroutine.close()
            raise RuntimeError('Process supervisor is closed.')
        task = asyncio.create_task(coroutine)
        self._background.add(task)

        def finished(completed):
            self._background.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Retrieve exceptions from detached tasks.
        task.add_done_callback(finished)
        return task

    @asynccontextmanager
    async def process(self, *command, **kwargs):
        if self._closed:
            raise RuntimeError('Process supervisor is closed.')
        if os.name != 'nt':
            kwargs['start_new_session'] = True
        async def launch():
            child = await asyncio.create_subprocess_exec(*command, **kwargs)
            try:
                child_job = _WindowsJob(child.pid) if os.name == 'nt' else None
            except BaseException:
                await self._stop(child, None)
                raise
            return child, child_job

        launching = asyncio.create_task(launch())
        proc = None
        job = None
        try:
            # Cancellation during launch still has to acquire and dispose the child.
            try:
                proc, job = await asyncio.shield(launching)
            except asyncio.CancelledError:
                proc, job = await launching
                raise
            self._processes[proc] = job
            yield proc
        finally:
            if proc is not None:
                self._processes.pop(proc, None)
                await asyncio.shield(self._stop(proc, job))

    async def _stop(self, proc, job):
        if os.name == 'nt' and proc.returncode is None:
            # A wrapper can spawn children before its Job Object is assigned.
            # Kill that existing tree while the root is still available to taskkill.
            try:
                await asyncio.to_thread(
                    subprocess.run, ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if job is not None:
            job.close()
        elif os.name != 'nt':
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif proc.returncode is None:
            proc.terminate()  # Also used if Job Object assignment failed.
        try:
            await asyncio.wait_for(proc.communicate(), self.grace_seconds)
        except Exception:
            # A broken output stream must not skip process reaping.
            pass
        finally:
            if os.name != 'nt':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        # Drain pipes and reap the root after escalation, with a bounded wait.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.communicate(), 5.0)
        except Exception:
            pass
        await asyncio.wait_for(proc.wait(), 5.0)

    async def aclose(self):
        self._closed = True
        tasks = [task for task in self._background if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Normally context managers have already disposed every process.
        await asyncio.gather(*(self._stop(proc, job) for proc, job in list(self._processes.items())))
        self._processes.clear()
