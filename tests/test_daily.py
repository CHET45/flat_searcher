import contextlib
import io
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flat_searcher.cli import main
from flat_searcher.daily.control import (
    ENDED,
    NOT_RUNNING,
    STOPPED_ITSELF,
    DailyPaths,
    RunLock,
    is_running,
    set_switched_on,
    stop_run,
    switched_on,
)
from flat_searcher.daily.gpu import GpuGuard
from flat_searcher.daily.judge import CandidateJudge, unjudged_candidates
from flat_searcher.daily.monitor import describe, duration
from flat_searcher.daily.progress import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED,
    STOPPED,
    WAITING,
    ProgressFile,
    mark_stopped,
    read_progress,
)
from flat_searcher.daily.run import (
    ALREADY_DONE,
    DailyRun,
    Step,
    StepContext,
    StepResult,
    StopRequested,
)
from flat_searcher.library import LocalLibraryStore
from flat_searcher.logging_config import configure_logging
from flat_searcher.shortlist.criteria import parse_criteria
from test_judging import FakeAsk, FakeFetcher, _entry

DAY = "2026-10-07"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _progress(root: Path, **kwargs) -> ProgressFile:
    return ProgressFile(root / "progress.json", root / "daily.log", now=lambda: f"{DAY}T10:00:00+03:00", **kwargs)


def _steps(calls: list[str], outcomes: dict | None = None) -> list[Step]:
    outcomes = outcomes or {}

    def action(name: str):
        def run(context: StepContext) -> StepResult:
            calls.append(name)
            outcome = outcomes.get(name)
            if callable(outcome):
                return outcome(context)
            return outcome or StepResult(DONE, f"{name} ok", {"seen": name})

        return run

    return [Step(name, name.title(), action(name)) for name in ("one", "two", "three")]


class ProgressFileTests(TestCase):
    def test_a_rerun_on_the_same_day_keeps_finished_steps_and_resets_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = _progress(root)
            first.begin(DAY, [("a", "A"), ("b", "B"), ("c", "C")], pid=1)
            first.start_step("a")
            first.finish_step("a", DONE, "fine", {"n": 1})
            first.start_step("b")
            first.count("b", 5, 10, "listings")

            again = _progress(root)
            again.begin(DAY, [("a", "A"), ("b", "B"), ("c", "C")], pid=2)
            steps = {step["name"]: step for step in read_progress(root / "progress.json")["steps"]}

            self.assertEqual((steps["a"]["status"], steps["a"]["data"]), (DONE, {"n": 1}))
            self.assertEqual((steps["b"]["status"], steps["b"]["done"]), (PENDING, None))
            self.assertEqual(read_progress(root / "progress.json")["pid"], 2)

    def test_another_day_or_fresh_starts_from_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            earlier = _progress(root)
            earlier.begin("2026-10-06", [("a", "A")], pid=1)
            earlier.finish_step("a", DONE)

            today = _progress(root)
            today.begin(DAY, [("a", "A")], pid=2)
            self.assertEqual(today.step("a")["status"], PENDING)
            today.finish_step("a", DONE)

            fresh = _progress(root)
            fresh.begin(DAY, [("a", "A")], pid=3, fresh=True)
            self.assertEqual(fresh.step("a")["status"], PENDING)

    def test_a_new_unit_starts_a_new_phase_for_the_time_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            progress = _progress(Path(temp))
            progress.begin(DAY, [("a", "A")], pid=1)
            progress.count("a", 1, 87, "list pages")
            progress.count("a", 87, 87, "list pages")
            progress.count("a", 3, 2600, "listings")
            step = progress.step("a")
            self.assertEqual((step["unit"], step["phase_done"], step["done"]), ("listings", 3, 3))

    def test_the_log_keeps_the_newest_lines_and_appends_to_the_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            progress = _progress(root)
            progress.begin(DAY, [("a", "A")], pid=1)
            for number in range(305):
                progress.log(f"line {number}")
            progress.finish(DONE)
            log = read_progress(root / "progress.json")["log"]
            self.assertEqual((len(log), log[-1]), (300, "10:00:00 line 304"))
            self.assertEqual(len((root / "daily.log").read_text(encoding="utf-8").splitlines()), 305)

    def test_a_run_ended_from_outside_is_marked_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            progress = _progress(root)
            progress.begin(DAY, [("a", "A"), ("b", "B")], pid=1)
            progress.start_step("a")
            progress.wait("a", "Waiting for the GPU")
            mark_stopped(root / "progress.json", now=f"{DAY}T11:00:00+03:00")
            state = read_progress(root / "progress.json")
            self.assertEqual(state["state"], STOPPED)
            self.assertEqual([step["status"] for step in state["steps"]], [STOPPED, PENDING])


