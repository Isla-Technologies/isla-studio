"""Training runs: practise, learn, search the design, keep checkpoints, report progress.

A run lives in its own folder under ``runs/``:

- ``setup.json``: what the robot learns, and how;
- ``robot.json``: the robot's profile as it was when the run began;
- ``progress.csv``: one row per learning round;
- ``codesign.json``: the design search, one entry per update (co-design runs);
- ``checkpoint.npz``: everything needed to continue after a stop;
- ``policy.json``: the latest policy, ready to play or deploy;
- ``stop``: create it to stop the run gracefully.

The web app starts a run as a separate process (``python -m isla_rl.learn train
runs/<name>``) and reads its progress: one JSON object per line on stdout.
"""

from __future__ import annotations

import base64
import csv
import json
import math
import os
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import paths
from ..robot.profile import RobotProfile
from .device import AUTO, GPU, choose, gpu_name, to_numpy
from .env import DT, PracticeRobots
from .policy import Policy
from .ppo import PPO, Rollout
from .skills import REWARDS, TrainingSetup

SETUP, ROBOT, PROGRESS, CHECKPOINT, POLICY, CODESIGN, STOP, STATUS, LOG = (
    "setup.json", "robot.json", "progress.csv", "checkpoint.npz", "policy.json", "codesign.json", "stop",
    "status.json", "train.log",
)
SAVE_SECONDS = 30.0
WINDOW = 400  # Tries averaged in progress reports.
SWARM = 16  # Practice robots shown while training.
COLUMNS = (
    "iteration", "steps", "seconds", "rate", "score", "length", "falls", "speed_error",
    "impact", "power", "std", "learning_rate", "value_loss", "device", "phase",
)


@dataclass(frozen=True, slots=True)
class Run:
    folder: Path

    @property
    def name(self) -> str:
        return self.folder.name

    def path(self, name: str) -> Path:
        return self.folder / name

    @property
    def setup(self) -> TrainingSetup:
        return TrainingSetup.from_json(self.path(SETUP).read_text(encoding="utf-8"))

    def profile(self) -> RobotProfile:
        """The robot as it was when the run began (its model files stay in place)."""
        snapshot = self.path(ROBOT)
        if snapshot.exists():
            data = json.loads(snapshot.read_text(encoding="utf-8"))
            saved = data.pop("_folder", None)  # Absent in the examples: they use the hangar's copy.
            folder = Path(saved) if saved else None
            if folder is None or not folder.exists():
                folder = paths.ROBOTS / data.get("id", self.setup.robot)
            profile = RobotProfile(folder, data)
            profile.validate()
            return profile
        return RobotProfile.load(self.setup.robot)

    @property
    def started(self) -> bool:
        return self.path(CHECKPOINT).exists()

    def policy(self) -> Policy | None:
        path = self.path(POLICY)
        return Policy.load(path) if path.exists() else None

    def progress(self) -> list[dict]:
        path = self.path(PROGRESS)
        if not path.exists():
            return []
        rows = []
        with path.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                try:
                    rows.append(
                        {
                            k: (v if k in ("device", "phase") else float(v))
                            for k, v in row.items()
                            if k is not None and v not in (None, "")
                        }
                    )
                except (TypeError, ValueError):
                    continue
        return rows

    def codesign(self) -> list[dict]:
        path = self.path(CODESIGN)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def status(self) -> dict:
        """What the run's process last said about itself: running, finished, stopped."""
        path = self.path(STATUS)
        if not path.exists():
            return {"state": "new" if not self.started else "stopped"}
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"state": "unknown"}
        if status.get("state") == "running" and time.time() - float(status.get("updated", 0)) > 60:
            status["state"] = "stopped"  # Its process ended without saying so.
        return status

    def summary(self) -> dict:
        setup = self.setup
        progress = self.progress()
        last = progress[-1] if progress else {}
        policy_path = self.path(POLICY)
        return {
            "name": self.name,
            "robot": setup.robot,
            "skill": setup.skill,
            "codesign": setup.codesign.enabled,
            "robots": setup.robots,
            "total": setup.total_steps,
            "steps": int(last.get("steps", 0)),
            "score": last.get("score"),
            "seconds": last.get("seconds", 0),
            "device": last.get("device"),
            "has_policy": policy_path.exists(),
            "status": self.status().get("state"),
            "modified": max(
                (p.stat().st_mtime for p in self.folder.iterdir() if p.is_file()), default=0
            ),
        }


def list_runs() -> list[Run]:
    if not paths.RUNS.exists():
        return []
    runs = [Run(f) for f in paths.RUNS.iterdir() if (f / SETUP).exists()]
    return sorted(runs, key=lambda r: r.path(SETUP).stat().st_mtime, reverse=True)


