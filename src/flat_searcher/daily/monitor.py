"""A small window over the daily run: steps, counts, time left, the log, and start, stop and switch."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from flat_searcher.daily.control import (
    DailyPaths,
    is_running,
    launch,
    set_switched_on,
    stop_run,
    switched_on,
)
from flat_searcher.daily.progress import (
    ACTIVE,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED,
    STOPPED,
    WAITING,
    read_progress,
)

REFRESH_MS = 1000
STATUS_LABELS = {
    PENDING: "",
    RUNNING: "▶ running",
    WAITING: "⏸ waiting",
    DONE: "✓ done",
    SKIPPED: "– skipped",
    FAILED: "✗ failed",
    STOPPED: "■ stopped",
}


@dataclass(frozen=True)
class Row:
    name: str
    title: str
    status: str
    progress: str
    time: str
    result: str


@dataclass(frozen=True)
class View:
    headline: str
    rows: list[Row] = field(default_factory=list)
    detail: str = ""
    fraction: float | None = None
    log: list[str] = field(default_factory=list)
    running: bool = False
    finished_today: bool = False


def describe(
    state: Mapping[str, Any] | None, *, running: bool, today: str, now: datetime
) -> View:
    if state is None:
        return View("No run yet.", running=running)
    steps: Sequence[Mapping[str, Any]] = state.get("steps") or []
    rows = [_row(step, now) for step in steps]
    day = state.get("day") or ""
    on_day = "" if day == today else f" ({day})"
    status = state.get("state")
    current = _find(steps, state.get("current")) or _last_touched(steps)
    title = current.get("title", "") if current else ""
    detail, fraction = ("", None)
    if status in ACTIVE and running and current is not None:
        position = steps.index(current) + 1
        detail, fraction = _detail(current, now)
        if status == WAITING:
            headline = (current.get("note") or f"Waiting: {title}") + on_day
        else:
            headline = f"Running{on_day}: {title} (step {position} of {len(steps)})"
    elif status in ACTIVE:
        headline = f"Interrupted{on_day} during {title}. Start resumes from there."
    elif day != today:
        headline = f"No run today yet. Last run {day}: {status}."
    elif status == DONE:
        headline = f"Finished at {_clock(state.get('finished_at'))}."
    elif status == FAILED:
        reason = current.get("summary") if current else ""
        headline = f"Failed at {title}: {reason}" if reason else f"Failed at {title}."
    elif status == STOPPED:
        headline = f"Stopped during {title}." if title else "Stopped."
    else:
        headline = f"Today's run: {status}."
    return View(
        headline,
        rows,
        detail,
        fraction,
        list(state.get("log") or []),
        running,
        finished_today=day == today and status == DONE,
    )


def duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def _row(step: Mapping[str, Any], now: datetime) -> Row:
    status = step.get("status") or PENDING
    started = _time(step.get("started_at"))
    finished = _time(step.get("finished_at"))
    spent = ""
    if started is not None and status != PENDING:
        spent = duration(((finished or now) - started).total_seconds())
    done, total = step.get("done"), step.get("total")
    counted = f"{done:,} / {total:,} {step.get('unit') or ''}".strip() if total else ""
    return Row(
        name=str(step.get("name")),
        title=str(step.get("title") or step.get("name")),
        status=STATUS_LABELS.get(status, status),
        progress=counted,
        time=spent,
        result=str(step.get("summary") or step.get("note") or ""),
    )


def _detail(step: Mapping[str, Any], now: datetime) -> tuple[str, float | None]:
    done, total = step.get("done"), step.get("total")
    if not total or done is None:
        return str(step.get("note") or ""), None
    text = f"{done:,} of {total:,} {step.get('unit') or ''}".rstrip()
    since = _time(step.get("phase_started_at"))
    moved = done - int(step.get("phase_done") or 0)
    if since is not None and moved > 0 and done < total and step.get("status") == RUNNING:
        left = (now - since).total_seconds() / moved * (total - done)
        text += f" · about {duration(left)} left"
    return text, min(1.0, done / total)


def _find(steps: Sequence[Mapping[str, Any]], name: Any) -> Mapping[str, Any] | None:
    return next((step for step in steps if step.get("name") == name), None)


def _last_touched(steps: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    touched = [step for step in steps if step.get("status") not in (PENDING, DONE, SKIPPED)]
    return touched[-1] if touched else None


def _time(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _clock(text: Any) -> str:
    moment = _time(text)
    return moment.strftime("%H:%M") if moment else "?"


def open_window(
    paths: DailyPaths, cwd: Path, page: Callable[[], Path | None]
) -> None:  # pragma: no cover - a window; the logic above is tested
    import tkinter as tk
    from tkinter import messagebox, ttk

    _dpi_aware()
    root = tk.Tk()
    root.title("Flat Searcher")
    root.geometry("980x640")
    root.minsize(720, 480)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(3, weight=1)

    headline = ttk.Label(root, font=("Segoe UI", 13, "bold"), wraplength=940)
    headline.grid(row=0, column=0, sticky="we", padx=12, pady=(12, 2))
    switch_on = tk.BooleanVar(value=switched_on(paths))
    switch = ttk.Checkbutton(
        root,
        text="Start the analysis when the computer starts",
        variable=switch_on,
        command=lambda: set_switched_on(paths, switch_on.get()),
    )
    switch.grid(row=1, column=0, sticky="w", padx=12)

    table = ttk.Treeview(
        root,
        columns=("step", "status", "progress", "time", "result"),
        show="headings",
        height=8,
        selectmode="none",
    )
    for column, heading, width, stretch in (
        ("step", "Step", 230, False),
        ("status", "Status", 100, False),
        ("progress", "Progress", 150, False),
        ("time", "Time", 100, False),
        ("result", "Result", 360, True),
    ):
        table.heading(column, text=heading, anchor="w")
        table.column(column, width=width, stretch=stretch, anchor="w")
    table.grid(row=2, column=0, sticky="we", padx=12, pady=8)

    middle = ttk.Frame(root)
    middle.grid(row=3, column=0, sticky="nsew", padx=12)
    middle.columnconfigure(0, weight=1)
    middle.rowconfigure(2, weight=1)
    bar = ttk.Progressbar(middle, maximum=1000)
    bar.grid(row=0, column=0, sticky="we")
    detail = ttk.Label(middle)
    detail.grid(row=1, column=0, sticky="w", pady=(2, 6))
    log = tk.Text(middle, height=12, wrap="none", font=("Consolas", 9), state="disabled")
    log.grid(row=2, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(middle, command=log.yview)
    scroll.grid(row=2, column=1, sticky="ns")
    log.configure(yscrollcommand=scroll.set)

    buttons = ttk.Frame(root)
    buttons.grid(row=4, column=0, sticky="we", padx=12, pady=12)
    start = ttk.Button(buttons, text="Start")
    start.pack(side="left")
    stop = ttk.Button(buttons, text="Stop")
    stop.pack(side="left", padx=8)
    open_page = ttk.Button(buttons, text="Open today's page")
    open_page.pack(side="left")
    open_log = ttk.Button(buttons, text="Open log", command=lambda: _open(paths.log))
    open_log.pack(side="left", padx=8)

    shown: dict[str, Any] = {"log": None, "view": None, "stopping": False, "bar": None}

    def refresh() -> None:
        now = datetime.now().astimezone()
        view = describe(
            read_progress(paths.progress),
            running=is_running(paths),
            today=now.date().isoformat(),
            now=now,
        )
        shown["view"] = view
        headline.configure(text="Stopping…" if shown["stopping"] else view.headline)
        switch_on.set(switched_on(paths))
        present = set(table.get_children())
        for row in view.rows:
            values = (row.title, row.status, row.progress, row.time, row.result)
            if row.name in present:
                table.item(row.name, values=values)
            else:
                table.insert("", "end", iid=row.name, values=values)
        for name in present - {row.name for row in view.rows}:
            table.delete(name)
        if view.fraction is not None:
            if shown["bar"] != "counted":
                bar.stop()
                bar.configure(mode="determinate")
                shown["bar"] = "counted"
            bar.configure(value=int(view.fraction * 1000))
        elif view.running:
            if shown["bar"] != "busy":
                bar.configure(mode="indeterminate")
                bar.start(15)
                shown["bar"] = "busy"
        elif shown["bar"] != "idle":
            bar.stop()
            bar.configure(mode="determinate", value=0)
            shown["bar"] = "idle"
        detail.configure(text=view.detail)
        if view.log != shown["log"]:
            at_end = log.yview()[1] >= 0.999
            log.configure(state="normal")
            log.delete("1.0", "end")
            log.insert("end", "\n".join(view.log))
            log.configure(state="disabled")
            if at_end:
                log.see("end")
            shown["log"] = view.log
        start.configure(
            text="Run again" if view.finished_today else "Start",
            state="disabled" if view.running or shown["stopping"] else "normal",
        )
        stop.configure(state="normal" if view.running and not shown["stopping"] else "disabled")
        open_page.configure(state="normal" if page() else "disabled")
        root.after(REFRESH_MS, refresh)

    def on_start() -> None:
        view: View | None = shown["view"]
        if view is not None and view.finished_today:
            if not messagebox.askyesno(
                "Flat Searcher",
                "Today's analysis has finished. Run it again from the start?\n\n"
                "A new crawl of SS.com takes about 45 minutes.",
            ):
                return
            launch(["daily", "--fresh"], cwd)
        else:
            launch(["daily"], cwd)

    def on_stop() -> None:
        if not messagebox.askyesno("Flat Searcher", "Stop today's analysis?"):
            return
        shown["stopping"] = True

        def work() -> None:
            stop_run(paths)
            shown["stopping"] = False

        threading.Thread(target=work, daemon=True).start()

    def on_open_page() -> None:
        target = page()
        if target is not None:
            _open(target)

    start.configure(command=on_start)
    stop.configure(command=on_stop)
    open_page.configure(command=on_open_page)
    refresh()
    root.mainloop()


def show_message(title: str, text: str) -> None:  # pragma: no cover - a dialog
    try:
        import tkinter as tk
        from tkinter import messagebox

        _dpi_aware()
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(title, text, parent=root)
        root.destroy()
    except Exception:
        pass


def _open(path: Path) -> None:  # pragma: no cover - hands the file to the desktop
    if sys.platform == "win32":
        os.startfile(path)


def _dpi_aware() -> None:  # pragma: no cover - Windows only
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