class DailyRunTests(TestCase):
    def _run(self, root: Path, steps: list[Step], stop=lambda: False, fresh=False) -> str:
        return DailyRun(_progress(root), steps, stop, DAY, pid=7, sleep=lambda _: None).run(fresh=fresh)

    def test_steps_run_in_order_and_later_steps_see_earlier_results(self) -> None:
        seen: list[dict] = []

        def three(context: StepContext) -> StepResult:
            seen.append(dict(context.results))
            return StepResult(DONE)

        with tempfile.TemporaryDirectory() as temp:
            calls: list[str] = []
            outcome = self._run(Path(temp), _steps(calls, {"three": three}))
            self.assertEqual((outcome, calls), (DONE, ["one", "two", "three"]))
            self.assertEqual(seen, [{"one": {"seen": "one"}, "two": {"seen": "two"}}])

    def test_a_failed_step_ends_the_run_and_the_next_run_resumes_there(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls: list[str] = []
            outcome = self._run(root, _steps(calls, {"two": StepResult(FAILED, "broken")}))
            state = read_progress(root / "progress.json")
            self.assertEqual((outcome, calls), (FAILED, ["one", "two"]))
            self.assertEqual([step["status"] for step in state["steps"]], [DONE, FAILED, PENDING])

            resumed: list[str] = []
            seen: list[dict] = []

            def two(context: StepContext) -> StepResult:
                seen.append(dict(context.results))
                return StepResult(DONE)

            self.assertEqual(self._run(root, _steps(resumed, {"two": two})), DONE)
            self.assertEqual(resumed, ["two", "three"])
            self.assertEqual(seen, [{"one": {"seen": "one"}}])

    def test_a_finished_day_is_not_run_again_unless_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._run(root, _steps([]))
            again: list[str] = []
            self.assertEqual(self._run(root, _steps(again)), ALREADY_DONE)
            self.assertEqual(again, [])
            self.assertEqual(self._run(root, _steps(again), fresh=True), DONE)
            self.assertEqual(again, ["one", "two", "three"])

    def test_an_exception_fails_the_step_with_its_type_only(self) -> None:
        def explode(context: StepContext) -> StepResult:
            raise RuntimeError("https://secret.example/library")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self._run(root, _steps([], {"one": explode})), FAILED)
            text = (root / "progress.json").read_text(encoding="utf-8")
            self.assertIn("error: RuntimeError", text)
            self.assertNotIn("secret", text)

    def test_a_stop_inside_a_step_marks_it_stopped_and_ends_the_run(self) -> None:
        requested = {"stop": False}

        def counting(context: StepContext) -> StepResult:
            context.count(1, 10, "listings")
            requested["stop"] = True
            context.count(2, 10, "listings")
            return StepResult(DONE)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls: list[str] = []
            outcome = self._run(root, _steps(calls, {"two": counting}), stop=lambda: requested["stop"])
            state = read_progress(root / "progress.json")
            self.assertEqual((outcome, calls), (STOPPED, ["one", "two"]))
            self.assertEqual(state["state"], STOPPED)
            self.assertEqual([step["status"] for step in state["steps"]], [DONE, STOPPED, PENDING])

    def test_a_stop_between_steps_runs_nothing_more(self) -> None:
        requested = {"stop": False}

        def first(context: StepContext) -> StepResult:
            requested["stop"] = True
            return StepResult(DONE)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls: list[str] = []
            outcome = self._run(root, _steps(calls, {"one": first}), stop=lambda: requested["stop"])
            state = read_progress(root / "progress.json")
            self.assertEqual((outcome, calls), (STOPPED, ["one"]))
            self.assertEqual([step["status"] for step in state["steps"]], [DONE, PENDING, PENDING])

    def test_stop_requested_is_not_an_exception_a_stage_handler_would_swallow(self) -> None:
        self.assertFalse(issubclass(StopRequested, Exception))