def create_run(name: str, setup: TrainingSetup, profile: RobotProfile) -> Run:
    folder = paths.RUNS / _folder_name(name)
    folder.mkdir(parents=True, exist_ok=False)
    run = Run(folder)
    _write(run.path(SETUP), setup.to_json())
    snapshot = {**profile.data, "_folder": str(profile.folder)}
    _write(run.path(ROBOT), json.dumps(snapshot, indent=2) + "\n")
    return run


def _folder_name(name: str) -> str:
    plain = "".join(c if c.isalnum() or c in "-_." else "-" for c in name.strip()).strip(".-")
    return (plain or "run")[:64]


def train(
    run: Run,
    report: Callable[[dict], None] = lambda message: None,
    should_stop: Callable[[], bool] = lambda: False,
    iterations: int | None = None,
    device: str = AUTO,
    threads: int | None = None,
    swarm: bool = True,
) -> None:
    """Practise and learn until the setup's steps are done, a stop, or ``iterations``."""
    setup = run.setup
    device = choose(device)
    where = gpu_name() if device == GPU else f"this computer's processor ({os.cpu_count()} threads)"
    report({"type": "preparing", "device": device, "name": where})
    robots = PracticeRobots(run.profile(), setup, device, threads)
    try:
        _train(run, setup, robots, report, should_stop, iterations, swarm)
    finally:
        robots.close()


