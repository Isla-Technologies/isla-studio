"""Choosing the hardware while learning to control it.

The search keeps a distribution over the design values it may change (here: the
carbon-fibre rod lengths and the shock absorbers' springs, damping and preload),
as a mean and a spread per value, each scaled to its range. From it it draws a
pool of candidate designs. Every practice robot takes one candidate when it starts
a try, and the policy senses which, so a single network learns to drive them all.

Each candidate's score is the average return of the tries made on it. Every few
rounds the best quarter (the elite) pulls the distribution toward itself and the
spread narrows: the cross-entropy method. Tries begun before a pool was replaced
are not credited to the new pool. Once the search ends, the design is fixed at the
distribution's mean, snapped to what a workshop can make, and the policy practises
on it alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np
from numpy.typing import NDArray

from ..robot.build import BuildInfo
from ..robot.design import DesignSpace, design_metrics
from ..robot.profile import RobotProfile
from .skills import Codesign

SEARCH, WARMUP, FINETUNE, FIXED = "search", "warmup", "finetune", "fixed"


@dataclass
class Candidate:
    vector: NDArray  # Physical units, every design value.
    units: NDArray  # Scaled to 0..1.
    qpos: NDArray
    ctrl: NDArray
    hip_height: float
    gravity: NDArray
    metrics: dict


class DesignPool:
    """The candidate designs practice robots draw from, and the search over them."""

    def __init__(
        self,
        profile: RobotProfile,
        model: mujoco.MjModel,
        info: BuildInfo,
        space: DesignSpace,
        cad: dict,
        settings: Codesign,
        fixed: dict[str, float],
        seed: int = 1,
    ) -> None:
        self.profile, self.model, self.info, self.space, self.cad = profile, model, info, space, cad
        self.settings = settings
        self.rng = np.random.default_rng(seed + 7919)
        self.base_units = space.normalize(space.vector(fixed))
        if settings.enabled and space.size:
            wanted = settings.params or tuple(space.keys)
            self.search = np.array([space.keys.index(k) for k in wanted if k in space.keys], dtype=int)
        else:
            self.search = np.zeros(0, dtype=int)
        self.mean = np.full(len(self.search), 0.5)
        self.std = np.full(len(self.search), settings.init_std)
        self.phase = WARMUP if len(self.search) else FIXED
        self.generation = 0
        self.history: list[dict] = []
        self.candidates: list[Candidate] = []
        self.min_seconds = 20.0  # Robot-seconds a candidate needs before it is judged.
        self.refill()

    @property
    def searching(self) -> bool:
        return len(self.search) > 0

    @property
    def size(self) -> int:
        return len(self.candidates)

    # Drawing candidates.

    def _units(self, count: int) -> NDArray:
        units = np.repeat(self.base_units[None], count, axis=0)
        if len(self.search) and self.phase in (WARMUP, SEARCH):
            draws = self.mean + self.std * self.rng.standard_normal((count, len(self.search)))
            draws[0] = self.mean  # The mean itself always takes part.
            units[:, self.search] = np.clip(draws, 0.0, 1.0)
        elif len(self.search):
            units[:, self.search] = np.clip(self.mean, 0.0, 1.0)
        return units

    def refill(self) -> None:
        """Draw a new pool from the distribution (or the chosen design, once fixed)."""
        count = self.settings.pool if self.phase in (WARMUP, SEARCH) else 1
        units = self._units(count)
        vectors = self.space.denormalize(units)
        if self.phase in (FINETUNE, FIXED):
            vectors = np.array([self.snap(v) for v in vectors])
            units = self.space.normalize(vectors)
        self.candidates = []
        for vector, unit in zip(vectors, units, strict=True):
            pose, metrics = design_metrics(self.profile, self.model, self.info, self.space, vector, self.cad)
            self.candidates.append(
                Candidate(vector, unit, pose.qpos, pose.ctrl, pose.hip_height, pose.gravity, metrics)
            )
        self.generation += 1

    def snap(self, vector: NDArray) -> NDArray:
        params = self.profile.design
        return np.array([params[k].snap(v) for k, v in zip(self.space.keys, vector, strict=True)])

    def pick(self, count: int) -> NDArray:
        """Candidate indices for robots starting tries: spread evenly over the pool."""
        return self.rng.integers(0, len(self.candidates), count)

    # Scoring.

    def fitness(self, points: NDArray, seconds: NDArray) -> NDArray:
        """Each candidate's score: points earned per second of robot time on it.

        Every robot adds its points to its candidate on every move, finished try or
        not, so a design is not judged by its quickest falls. Candidates with less
        than ``min_seconds`` of practice are not judged. A target height, if set,
        costs ``height_weight`` points per second per metre off it.
        """
        seconds = np.asarray(seconds, float)
        rate = np.where(seconds >= self.min_seconds, np.asarray(points, float) / np.maximum(seconds, 1e-9), -np.inf)
        target = self.settings.target_height
        if target is not None:
            heights = np.array([c.metrics["height"] for c in self.candidates])
            rate = rate - self.settings.height_weight * np.abs(heights - target)
        return rate

    # Moving the distribution.

    def update(self, points: NDArray, seconds: NDArray) -> dict | None:
        """Pull the distribution toward the elite and draw a new pool."""
        if self.phase != SEARCH or not len(self.search):
            return None
        scores = self.fitness(points, seconds)
        judged = np.flatnonzero(np.isfinite(scores))
        if len(judged) < max(4, len(self.candidates) // 4):
            return None  # Not enough practice yet: keep gathering.
        order = judged[np.argsort(scores[judged])[::-1]]
        elite = order[: max(2, int(round(len(order) * self.settings.elite)))]
        units = np.array([self.candidates[i].units[self.search] for i in elite])
        a = self.settings.smoothing
        self.mean = (1 - a) * self.mean + a * units.mean(axis=0)
        self.std = np.maximum((1 - a) * self.std + a * units.std(axis=0), self.settings.min_std)
        report = self.report(scores, seconds)
        self.history.append(report)
        self.refill()
        return report

    def advance(self, progress: float) -> bool:
        """Move between warm-up, search and fine-tuning as practice goes on.

        Returns True when the pool changed."""
        if not len(self.search):
            return False
        s = self.settings
        if self.phase == WARMUP and progress >= s.warmup:
            self.phase = SEARCH
            return False
        if self.phase == SEARCH and progress >= 1.0 - s.finetune:
            self.phase = FINETUNE
            self.refill()
            return True
        return False

    def chosen(self) -> dict[str, float]:
        """The design the search has settled on, snapped to workshop steps."""
        units = self.base_units.copy()
        if len(self.search):
            units[self.search] = np.clip(self.mean, 0, 1)
        return self.space.as_dict(self.snap(self.space.denormalize(units)))

    def report(self, scores: NDArray | None = None, seconds: NDArray | None = None) -> dict:
        if scores is None:
            scores = np.full(len(self.candidates), -np.inf)
        if seconds is None:
            seconds = np.zeros(len(self.candidates))
        keys = self.space.keys
        finite = np.isfinite(scores)
        best = int(np.argmax(np.where(finite, scores, -np.inf))) if finite.any() else 0
        chosen = self.chosen()
        _, chosen_metrics = design_metrics(
            self.profile, self.model, self.info, self.space, self.space.vector(chosen), self.cad
        )
        return {
            "phase": self.phase,
            "generation": self.generation,
            "searched": [keys[i] for i in self.search],
            "mean": {keys[i]: float(self.space.denormalize(self._full(self.mean))[i]) for i in self.search},
            "spread": {keys[i]: float(s) for i, s in zip(self.search, self.std, strict=True)},
            "chosen": chosen,
            "chosen_metrics": chosen_metrics,
            "best": {
                "design": self.space.as_dict(self.candidates[best].vector),
                "score": float(scores[best]) if finite.any() else None,
                "metrics": self.candidates[best].metrics,
            },
            "candidates": [
                {
                    "design": [round(float(v), 4) for v in c.vector],
                    "score": None if not math.isfinite(float(sc)) else round(float(sc), 3),
                    "seconds": round(float(n), 1),
                    "height": round(float(c.metrics["height"]), 4),
                }
                for c, sc, n in zip(self.candidates, scores, seconds, strict=True)
            ],
            "keys": keys,
        }

    def _full(self, searched_units: NDArray) -> NDArray:
        units = self.base_units.copy()
        units[self.search] = np.clip(searched_units, 0, 1)
        return units

    # Keeping it with the checkpoint.

    def state(self) -> dict[str, NDArray]:
        return {
            "codesign.mean": self.mean,
            "codesign.std": self.std,
            "codesign.phase": np.array({WARMUP: 0, SEARCH: 1, FINETUNE: 2, FIXED: 3}[self.phase]),
        }

    def load_state(self, state: dict) -> None:
        if "codesign.mean" not in state or not len(self.search):
            return
        mean = np.asarray(state["codesign.mean"], float)
        if mean.shape != self.mean.shape:
            return
        self.mean = mean
        self.std = np.asarray(state["codesign.std"], float)
        self.phase = {0: WARMUP, 1: SEARCH, 2: FINETUNE, 3: FIXED}[int(state["codesign.phase"])]
        self.refill()
