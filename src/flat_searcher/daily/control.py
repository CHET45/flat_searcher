"""Starting, stopping and switching the daily run from outside it: the monitor and the shortcuts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from flat_searcher.daily.progress import mark_stopped, read_progress

STOP_GRACE_SECONDS = 20.0

NOT_RUNNING = "not running"
STOPPED_ITSELF = "stopped"
ENDED = "ended"


@dataclass(frozen=True)
class DailyPaths:
    root: Path

    @property
    def progress(self) -> Path:
        return self.root / "progress.json"

    @property
    def log(self) -> Path:
        return self.root / "daily.log"

    @property
    def lock(self) -> Path:
        return self.root / "run.lock"

    @property
    def stop(self) -> Path:
        return self.root / "stop"

    @property
    def switched_off(self) -> Path:
        return self.root / "switched-off"

    def stop_requested(self) -> bool:
        return self.stop.exists()


class RunLock:
    """One daily run at a time; the operating system drops the lock when the process dies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            _lock(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None


def is_running(paths: DailyPaths) -> bool:
    probe = RunLock(paths.lock)
    if not probe.acquire():
        return True
    probe.release()
    return False


def switched_on(paths: DailyPaths) -> bool:
    return not paths.switched_off.exists()


def set_switched_on(paths: DailyPaths, on: bool) -> None:
    if on:
        paths.switched_off.unlink(missing_ok=True)
    else:
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.switched_off.write_text("", encoding="utf-8")


def stop_run(
    paths: DailyPaths,
    grace_seconds: float = STOP_GRACE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    kill: Callable[[int], None] | None = None,
) -> str:
    """Asks the run to stop at its next checkpoint, and ends the process if it does not."""
    if not is_running(paths):
        return NOT_RUNNING
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.stop.write_text("", encoding="utf-8")
    deadline = time.monotonic() + grace_seconds
    while is_running(paths):
        if time.monotonic() >= deadline:
            break
        sleep(0.5)
    else:
        return STOPPED_ITSELF
    pid = (read_progress(paths.progress) or {}).get("pid")
    if isinstance(pid, int):
        (kill or kill_process_tree)(pid)
    mark_stopped(paths.progress)
    paths.stop.unlink(missing_ok=True)
    return ENDED


def launch(arguments: Sequence[str], cwd: Path | None = None) -> None:
    """Starts `flat_searcher <arguments>` with no console window, outliving the caller."""
    command = [windowless_python(), "-m", "flat_searcher", *arguments]
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            command,
            cwd=cwd,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    else:
        subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def windowless_python() -> str:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if sys.platform == "win32" and candidate.exists() else sys.executable


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _lock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
