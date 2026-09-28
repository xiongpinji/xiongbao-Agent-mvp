"""A private Windows Job contains a suspended child before any descendants run.

Structures/API contracts: Microsoft Learn jobapi2, winnt, ToolHelp32 and ResumeThread.
The Job has KILL_ON_JOB_CLOSE; it never accepts unrelated running processes.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as wt


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wt.DWORD),
        ("min_working", ctypes.c_size_t),
        ("max_working", ctypes.c_size_t),
        ("active_limit", wt.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wt.DWORD),
        ("scheduling", wt.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", ctypes.c_ulonglong * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_longlong * 4),
        ("faults", wt.DWORD),
        ("total", wt.DWORD),
        ("active", wt.DWORD),
        ("terminated", wt.DWORD),
    ]


class ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wt.DWORD),
        ("usage", wt.DWORD),
        ("tid", wt.DWORD),
        ("pid", wt.DWORD),
        ("priority", wt.LONG),
        ("delta", wt.LONG),
        ("flags", wt.DWORD),
    ]


class OwnedJob:
    def __init__(self) -> None:
        assert os.name == "nt"
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wt.LPCWSTR], wt.HANDLE),
            "SetInformationJobObject": (
                [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD],
                wt.BOOL,
            ),
            "AssignProcessToJobObject": ([wt.HANDLE, wt.HANDLE], wt.BOOL),
            "QueryInformationJobObject": (
                [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p],
                wt.BOOL,
            ),
            "TerminateJobObject": ([wt.HANDLE, wt.UINT], wt.BOOL),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
            "CreateToolhelp32Snapshot": ([wt.DWORD, wt.DWORD], wt.HANDLE),
            "Thread32First": ([wt.HANDLE, ctypes.POINTER(ThreadEntry)], wt.BOOL),
            "Thread32Next": ([wt.HANDLE, ctypes.POINTER(ThreadEntry)], wt.BOOL),
            "OpenThread": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "ResumeThread": ([wt.HANDLE], wt.DWORD),
        }
        for name, (arguments, returns) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = arguments, returns
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self.check(
                self.k.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(success: object) -> None:
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def attach_and_resume(self, child: object) -> None:
        # Caller creates exactly this child with CREATE_SUSPENDED (0x4).
        self.check(self.k.AssignProcessToJobObject(self.handle, int(child._handle)))
        snapshot = self.k.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        thread_ids = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            available = self.k.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.pid == child.pid:
                    thread_ids.append(entry.tid)
                entry.size = ctypes.sizeof(entry)
                available = self.k.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            self.check(self.k.CloseHandle(snapshot))
        assert len(thread_ids) == 1, "Fresh suspended child must have one primary thread"
        thread = self.k.OpenThread(0x2, False, thread_ids[0])  # THREAD_SUSPEND_RESUME
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            previous = self.k.ResumeThread(thread)
            if previous == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            assert previous == 1
        finally:
            self.check(self.k.CloseHandle(thread))

    def counts(self) -> dict:
        counters = Accounting()
        self.check(
            self.k.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(counters), ctypes.sizeof(counters), None
            )
        )
        return {
            "total": counters.total,
            "active": counters.active,
            "terminated": counters.terminated,
        }

    def terminate(self) -> None:
        self.check(self.k.TerminateJobObject(self.handle, 2))

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            self.check(self.k.CloseHandle(handle))