class ControlTests(TestCase):
    def test_the_lock_admits_one_run_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = DailyPaths(Path(temp))
            first, second = RunLock(paths.lock), RunLock(paths.lock)
            self.assertFalse(is_running(paths))
            self.assertTrue(first.acquire())
            self.assertTrue(is_running(paths))
            self.assertFalse(second.acquire())
            first.release()
            self.assertFalse(is_running(paths))
            self.assertTrue(second.acquire())
            second.release()

    def test_the_switch_is_on_until_turned_off(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = DailyPaths(Path(temp) / "daily")
            self.assertTrue(switched_on(paths))
            set_switched_on(paths, False)
            self.assertFalse(switched_on(paths))
            set_switched_on(paths, True)
            self.assertTrue(switched_on(paths))

    def test_stopping_nothing_is_reported_as_such(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = DailyPaths(Path(temp))
            self.assertEqual(stop_run(paths, kill=self.fail), NOT_RUNNING)
            self.assertFalse(paths.stop.exists())

    def test_a_run_that_honours_the_stop_request_is_not_killed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = DailyPaths(Path(temp))
            lock = RunLock(paths.lock)
            lock.acquire()

            def run() -> None:
                while not paths.stop_requested():
                    time.sleep(0.01)
                lock.release()

            worker = threading.Thread(target=run)
            worker.start()
            self.assertEqual(stop_run(paths, grace_seconds=5, kill=self.fail), STOPPED_ITSELF)
            worker.join()

    def test_a_run_that_ignores_the_request_is_ended_and_marked_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = DailyPaths(Path(temp))
            progress = ProgressFile(paths.progress)
            progress.begin(DAY, [("a", "A")], pid=4242)
            progress.start_step("a")
            lock = RunLock(paths.lock)
            lock.acquire()
            killed: list[int] = []
            try:
                outcome = stop_run(paths, grace_seconds=0.2, sleep=lambda _: None, kill=killed.append)
            finally:
                lock.release()
            self.assertEqual((outcome, killed), (ENDED, [4242]))
            self.assertEqual(read_progress(paths.progress)["state"], STOPPED)
            self.assertFalse(paths.stop.exists())


NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:11434          0.0.0.0:0              LISTENING       500
  TCP    127.0.0.1:11434        127.0.0.1:50001        ESTABLISHED     500
  TCP    127.0.0.1:50001        127.0.0.1:11434        ESTABLISHED     900
  TCP    127.0.0.1:50002        127.0.0.1:11434        ESTABLISHED     501
  TCP    127.0.0.1:50003        127.0.0.1:11434        ESTABLISHED     77
  TCP    127.0.0.1:50004        127.0.0.1:11434        TIME_WAIT       0
  TCP    127.0.0.1:50005        127.0.0.1:8080         ESTABLISHED     901
"""
TASKLIST = '"ollama.exe","500","Console","1","20,000 K"\n"ollama app.exe","501","Console","1","9,000 K"\n'


def _runner(netstat: str = "", vram: str = "", processes: str = ""):
    def run(command) -> str:
        if command[0] == "netstat":
            return netstat
        if command[0] == "tasklist":
            return TASKLIST
        if command[0] == "nvidia-smi":
            return vram
        return processes

    return run


class GpuGuardTests(TestCase):
    def test_another_ollama_client_makes_the_gpu_busy_but_ollama_itself_and_we_do_not(self) -> None:
        guard = GpuGuard(lambda: 0, run=_runner(NETSTAT), own_pid=77)
        self.assertEqual(guard.other_ollama_clients(), [900])
        self.assertIn("900", guard.busy() or "")

    def test_video_memory_held_by_others_counts_without_the_model_itself(self) -> None:
        free = GpuGuard(lambda: 6000, run=_runner(vram="8000\n"))
        self.assertIsNone(free.busy())
        busy = GpuGuard(lambda: 0, run=_runner(vram="4817\n"))
        self.assertEqual(busy.busy(), "other programs hold 4.7 GB of video memory")

    def test_a_named_program_makes_the_gpu_busy(self) -> None:
        guard = GpuGuard(
            lambda: 0,
            busy_patterns=[" FaceFusion", " "],
            run=_runner(processes="python.exe facefusion.py run\nexplorer.exe\n"),
        )
        self.assertEqual(guard.busy(), "facefusion is running")

    def test_the_slow_process_scan_is_reused_for_a_minute(self) -> None:
        scans: list[str] = []
        listing = {"text": "python.exe facefusion.py\n"}
        clock = FakeClock()

        def run(command) -> str:
            if command[0] in ("powershell", "ps"):
                scans.append(command[0])
                return listing["text"]
            return ""

        guard = GpuGuard(lambda: 0, busy_patterns=["facefusion"], run=run, clock=clock)
        self.assertIsNotNone(guard.busy())
        listing["text"] = "explorer.exe\n"
        clock.now = 59
        self.assertIsNotNone(guard.busy())
        clock.now = 61
        self.assertIsNone(guard.busy())
        self.assertEqual(len(scans), 2)

    def test_missing_tools_mean_free(self) -> None:
        self.assertIsNone(GpuGuard(lambda: 0, run=lambda command: "").busy())


class FakeClient:
    name = "ollama/test"

    def __init__(self, ask: FakeAsk, reachable: bool = True) -> None:
        self._ask = ask
        self.reachable = reachable
        self.unloads = 0

    def ask(self, system, text, images, schema) -> str:
        return self._ask(system, text, images, schema)

    def available(self) -> bool:
        return self.reachable

    def unload(self) -> None:
        self.unloads += 1


class FakeGuard:
    def __init__(self, answers: list[str | None]) -> None:
        self.answers = answers

    def busy(self) -> str | None:
        return self.answers.pop(0) if self.answers else None


class CandidateJudgeTests(TestCase):
    def _context(self, root: Path, clock: FakeClock, stop=lambda: False) -> tuple[StepContext, ProgressFile]:
        progress = _progress(root)
        progress.begin(DAY, [("judge", "Judge")], pid=1)
        progress.start_step("judge")
        return StepContext(progress, "judge", stop, {}, clock.sleep), progress

    def _setup(self, root: Path) -> tuple[LocalLibraryStore, Path]:
        store = LocalLibraryStore(root / "library")
        store.write_queue("2026-10-05", [_entry("a1"), _entry("b2")])
        store.write_queue("2026-10-07", [_entry("c3")])
        instructions = root / "instructions.md"
        instructions.write_text("Judge.", encoding="utf-8")
        return store, instructions

    def _judge(self, store, instructions, client, guard, clock, start=lambda: True) -> CandidateJudge:
        return CandidateJudge(
            store, client, guard, instructions, FakeFetcher(), start,
            poll_seconds=15, quiet_seconds=120, start_timeout_seconds=10, clock=clock,
        )

    def test_judges_every_planned_candidate_and_unloads_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, clock = Path(temp), FakeClock()
            store, instructions = self._setup(root)
            client = FakeClient(FakeAsk())
            context, progress = self._context(root, clock)
            result = self._judge(store, instructions, client, FakeGuard([]), clock).run(
                context, {"2026-10-05": ["a1"], "2026-10-07": ["c3"]}, unqueued=1
            )
            self.assertEqual((result.status, dict(result.data)), (DONE, {"judged": 2}))
            self.assertEqual(result.summary, "judged=2 failed=0; 1 in no queue")
            self.assertEqual([v["ss_id"] for v in store.read_verdicts("2026-10-05")], ["a1"])
            self.assertEqual([v["ss_id"] for v in store.read_verdicts("2026-10-07")], ["c3"])
            self.assertEqual((progress.step("judge")["done"], progress.step("judge")["total"]), (2, 2))
            self.assertEqual(client.unloads, 1)

    def test_a_busy_gpu_unloads_the_model_and_waits_for_a_quiet_spell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, clock = Path(temp), FakeClock()
            store, instructions = self._setup(root)
            client = FakeClient(FakeAsk())
            context, progress = self._context(root, clock)
            states: list[str] = []
            guard = FakeGuard(["facefusion is running", "facefusion is running", None, "game", None])
            original = progress.wait

            def wait(name: str, reason: str) -> None:
                states.append(reason)
                original(name, reason)

            progress.wait = wait  # type: ignore[method-assign]
            result = self._judge(store, instructions, client, guard, clock).run(
                context, {"2026-10-05": ["a1"]}, unqueued=0
            )
            self.assertEqual(result.status, DONE)
            self.assertEqual(
                states,
                ["Waiting for the GPU: facefusion is running", "Waiting for the GPU: game"],
            )
            self.assertGreaterEqual(clock.now, 15 * 4 + 120)
            self.assertEqual(client.unloads, 2)
            self.assertEqual(progress.step("judge")["status"], RUNNING)

    def test_nothing_to_judge_and_no_server_are_not_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, clock = Path(temp), FakeClock()
            store, instructions = self._setup(root)
            context, _ = self._context(root, clock)
            nothing = self._judge(store, instructions, FakeClient(FakeAsk()), FakeGuard([]), clock)
            self.assertEqual(nothing.run(context, {}, 0).status, DONE)
            down = self._judge(
                store, instructions, FakeClient(FakeAsk(), reachable=False), FakeGuard([]), clock,
                start=lambda: False,
            )
            result = down.run(context, {"2026-10-05": ["a1"]}, 0)
            self.assertEqual((result.status, result.data["judged"]), (SKIPPED, 0))

    def test_a_model_that_keeps_failing_fails_the_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, clock = Path(temp), FakeClock()
            store, instructions = self._setup(root)
            store.write_queue("2026-10-05", [_entry(f"x{n}") for n in range(4)])
            context, _ = self._context(root, clock)
            client = FakeClient(FakeAsk(default="not json"))
            result = self._judge(store, instructions, client, FakeGuard([]), clock).run(
                context, {"2026-10-05": [f"x{n}" for n in range(4)]}, 0
            )
            self.assertEqual(result.status, FAILED)
            self.assertIn("Aborted after 3 consecutive failures", result.summary)
            self.assertEqual(client.unloads, 1)

    def test_a_stop_while_waiting_for_the_gpu_ends_the_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, clock = Path(temp), FakeClock()
            store, instructions = self._setup(root)
            context, _ = self._context(root, clock, stop=lambda: clock.now > 30)
            judge = self._judge(store, instructions, FakeClient(FakeAsk()), FakeGuard(["busy"] * 99), clock)
            with self.assertRaises(StopRequested):
                judge.run(context, {"2026-10-05": ["a1"]}, 0)
            self.assertEqual(store.read_verdicts("2026-10-05"), [])

    def test_candidates_without_any_verdict_are_planned_by_their_newest_queue_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LocalLibraryStore(Path(temp))

            def record(ss_id: str, price: int, judged: bool = False) -> dict:
                core = {"price_eur": price, "declared_rooms": 2, "area_m2": 40}
                return {
                    "ss_id": ss_id, "status": "active", "url": "u", "core": core,
                    "text": {"description": ""}, **({"judgment": {"score": 1}} if judged else {}),
                }

            store.save_listings({
                "cheap": record("cheap", 40000),
                "again": record("again", 41000),
                "pricey": record("pricey", 90000),
                "merged": record("merged", 42000, judged=True),
                "verdict": record("verdict", 43000),
                "lost": record("lost", 44000),
            })
            store.write_queue("2026-10-05", [{"ss_id": s} for s in ("cheap", "again", "pricey", "verdict")])
            store.write_queue("2026-10-07", [{"ss_id": "again"}])
            store.append_verdicts("2026-10-05", [{"ss_id": "verdict", "score": 3}])
            plan, unqueued = unjudged_candidates(store, parse_criteria("[price]\nmax_eur = 60000\n"))
            self.assertEqual(plan, {"2026-10-05": ["cheap"], "2026-10-07": ["again"]})
            self.assertEqual(unqueued, 1)


class MonitorViewTests(TestCase):
    NOW = datetime.fromisoformat(f"{DAY}T10:10:00+03:00")

    def _state(self, state: str, steps: list[dict], current: str | None = None, day: str = DAY) -> dict:
        return {"day": day, "state": state, "current": current, "steps": steps, "log": ["10:00:00 hi"],
                "finished_at": f"{day}T11:42:10+03:00"}

    def _step(self, name: str, status: str, **extra) -> dict:
        return {"name": name, "title": name.title(), "status": status, **extra}

    def test_a_running_count_shows_the_step_its_count_and_the_time_left(self) -> None:
        crawl = self._step(
            "crawl", RUNNING, started_at=f"{DAY}T10:00:00+03:00", done=1100, total=2600, unit="listings",
            phase_started_at=f"{DAY}T10:05:00+03:00", phase_done=800,
        )
        view = describe(
            self._state(RUNNING, [crawl, self._step("transit", PENDING)], "crawl"),
            running=True, today=DAY, now=self.NOW,
        )
        self.assertEqual(view.headline, "Running: Crawl (step 1 of 2)")
        self.assertEqual(view.detail, "1,100 of 2,600 listings · about 25 min 00 s left")
        self.assertAlmostEqual(view.fraction or 0, 1100 / 2600)
        self.assertEqual((view.rows[0].progress, view.rows[0].time), ("1,100 / 2,600 listings", "10 min 00 s"))

    def test_waiting_interrupted_finished_failed_and_old_runs_read_differently(self) -> None:
        waiting = self._step("judge", WAITING, note="Waiting for the GPU: game")
        self.assertEqual(
            describe(self._state(WAITING, [waiting], "judge"), running=True, today=DAY, now=self.NOW).headline,
            "Waiting for the GPU: game",
        )
        crawl = self._step("crawl", RUNNING)
        self.assertEqual(
            describe(self._state(RUNNING, [crawl], "crawl"), running=False, today=DAY, now=self.NOW).headline,
            "Interrupted during Crawl. Start resumes from there.",
        )
        done = describe(self._state(DONE, [self._step("crawl", DONE)]), running=False, today=DAY, now=self.NOW)
        self.assertEqual((done.headline, done.finished_today), ("Finished at 11:42.", True))
        failed = self._step("transit", FAILED, summary="Transit sources failed: OSError")
        self.assertEqual(
            describe(self._state(FAILED, [self._step("crawl", DONE), failed]), running=False,
                     today=DAY, now=self.NOW).headline,
            "Failed at Transit: Transit sources failed: OSError",
        )
        old = describe(self._state(DONE, [], day="2026-10-05"), running=False, today=DAY, now=self.NOW)
        self.assertEqual((old.headline, old.finished_today), ("No run today yet. Last run 2026-10-05: done.", False))
        self.assertEqual(describe(None, running=False, today=DAY, now=self.NOW).headline, "No run yet.")

    def test_durations_read_as_seconds_minutes_or_hours(self) -> None:
        self.assertEqual([duration(s) for s in (4.4, 192, 3 * 3600 + 5 * 60)], ["4 s", "3 min 12 s", "3 h 05 min"])


class DailyCommandTests(TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.home = Path(self._temp.name)
        self.library = self.home / "library"
        self.paths = DailyPaths(self.home / "daily")
        environment = {
            "FLAT_SEARCHER_HOME": str(self.home),
            "FLAT_SEARCHER_LIBRARY_PATH": str(self.library),
            "FLAT_SEARCHER_ENV_FILE": str(self.home / "missing.env"),
        }
        shell = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        self._env = patch.dict(os.environ, {**shell, **environment}, clear=True)
        self._env.start()

    def tearDown(self) -> None:
        configure_logging()
        self._env.stop()
        self._temp.cleanup()

    def _main(self, *argv: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(argv))
        return code, output.getvalue()

    def test_the_daily_command_runs_the_steps_and_writes_progress(self) -> None:
        calls: list[str] = []
        with patch("flat_searcher.cli._daily_steps", lambda config, day: _steps(calls)):
            code, output = self._main("daily")
        self.assertEqual((code, calls), (0, ["one", "two", "three"]))
        self.assertIn(": done", output)
        self.assertEqual(read_progress(self.paths.progress)["state"], DONE)
        self.assertFalse(is_running(self.paths))

    def test_startup_does_nothing_when_switched_off_or_already_running(self) -> None:
        calls: list[str] = []
        with patch("flat_searcher.cli._daily_steps", lambda config, day: _steps(calls)):
            set_switched_on(self.paths, False)
            self.assertEqual(self._main("daily", "--startup"), (0, "daily: switched off\n"))
            set_switched_on(self.paths, True)
            lock = RunLock(self.paths.lock)
            lock.acquire()
            try:
                self.assertEqual(self._main("daily", "--startup"), (0, "daily: already running\n"))
            finally:
                lock.release()
        self.assertEqual(calls, [])

    def test_a_failed_step_fails_the_command(self) -> None:
        with patch("flat_searcher.cli._daily_steps", lambda config, day: _steps([], {"one": StepResult(FAILED)})):
            self.assertEqual(self._main("daily")[0], 1)

    def test_the_switch_turns_off_and_on(self) -> None:
        launched: list[list[str]] = []
        with patch("flat_searcher.daily.monitor.show_message") as shown, patch(
            "flat_searcher.cli.launch", lambda arguments, cwd: launched.append(list(arguments))
        ):
            self._main("switch")
            self.assertFalse(switched_on(self.paths))
            self.assertIn("OFF", shown.call_args.args[1])
            self._main("switch")
            self.assertTrue(switched_on(self.paths))
            self.assertIn("ON", shown.call_args.args[1])
        self.assertEqual(launched, [["daily"]])

    def test_switching_off_stops_a_running_analysis(self) -> None:
        lock = RunLock(self.paths.lock)
        lock.acquire()

        def run() -> None:
            while not self.paths.stop_requested():
                time.sleep(0.01)
            lock.release()

        worker = threading.Thread(target=run)
        worker.start()
        with patch("flat_searcher.daily.monitor.show_message") as shown:
            self._main("switch", "off")
        worker.join(timeout=5)
        self.assertIn("is stopping", shown.call_args.args[1])
        self.assertFalse(is_running(self.paths))

    def test_the_index_step_is_done_when_an_index_run_finished_today(self) -> None:
        from flat_searcher.cli import _daily_steps
        from flat_searcher.config import AppConfig

        store = LocalLibraryStore(self.library)
        finished = datetime.now().astimezone().replace(microsecond=0)
        store.write_meta({
            "finished_at": finished.isoformat(), "status": "ok", "dry_run": False,
            "stages": [{"name": "promote", "counts": {"new": 4, "removed": 2}}],
        })
        index = _daily_steps(AppConfig.from_env(), finished.date().isoformat())[0]
        progress = _progress(self.home)
        progress.begin(DAY, [("index", "Index")], pid=1)
        context = StepContext(progress, "index", lambda: False, {}, lambda _: None)
        with patch("flat_searcher.cli._run_index", side_effect=AssertionError("crawled again")):
            result = index.action(context)
        self.assertEqual(result.status, DONE)
        self.assertEqual(result.summary, f"already ran at {finished:%H:%M}: new=4 removed=2")

        store.write_meta({**store.load_meta(), "finished_at": (finished - timedelta(days=1)).isoformat()})
        with patch("flat_searcher.cli._run_index", return_value=0) as crawl, patch(
            "flat_searcher.cli.socket.getaddrinfo"
        ):
            self.assertEqual(index.action(context).status, DONE)
        crawl.assert_called_once()

    def test_the_verdict_steps_run_only_after_new_verdicts(self) -> None:
        from flat_searcher.cli import _daily_steps
        from flat_searcher.config import AppConfig

        steps = {step.name: step for step in _daily_steps(AppConfig.from_env(), DAY)}
        store = LocalLibraryStore(self.library)
        store.save_listings({"a1": {"ss_id": "a1", "status": "active", "core": {}}})
        store.append_verdicts(DAY, [{"ss_id": "a1", "score": 70, "judged_at": "t"}])
        progress = _progress(self.home)
        progress.begin(DAY, [("merge", "Merge")], pid=1)

        def run(name: str, judged: int) -> StepResult:
            context = StepContext(progress, name, lambda: False, {"judge": {"judged": judged}}, lambda _: None)
            return steps[name].action(context)

        self.assertEqual(run("merge", 0).status, SKIPPED)
        self.assertNotIn("judgment", store.load_listings()["a1"])
        self.assertEqual(run("publish_again", 0).summary, "nothing new was judged")
        merged = run("merge", 1)
        self.assertEqual((merged.status, merged.summary), (DONE, "merged=1 unknown=0"))
        self.assertEqual(store.load_listings()["a1"]["judgment"]["score"], 70)


class ProgressFileWritesTests(TestCase):
    def test_a_finished_run_is_not_written_again_by_a_pending_flush(self) -> None:
        from flat_searcher.daily import progress as module

        writes: list[str] = []
        original = module.write_progress

        with tempfile.TemporaryDirectory() as temp:
            mine = Path(temp) / "progress.json"

            def counting(path, state) -> None:
                if path == mine:
                    writes.append(state["state"])
                original(path, state)

            patcher = patch.object(module, "write_progress", counting)
            patcher.start()
            self.addCleanup(patcher.stop)
            progress = _progress(Path(temp))
            progress.begin(DAY, [("a", "A")], pid=1)
            progress.count("a", 1, 9, "listings")
            progress.finish(DONE)
            written = len(writes)
            time.sleep(0.8)
            self.assertEqual(len(writes), written)

    def test_a_throttled_count_still_reaches_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            progress = _progress(root)
            progress.begin(DAY, [("a", "A")], pid=1)
            progress.count("a", 1, 9, "listings")
            progress.count("a", 2, 9, "listings")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if json.loads((root / "progress.json").read_text(encoding="utf-8"))["steps"][0]["done"] == 2:
                    break
                time.sleep(0.05)
            self.assertEqual(read_progress(root / "progress.json")["steps"][0]["done"], 2)