def _train(run, setup: TrainingSetup, robots: PracticeRobots, report, should_stop, iterations, swarm) -> None:
    settings = setup.settings
    ppo = PPO(robots.obs_size, robots.critic_size, len(robots.joints), settings, setup.seed, robots.device)
    steps, iteration, elapsed = 0, 0, 0.0
    checkpoint = run.path(CHECKPOINT)
    pool = robots.pool
    if checkpoint.exists():
        with np.load(checkpoint) as saved:
            state = {key: saved[key] for key in saved.files}
        ppo.load_state(state)
        pool.load_state(state)
        steps, iteration, elapsed = int(state["steps"]), int(state["iteration"]), float(state["seconds"])
        report({"type": "continued", "iteration": iteration, "steps": steps})
    total = setup.total_steps
    names = [*robots.reward_terms, "fall"]
    report(
        {
            "type": "started",
            "total": total,
            "robots": robots.count,
            "rewards": {k: REWARDS[k].label for k in names},
            "device": robots.device,
            "joints": list(robots.joints),
            "codesign": pool.searching,
            "design_keys": robots.space.keys,
            "nq": robots.nq,
        }
    )
    window = {k: deque(maxlen=WINDOW) for k in ("score", "length", "falls", "speed_error", "impact", "power")}
    terms = deque(maxlen=WINDOW)
    count, T = robots.count, setup.rollout
    rollout = Rollout(T, count, robots.obs_size, robots.critic_size, len(robots.joints), robots.xp)
    obs, critic = robots.reset()
    begun = time.monotonic() - elapsed
    saved_at = time.monotonic()
    done_iterations = 0
    search = setup.codesign
    total_iterations = max(1, math.ceil(total / (T * count)))
    search_iterations = max(1, int(total_iterations * (1.0 - search.warmup - search.finetune)))
    update_every = max(2, search_iterations // max(search.updates, 1))
    xp = robots.xp
    watch = xp.arange(min(SWARM, count))
    _status(run, "running", iteration, steps)
    while steps < total and not should_stop():
        started = time.monotonic()
        robots.progress = steps / total  # Curricula (such as a top speed that opens up) follow it.
        frames = [] if swarm else None
        for step in range(T):
            scaled, scaled_critic = ppo.normalize(obs, critic)
            actions, log_probs, means = ppo.act(scaled)
            values = ppo.value(scaled_critic)
            obs, critic, rewards, dones, timeouts, episodes = robots.step(actions)
            rewards = rewards + settings.gamma * values * timeouts
            rollout.obs[step], rollout.critic_obs[step] = scaled, scaled_critic
            rollout.actions[step], rollout.log_probs[step] = actions, log_probs
            rollout.means[step], rollout.values[step] = means, values
            rollout.rewards[step], rollout.dones[step] = rewards, dones
            if frames is not None and step % 2 == 0:
                frames.append(robots.physics.qpos[watch])
            if len(episodes.returns):
                window["score"].extend(episodes.returns)
                window["length"].extend(episodes.seconds)
                window["falls"].extend(episodes.falls)
                window["speed_error"].extend(episodes.speed_errors)
                window["impact"].extend(episodes.impact)
                window["power"].extend(episodes.power)
                terms.extend(episodes.terms)
        ppo.finish(rollout, ppo.critic_normalizer(critic))
        stats = ppo.update(rollout)
        iteration += 1
        done_iterations += 1
        steps += T * count
        now = time.monotonic()
        progress = steps / total
        row = {
            "iteration": iteration,
            "steps": steps,
            "seconds": round(now - begun, 2),
            "rate": round(T * count / max(now - started, 1e-9)),
            "score": _mean(window["score"]),
            "length": _mean(window["length"]),
            "falls": _mean(window["falls"]),
            "speed_error": _mean(window["speed_error"]),
            "impact": _mean(window["impact"]),
            "power": _mean(window["power"]),
            "std": round(stats["std"], 4),
            "learning_rate": stats["learning_rate"],
            "value_loss": round(stats["value"], 5),
            "device": robots.device,
            "phase": pool.phase,
        }
        _append(run.path(PROGRESS), row)
        points = np.mean(np.array(terms), axis=0) if terms else np.zeros(len(names))
        report({"type": "progress", **row, "total": total, "rewards": {k: round(float(v), 4) for k, v in zip(names, points, strict=True)}})
        if frames:
            stack = to_numpy(xp.stack(frames)).astype(np.float32)  # (time, robots, nq)
            design = to_numpy(robots.design_vectors[watch]).round(4).tolist() if robots.space.size else []
            report(
                {
                    "type": "swarm",
                    "shape": list(stack.shape),
                    "dt": 2 * DT,
                    "qpos": base64.b64encode(stack.tobytes()).decode("ascii"),
                    "designs": design,
                }
            )

        # The design search: new candidates on a schedule, the distribution moving in search.
        if pool.searching:
            if pool.advance(progress):
                robots.pool_changed()
                report({"type": "codesign", **pool.report()})
            elif pool.phase in ("warmup", "search") and iteration % update_every == 0:
                points, seconds = robots.design_scores()
                if pool.phase == "search":
                    update = pool.update(points, seconds)
                    if update is not None:
                        history = run.codesign()
                        history.append({"iteration": iteration, "steps": steps, **_compact(update)})
                        _write(run.path(CODESIGN), json.dumps(history) + "\n")
                        report({"type": "codesign", "iteration": iteration, **update})
                        robots.pool_changed()
                else:
                    report({"type": "codesign", "iteration": iteration, **pool.report(pool.fitness(points, seconds), seconds)})
                    pool.refill()
                    robots.pool_changed()

        if now - saved_at >= SAVE_SECONDS:
            _save(run, robots, ppo, iteration, steps, now - begun, row)
            saved_at = now
            report({"type": "saved", "iteration": iteration})
        if iteration % 5 == 0:
            _status(run, "running", iteration, steps)
        if iterations is not None and done_iterations >= iterations:
            break
    _save(run, robots, ppo, iteration, steps, time.monotonic() - begun, row if done_iterations else {})
    final = {"type": "finished" if steps >= total else "stopped", "steps": steps}
    _status(run, final["type"], iteration, steps)
    if pool.searching:
        final["design"] = pool.chosen()
        final["codesign"] = pool.report()
    report(final)


def _compact(update: dict) -> dict:
    """A design update without the per-candidate table, for the history file."""
    return {k: v for k, v in update.items() if k != "candidates"} | {
        "candidates": [[*c["design"], c["score"], c["seconds"]] for c in update["candidates"]]
    }


def _status(run: Run, state: str, iteration: int, steps: int) -> None:
    document = {"state": state, "pid": os.getpid(), "updated": time.time(), "iteration": iteration, "steps": steps}
    _write(run.path(STATUS), json.dumps(document) + "\n")


def _mean(values) -> float:
    return round(float(np.mean(values)), 4) if len(values) else math.nan


def _save(run: Run, robots: PracticeRobots, ppo: PPO, iteration: int, steps: int, seconds: float, row: dict) -> None:
    state = {
        **{key: to_numpy(value) for key, value in ppo.state().items()},
        **robots.pool.state(),
        "iteration": np.array(iteration),
        "steps": np.array(steps),
        "seconds": np.array(seconds),
    }
    checkpoint = run.path(CHECKPOINT)
    temporary = checkpoint.with_name(f".{checkpoint.name}.tmp.npz")
    np.savez(temporary, **state)
    os.replace(temporary, checkpoint)
    minutes = seconds / 60.0
    about = (
        f"Trained to {robots.skill.title.lower()} for {steps / 1e6:.1f} million moves "
        f"({minutes:.0f} min) in run {run.name}."
    )
    stats = {k: row.get(k) for k in ("score", "length", "falls", "speed_error", "impact", "power") if k in row}
    policy = robots.policy(ppo, run.name, about, stats)
    _write(run.path(POLICY), policy.to_json())


def _append(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)


def _write(path: Path, text: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def delete_run(run: Run) -> None:
    shutil.rmtree(run.folder)
