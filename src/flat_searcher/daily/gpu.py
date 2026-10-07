"""Whether the graphics card is free for the local model, and getting Ollama up when it is not."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

OLLAMA_PORT = "11434"
# Measured on an 8 GB card: with more than this held by other programs the model
# no longer fits on the GPU and judging slows down several times.
OTHERS_MAX_MIB = 2500
OLLAMA_IMAGES = frozenset({"ollama.exe", "ollama app.exe", "llama-server.exe", "ollama"})
# Listing every command line takes seconds on Windows; a minute-old answer is good enough.
SCAN_SECONDS = 60.0

Runner = Callable[[Sequence[str]], str]


def run_quietly(command: Sequence[str]) -> str:
    """The command's output, or "" when it is missing or fails."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="oem" if sys.platform == "win32" else "utf-8",
            errors="replace",
            timeout=60,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


class GpuGuard:
    def __init__(
        self,
        loaded_vram_mib: Callable[[], int],
        busy_patterns: Sequence[str] = (),
        others_max_mib: int = OTHERS_MAX_MIB,
        run: Runner = run_quietly,
        own_pid: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loaded_vram_mib = loaded_vram_mib
        self._patterns = tuple(
            pattern.strip().casefold() for pattern in busy_patterns if pattern.strip()
        )
        self._others_max_mib = others_max_mib
        self._run = run
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._clock = clock
        self._scanned: tuple[float, str | None] | None = None

    def busy(self) -> str | None:
        """Why the model should not run now, or None when the card is free."""
        named = self._named_program()
        if named is not None:
            return f"{named} is running"
        clients = self.other_ollama_clients()
        if clients:
            return f"another program is using Ollama (pid {', '.join(map(str, clients))})"
        used = self.used_vram_mib()
        if used is not None:
            others = used - self._loaded_vram_mib()
            if others > self._others_max_mib:
                return f"other programs hold {others / 1024:.1f} GB of video memory"
        return None

    def other_ollama_clients(self) -> list[int]:
        ollama = {
            pid for pid, image in self._processes().items() if image.casefold() in OLLAMA_IMAGES
        }
        clients: set[int] = set()
        for line in self._run(["netstat", "-ano", "-p", "TCP"]).splitlines():
            parts = line.split()
            if len(parts) != 5 or parts[0] != "TCP" or parts[3] != "ESTABLISHED":
                continue
            if parts[2].rsplit(":", 1)[-1] != OLLAMA_PORT or not parts[4].isdigit():
                continue
            pid = int(parts[4])
            if pid not in ollama and pid != self._own_pid:
                clients.add(pid)
        return sorted(clients)

    def used_vram_mib(self) -> int | None:
        output = self._run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]
        )
        first = output.strip().splitlines()[:1]
        return int(first[0]) if first and first[0].strip().isdigit() else None

    def _processes(self) -> dict[int, str]:
        processes: dict[int, str] = {}
        for line in self._run(["tasklist", "/FO", "CSV", "/NH"]).splitlines():
            fields = [field.strip('"') for field in line.split('","')]
            if len(fields) >= 2 and fields[1].isdigit():
                processes[int(fields[1])] = fields[0]
        return processes

    def _named_program(self) -> str | None:
        if not self._patterns:
            return None
        now = self._clock()
        if self._scanned is not None and now - self._scanned[0] < SCAN_SECONDS:
            return self._scanned[1]
        lines = [line.casefold() for line in self._command_lines()]
        found = next((pattern for pattern in self._patterns if any(pattern in line for line in lines)), None)
        self._scanned = (now, found)
        return found

    def _command_lines(self) -> list[str]:
        if sys.platform == "win32":
            output = self._run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }",
                ]
            )
        else:
            output = self._run(["ps", "-eo", "args="])
        return output.splitlines()


def start_ollama() -> bool:
    """Starts the Ollama app, or `ollama serve`, outside this process tree. False when neither exists."""
    app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
    if sys.platform == "win32":
        target = str(app) if app.is_file() else shutil.which("ollama")
        if target is None:
            return False
        serve = "" if target == str(app) else " serve"
        # Through `start`, so ending the daily run's process tree leaves Ollama running.
        subprocess.Popen(
            f'cmd /c start "" /min "{target}"{serve}',
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    binary = shutil.which("ollama")
    if binary is None:
        return False
    subprocess.Popen(
        [binary, "serve"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True
