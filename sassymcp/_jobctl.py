"""Orphan-proof child-process control for the SassyMCP supervisor.

The headline guarantee: when the supervisor dies for ANY reason — clean
exit, unhandled crash, or a hard `kill -9` / `TerminateProcess` where no
signal handler or atexit hook ever runs — every child it spawned dies too.
No orphaned bridge holding a wedged SQLite/WAL lock, no zombie tunnel.

Windows
-------
A Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. The supervisor
holds the only handle to the job and assigns each child to it. When the
supervisor process exits, the kernel closes that handle and terminates
every process in the job — the one mechanism that survives a hard kill.

Implemented with ``ctypes`` against ``kernel32`` on purpose: ``pywin32``
is only a transitive dependency (via ``pywinauto``) and is **not** listed
in ``sassymcp.spec`` hiddenimports, so ``import win32job`` is not
guaranteed to work inside the frozen ``sassymcp.exe``. ctypes needs no
packaged dependency and no spec change, so the guarantee holds in the
shipped binary.

POSIX
-----
Each child is started in a new session (``start_new_session=True``) so it
leads its own process group; teardown signals the whole group via
``killpg`` (SIGTERM then SIGKILL). There is no portable Linux+macOS
kernel primitive for kill-on-parent-death, so POSIX relies on the
supervisor's teardown path. On Linux we additionally arm
``PR_SET_PDEATHSIG`` so a hard-killed parent still takes its direct
children down.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


# ── POSIX: arm parent-death signal on Linux ───────────────────────────
def _posix_preexec() -> None:  # pragma: no cover - exercised only on POSIX
    """Run in the child between fork and exec (POSIX only).

    Start a new session (group leader) and, on Linux, ask the kernel to
    SIGKILL us if our parent dies. setsid is also requested via
    start_new_session=True; calling it here too is harmless and keeps the
    PDEATHSIG arming in one place.
    """
    try:
        os.setsid()
    except OSError:
        pass
    if IS_LINUX:
        try:
            # PR_SET_PDEATHSIG = 1
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)
        except Exception:
            pass


# ── Windows Job Object via ctypes/kernel32 ────────────────────────────
if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    ULONG_PTR = ctypes.c_size_t

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ULONG_PTR),
            ("MaximumWorkingSetSize", ULONG_PTR),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ULONG_PTR),
            ("JobMemoryLimit", ULONG_PTR),
            ("PeakProcessMemoryUsed", ULONG_PTR),
            ("PeakJobMemoryUsed", ULONG_PTR),
        ]

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _win_create_job() -> int:
        h = _kernel32.CreateJobObjectW(None, None)
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            h, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            err = ctypes.get_last_error()
            _kernel32.CloseHandle(h)
            raise ctypes.WinError(err)
        return h

    def _win_assign(job_handle: int, pid: int) -> None:
        hproc = _kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid)
        if not hproc:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _kernel32.AssignProcessToJobObject(job_handle, hproc):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(hproc)


class ProcessJob:
    """A kill-on-close container for orphan-proof child processes.

    Create one per supervisor. Pass ``spawn_kwargs()`` into every managed
    ``subprocess.Popen``, then call ``assign(proc)`` immediately after the
    child starts. Closing the job (or the supervisor dying) tears the tree
    down.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self._adopted = False  # True once the current process joined the job
        self.kind = "none"
        if IS_WINDOWS:
            self._handle = _win_create_job()
            self.kind = "job_object"
        else:
            self.kind = "process_group"

    @property
    def active(self) -> bool:
        return self.kind != "none"

    def spawn_kwargs(self) -> dict:
        """subprocess.Popen kwargs that make a child assignable/reapable.

        Windows: CREATE_NO_WINDOW suppresses a console window. We must NOT
        set CREATE_BREAKAWAY_FROM_JOB — the child has to stay inside our
        job. POSIX: start_new_session + a preexec that arms PDEATHSIG so a
        hard-killed supervisor still takes children down on Linux.
        """
        if IS_WINDOWS:
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        return {"start_new_session": True, "preexec_fn": _posix_preexec}

    def assign(self, proc: subprocess.Popen) -> bool:
        """Bring a just-spawned child under the job (Windows). No-op on POSIX.

        Returns True if the child is now governed by the kill-on-close
        guarantee. A False return (race: child already exited, or assign
        denied) is logged by the caller — the child is still reapable via
        terminate_tree, just not auto-killed on a hard parent death.
        """
        if not IS_WINDOWS or self._handle is None:
            return self.kind == "process_group"
        if proc.poll() is not None:
            return False
        try:
            _win_assign(self._handle, proc.pid)
            return True
        except OSError:
            return False

    def adopt_current_process(self) -> bool:
        """Assign THIS process to the job so all descendants auto-inherit it.

        This is the only reliable way to govern PyInstaller onefile children:
        ``Popen([exe])`` returns the bootloader PID, which then spawns the
        real worker as its own child. Assigning the bootloader after the fact
        misses that worker. But a process created by a job member (with no
        CREATE_BREAKAWAY_FROM_JOB) joins the job *at creation*, so adopting
        ourselves makes every child — bootloader, onefile worker, grandchild —
        a job member with no race.

        MUST be called only by the real supervisor process, never when the
        Supervisor runs embedded in another process (e.g. a test thread):
        joining a KILL_ON_JOB_CLOSE job would tie that host process's life to
        the job handle. Returns True on success.
        """
        if not IS_WINDOWS or self._handle is None:
            self._adopted = self.kind == "process_group"
            return self._adopted
        try:
            _win_assign(self._handle, os.getpid())
            self._adopted = True
            return True
        except OSError:
            return False

    def close(self) -> None:
        """Release the job handle.

        If we adopted the current process, do NOT CloseHandle while alive —
        the last-handle close triggers KILL_ON_JOB_CLOSE and would terminate
        us mid-shutdown. The OS closes the handle when this process exits,
        which is the moment we *want* the kill-on-close to sweep any straggler
        (e.g. a onefile worker that outlived its bootloader). When we did not
        adopt ourselves (dev/test paths), closing now is safe and frees the
        empty job."""
        if IS_WINDOWS and self._handle is not None and not self._adopted:
            try:
                _kernel32.CloseHandle(self._handle)
            finally:
                self._handle = None


def terminate_tree(proc: subprocess.Popen, grace: float = 5.0) -> int | None:
    """Graceful terminate → wait(grace) → hard kill of the WHOLE child tree.

    Tree-aware on purpose: a PyInstaller onefile child is a bootloader plus a
    worker child, and a shell child spawns its own grandchildren — terminating
    only ``proc`` would orphan those. We enumerate descendants with psutil and
    signal them all; on POSIX we also ``killpg`` the child's process group to
    catch anything that reparented. Returns the child's exit code (or None)."""
    if proc.poll() is not None:
        return proc.returncode

    try:
        import psutil
        root = psutil.Process(proc.pid)
        tree = root.children(recursive=True)
        tree.append(root)
    except Exception:
        tree = []

    def _killpg(sig: int) -> None:
        if IS_WINDOWS:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, OSError):
            pass

    # graceful
    _killpg(signal.SIGTERM)
    for p in tree:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        import psutil
        _, alive = psutil.wait_procs(tree, timeout=grace) if tree else ([], [])
    except Exception:
        alive = tree
    # force the stragglers
    if alive:
        _killpg(signal.SIGKILL)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
    try:
        proc.wait(timeout=grace)
    except Exception:
        pass
    return proc.poll()
