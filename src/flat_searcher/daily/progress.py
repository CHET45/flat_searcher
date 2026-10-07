"""The daily run's progress, kept in one JSON file that the monitor window polls."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING = "pending"
RUNNING = "running"
WAITING = "waiting"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
STOPPED = "stopped"

FINISHED = frozenset({DONE, SKIPPED})
ACTIVE = frozenset({RUNNING, WAITING})
LOG_LINES = 300
WRITE_INTERVAL_SECONDS = 0.5
REPLACE_ATTEMPTS = 20


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_progress(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_progress(path: Path, state: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    # The monitor may hold the file open for a moment; Windows then refuses the replace.
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05)


def mark_stopped(path: Path, now: str | None = None) -> None:
    """For a run that was ended from outside and could not record it itself."""
    state = read_progress(path)
    if state is None or state.get("state") not in ACTIVE:
        return
    stamp = now or now_iso()
    for step in state.get("steps") or []:
        if step.get("status") in ACTIVE:
            step.update(status=STOPPED, finished_at=stamp)
    state.update(state=STOPPED, finished_at=stamp, current=None)
    write_progress(path, state)


class ProgressFile:
    def __init__(
        self,
        path: Path,
        log_path: Path | None = None,
        *,
        echo: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], str] = now_iso,
    ) -> None:
        self.path = path
        self.log_path = log_path
        self._echo = echo
        self._clock = clock
        self._now = now
        self._lock = threading.RLock()
        self._last_write = float("-inf")
        self._flush: threading.Timer | None = None
        self.state: dict[str, Any] = {}

    def begin(
        self, day: str, steps: Sequence[tuple[str, str]], pid: int, *, fresh: bool = False
    ) -> None:
        """Starts today's run, keeping the steps an earlier run of the same day finished."""
        earlier = None if fresh else read_progress(self.path)
        if earlier is not None and earlier.get("day") != day:
            earlier = None
        kept = {
            step.get("name"): step
            for step in (earlier or {}).get("steps") or []
            if step.get("status") in FINISHED
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.state = {
                "day": day,
                "pid": pid,
                "state": RUNNING,
                "started_at": self._now(),
                "finished_at": None,
                "current": None,
                "steps": [kept.get(name) or _step(name, title) for name, title in steps],
                "log": list((earlier or {}).get("log") or [])[-LOG_LINES:],
            }
            for step, (_, title) in zip(self.state["steps"], steps):
                step["title"] = title
            self._write(force=True)

    def step(self, name: str) -> dict[str, Any]:
        for step in self.state.get("steps") or []:
            if step["name"] == name:
                return step
        raise KeyError(name)

    def start_step(self, name: str) -> None:
        with self._lock:
            step = self.step(name)
            step.update(_step(name, step["title"]), status=RUNNING, started_at=self._now())
            self.state.update(state=RUNNING, current=name)
            self._write(force=True)
        self.log(f"{step['title']}: started")

    def count(self, name: str, done: int, total: int, unit: str) -> None:
        with self._lock:
            step = self.step(name)
            if step.get("unit") != unit:
                step.update(unit=unit, phase_started_at=self._now(), phase_done=done)
            step.update(done=done, total=total)
            self._write()

    def note(self, name: str, text: str) -> None:
        with self._lock:
            self.step(name)["note"] = text
            self._write(force=True)

    def wait(self, name: str, reason: str) -> None:
        with self._lock:
            self.step(name).update(status=WAITING, note=reason)
            self.state["state"] = WAITING
            self._write(force=True)
        self.log(reason)

    def resume(self, name: str) -> None:
        with self._lock:
            step = self.step(name)
            step.update(status=RUNNING, note="")
            if step.get("unit"):
                step.update(phase_started_at=self._now(), phase_done=step.get("done") or 0)
            self.state["state"] = RUNNING
            self._write(force=True)

    def finish_step(
        self, name: str, status: str, summary: str = "", data: Mapping[str, Any] | None = None
    ) -> None:
        with self._lock:
            step = self.step(name)
            step.update(
                status=status, finished_at=self._now(), summary=summary, note="", data=dict(data or {})
            )
            self._write(force=True)
        self.log(f"{step['title']}: {status}" + (f" - {summary}" if summary else ""))

    def finish(self, state: str) -> None:
        with self._lock:
            self.state.update(state=state, finished_at=self._now(), current=None)
            self._write(force=True)

    def log(self, line: str) -> None:
        stamped = f"{self._now()[11:19]} {line}"
        with self._lock:
            lines = self.state.setdefault("log", [])
            lines.append(stamped)
            del lines[:-LOG_LINES]
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{self.state.get('day', '')} {stamped}\n")
            self._write()
        if self._echo is not None:
            self._echo(stamped)

    def _write(self, force: bool = False) -> None:
        if not self.state:
            return
        moment = self._clock()
        if not force and moment - self._last_write < WRITE_INTERVAL_SECONDS:
            if self._flush is None:
                self._flush = threading.Timer(WRITE_INTERVAL_SECONDS, self._flush_now)
                self._flush.daemon = True
                self._flush.start()
            return
        if self._flush is not None:
            self._flush.cancel()
            self._flush = None
        self.state["updated_at"] = self._now()
        write_progress(self.path, self.state)
        self._last_write = moment

    def _flush_now(self) -> None:
        with self._lock:
            self._flush = None
            try:
                self._write(force=True)
            except OSError:
                pass


def _step(name: str, title: str) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "status": PENDING,
        "started_at": None,
        "finished_at": None,
        "done": None,
        "total": None,
        "unit": "",
        "note": "",
        "summary": "",
        "data": {},
    }
