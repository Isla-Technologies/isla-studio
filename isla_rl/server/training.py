"""Training runs as separate processes, and what they report, for the web app.

A run started here is ``python -m isla_rl.learn train <folder>`` with its output in
the run's ``train.log``. Every run's log is followed, whoever started it (this app,
the command line, another computer sharing the folder), so each run's progress,
design search and practice robots reach the web app the same way.

A test of a run's policy in full physics is ``python -m isla_rl.learn evaluate
<folder>``; it writes the run's ``evaluation.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import paths
from ..learn.skills import TrainingSetup
from ..learn.trainer import LOG, STOP, Run, create_run, list_runs
from ..robot.profile import RobotProfile

TAIL_START = 400_000  # Bytes of an existing log read back when the app starts.
EVALUATION = "evaluation.json"
TEST_LOG = "evaluate.log"
KEEP = ("started", "progress", "codesign", "swarm", "preparing", "finished", "stopped", "error", "saved", "continued")


class TrainingManager:
    def __init__(self, publish: Callable[[dict], None]) -> None:
        self.publish = publish
        self.processes: dict[str, subprocess.Popen] = {}
        self.tests: dict[str, subprocess.Popen] = {}
        self.offsets: dict[str, int] = {}
        self.latest: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="training-watch", daemon=True)

    def start_watching(self) -> None:
        for run in list_runs():
            self._prime(run)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    # Starting and stopping.

    def start(self, name: str, setup: TrainingSetup, profile: RobotProfile, device: str = "auto") -> Run:
        run = create_run(name, setup, profile)
        self._spawn(run, device)
        return run

    def resume(self, name: str, device: str = "auto") -> Run:
        run = Run(paths.RUNS / name)
        if not run.path("setup.json").exists():
            raise FileNotFoundError(f"No run named {name}")
        if self.running(name):
            raise RuntimeError(f"{name} is already training")
        run.path(STOP).unlink(missing_ok=True)
        self._spawn(run, device)
        return run

    def _spawn(self, run: Run, device: str) -> None:
        log = run.path(LOG)
        with self._lock:
            self.offsets[run.name] = log.stat().st_size if log.exists() else 0
        out = open(log, "ab")  # noqa: SIM115 - the child process owns it.
        err = open(run.path("train.err"), "ab")  # noqa: SIM115
        # Training outlives the server: closing its window (Windows) or terminal (a hangup
        # on Linux and macOS) must not stop a run. The next server picks it up again.
        detach: dict = {"start_new_session": True}
        if sys.platform == "win32":
            detach = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(
            [sys.executable, "-m", "isla_rl.learn", "train", str(run.folder), "--device", device],
            stdout=out,
            stderr=err,
            cwd=str(paths.ROOT),
            env=env,
            **detach,
        )
        self.processes[run.name] = process
        self.publish({"type": "train.state", "run": run.name, "state": "running"})

    # Testing a policy in full physics.

    def evaluate(self, name: str) -> None:
        """Test the run's policy as it is now, in the background; a message says when it is done."""
        run = Run(paths.RUNS / name)
        if not run.path("policy.json").exists():
            raise FileNotFoundError(f"{name} has no policy yet: it saves one after its first round of practice")
        with self._lock:
            if self.evaluating(name):
                raise RuntimeError(f"{name} is being tested already")
            log = open(run.path(TEST_LOG), "wb")  # noqa: SIM115 - closed when the test ends.
            hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            process = subprocess.Popen(
                [sys.executable, "-m", "isla_rl.learn", "evaluate", str(run.folder)],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(paths.ROOT),
                **hidden,
            )
            self.tests[name] = process
        self.publish({"type": "evaluation", "run": name, "state": "running"})
        threading.Thread(target=self._tested, args=(run, process, log), name=f"test-{name}", daemon=True).start()

    def evaluating(self, name: str) -> bool:
        process = self.tests.get(name)
        return process is not None and process.poll() is None

    def _tested(self, run: Run, process: subprocess.Popen, log) -> None:
        code = process.wait()
        log.close()
        if code == 0:
            self.publish({"type": "evaluation", "run": run.name, "state": "done"})
            return
        lines = run.path(TEST_LOG).read_text(encoding="utf-8", errors="replace").strip().splitlines()
        self.publish({"type": "evaluation", "run": run.name, "state": "error", "message": lines[-1] if lines else f"The test stopped with code {code}"})

    def stop(self, name: str) -> None:
        run = Run(paths.RUNS / name)
        run.path(STOP).write_text("stop\n", encoding="utf-8")
        self.publish({"type": "train.state", "run": name, "state": "stopping"})

    def running(self, name: str) -> bool:
        process = self.processes.get(name)
        if process is not None and process.poll() is None:
            return True
        run = Run(paths.RUNS / name)
        status = run.status()
        if status.get("state") == "running":
            return True
        if status.get("state") in ("finished", "stopped") and "updated" in status:
            return False  # Its process said it ended.
        log = run.path(LOG)  # A run without a heartbeat: judge by its log.
        return log.exists() and time.time() - log.stat().st_mtime < 45 and not run.path(STOP).exists()

    # Following the logs.

    def _prime(self, run: Run) -> None:
        """Read the end of an existing log so the latest reports are known at once."""
        log = run.path(LOG)
        if not log.exists():
            return
        size = log.stat().st_size
        with log.open("rb") as file:
            file.seek(max(0, size - TAIL_START))
            chunk = file.read()
        for line in chunk.splitlines()[1:] if size > TAIL_START else chunk.splitlines():
            self._take(run.name, line, publish=False)
        with self._lock:
            self.offsets[run.name] = size

    def _watch(self) -> None:
        while not self._stop.is_set():
            for run in list_runs():
                log = run.path(LOG)
                if not log.exists():
                    continue
                with self._lock:
                    offset = self.offsets.get(run.name)
                if offset is None:
                    self._prime(run)
                    continue
                size = log.stat().st_size
                if size < offset:
                    offset = 0
                if size == offset:
                    continue
                with log.open("rb") as file:
                    file.seek(offset)
                    chunk = file.read(size - offset)
                end = chunk.rfind(b"\n")
                if end < 0:
                    continue
                with self._lock:
                    self.offsets[run.name] = offset + end + 1
                for line in chunk[: end + 1].splitlines():
                    self._take(run.name, line, publish=True)
            for name, process in list(self.processes.items()):
                if process.poll() is not None:
                    del self.processes[name]
                    self.publish({"type": "train.state", "run": name, "state": "ended", "code": process.returncode})
            time.sleep(0.4)

    def _take(self, name: str, line: bytes, publish: bool) -> None:
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        kind = message.get("type")
        if kind not in KEEP:
            return
        with self._lock:
            self.latest.setdefault(name, {})[kind] = message
        if publish:
            self.publish({"type": "train", "run": name, "message": message})

    def snapshot(self, name: str) -> dict[str, dict]:
        with self._lock:
            return dict(self.latest.get(name, {}))

    def runs(self) -> list[dict]:
        out = []
        for run in list_runs():
            try:
                summary = run.summary()
            except (ValueError, OSError):
                continue
            summary["running"] = self.running(run.name)
            summary["evaluated"] = run.path(EVALUATION).exists()
            summary["evaluating"] = self.evaluating(run.name)
            latest = self.snapshot(run.name).get("progress")
            if latest:
                summary["steps"] = latest.get("steps", summary["steps"])
                summary["score"] = latest.get("score", summary["score"])
                summary["phase"] = latest.get("phase")
            out.append(summary)
        return out


def run_folder(name: str) -> Path:
    return paths.RUNS / name
