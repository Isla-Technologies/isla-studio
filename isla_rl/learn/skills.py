"""The skills a robot can learn, the rewards that teach each, and a training setup.

A reward scores one kind of behaviour: positive weights reward it, negative
weights penalise it. Weights are points per second, as legged-robot teams quote
them; the fall penalty is points per fall. Each skill starts from weights that
work (Unitree's, ETH's and Astra's published setups) and each can be changed.

Co-design: when the robot has hardware the search may change (rod lengths,
shock absorbers), training can choose it too. See :mod:`isla_rl.learn.codesign`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields

from .policy import Commands, Gait
from .ppo import PPOSettings


@dataclass(frozen=True, slots=True)
class Reward:
    key: str
    label: str
    help: str


REWARDS = {
    r.key: r
    for r in (
        Reward("alive", "Stay up", "Points for every moment it has not fallen."),
        Reward("track_speed", "Walk at the speed asked", "Points for moving forward and sideways at the commanded speed."),
        Reward("track_turn", "Turn at the rate asked", "Points for turning at the commanded rate."),
        Reward("feet_phase", "Step in rhythm", "Points when each foot is down or up as the gait clock says."),
        Reward("upright", "Keep the body's lean", "Penalty for the body tilting away from its standing lean."),
        Reward("height", "Keep the hips high", "Penalty for the hips sagging below standing height."),
        Reward("vertical_speed", "No bouncing", "Penalty for the body moving up and down."),
        Reward("wobble", "No rocking", "Penalty for the body rolling or pitching."),
        Reward("stay", "Stay in place", "Penalty for drifting across the floor."),
        Reward("pose", "Stay near the standing pose", "Penalty for joints far from standing."),
        Reward("hip_pose", "Keep the legs straight ahead", "Penalty for hips turning or splaying sideways."),
        Reward("waist_pose", "Keep the upper body still", "Penalty for the waist turning."),
        Reward("swing_height", "Lift the feet", "Penalty for a swinging foot far from the lift height: no shuffling."),
        Reward("feet_slip", "No sliding feet", "Penalty for a foot moving while it is down."),
        Reward("smooth", "Move smoothly", "Penalty for actions changing quickly: no jitter."),
        Reward("joint_accel", "Gentle joints", "Penalty for joints speeding up or slowing hard."),
        Reward("joint_speed", "Unhurried joints", "Penalty for fast joints."),
        Reward("torque", "Save effort", "Penalty for motor torque as a share of each motor's peak."),
        Reward("power", "Save energy", "Penalty for mechanical power: battery life."),
        Reward("joint_limits", "Keep clear of joint limits", "Penalty for joints beyond 90% of their range."),
        Reward("impact", "Land softly", "Penalty for a foot striking the floor harder than 1.3 times the robot's weight."),
        Reward("strut_limit", "Keep the shocks off their stops", "Penalty for a shock absorber at the end of its stroke."),
        Reward("jump_up", "Spring up", "Points for rising fast as a jump takes off."),
        Reward("airborne", "Leave the floor", "Points for both feet in the air in the middle of a jump."),
        Reward("jump_height", "Jump high", "Points for the hips rising above standing height in the middle of a jump."),
        Reward("grounded", "Stay down between jumps", "Penalty for a foot leaving the floor when no jump is asked."),
        Reward("fall", "Falling", "Penalty for each fall, in points per fall."),
    )
}


@dataclass(frozen=True, slots=True)
class Jumping:
    """When jumps are asked for in practice, and how long one takes."""

    seconds: float = 1.0  # From the command to standing again: crouch, take-off, flight, landing.
    first: tuple[float, float] = (0.6, 2.0)  # s into a try before the first jump.
    every: tuple[float, float] = (1.2, 2.6)  # s standing between one jump and the next.


@dataclass(frozen=True, slots=True)
class Skill:
    key: str
    title: str
    summary: str
    detail: str
    terms: tuple[str, ...]
    weights: Mapping[str, float]
    steps: int
    push_speed: float
    push_every: tuple[float, float]
    init_std: float = 1.0
    gait: Gait | None = None
    episode: float = 20.0
    commands: Commands = Commands()  # The speeds asked for, unless a setup says otherwise.
    curriculum: tuple[float, float] | None = None  # Top forward speed at the start, share of practice to reach the full range.
    run_practice: float = 0.0  # Share of commands drawn near the top speed reached so far.
    impact: tuple[float, float] = (1.3, 1.3)  # Weights of foot force that count as a hard strike: walking, running.
    jumping: Jumping | None = None


_COMMON = ("gyro", "gravity", "joints", "speeds", "actions")

SKILLS = {
    s.key: s
    for s in (
        Skill(
            key="stand",
            title="Stand and recover",
            summary="Keep standing while being pushed, stepping if it must.",
            detail=(
                "The quickest skill to learn and the best first lesson. Robots are pushed from "
                "every side and learn to catch themselves with hips, knees and steps."
            ),
            terms=_COMMON,
            weights={
                "alive": 1.0,
                "upright": -5.0,
                "height": -10.0,
                "vertical_speed": -2.0,
                "wobble": -0.05,
                "stay": -0.5,
                "pose": -0.5,
                "feet_slip": -0.1,
                "smooth": -0.01,
                "joint_accel": -2.5e-7,
                "torque": -0.05,
                "joint_limits": -5.0,
                "impact": -0.5,
                "strut_limit": -0.5,
                "fall": -2.0,
            },
            steps=30_000_000,
            push_speed=0.5,
            push_every=(2.0, 5.0),
            init_std=0.8,
            episode=12.0,
        ),
        Skill(
            key="walk",
            title="Walk",
            summary="Walk forward, backward, sideways and turn at the speed asked.",
            detail=(
                "What humanoid teams train first: the robot follows a speed command, as from a "
                "joystick, with a gait clock that swings each leg in turn. On a laptop GPU it "
                "takes around an hour."
            ),
            terms=(*_COMMON[:2], "command", *_COMMON[2:], "phase"),
            # Steadiness weighs more than in walk and run: rocking, leaning and bouncing each
            # cost a few percent of the speed reward instead of almost nothing.
            weights={
                "track_speed": 1.0,
                "track_turn": 0.5,
                "alive": 0.15,
                "feet_phase": 0.18,
                "upright": -2.0,
                "height": -10.0,
                "vertical_speed": -4.0,
                "wobble": -0.5,
                "hip_pose": -1.0,
                "waist_pose": -2.0,
                "swing_height": -20.0,
                "feet_slip": -0.2,
                "smooth": -0.01,
                "joint_accel": -2.5e-7,
                "joint_speed": -1e-3,
                "torque": -0.05,
                "joint_limits": -5.0,
                "impact": -0.5,
                "strut_limit": -0.5,
                "fall": -2.0,
            },
            steps=100_000_000,
            push_speed=0.4,
            push_every=(5.0, 10.0),
            init_std=0.8,
            gait=Gait(period=(0.7, 0.7), stance=(0.55, 0.55), swing_height=(0.04, 0.04)),
            episode=16.0,
        ),
        Skill(
            key="move",
            title="Walk and run",
            summary="Walk, run, side-step and turn at the speed asked, in any direction.",
            detail=(
                "One policy for moving around: as the commanded speed rises past 0.5 m/s the gait "
                "clock shortens the stride and the time each foot is down, until above 1 m/s both "
                "feet leave the floor between steps, which is running. Top speeds open up as the "
                "robot learns. On a laptop GPU it takes two to three hours."
            ),
            terms=(*_COMMON[:2], "command", *_COMMON[2:], "phase"),
            weights={
                "track_speed": 1.5,
                "track_turn": 0.6,
                "alive": 0.15,
                "feet_phase": 0.25,
                "upright": -1.0,
                "height": -10.0,
                "vertical_speed": -2.0,
                "wobble": -0.05,
                "hip_pose": -1.0,
                "waist_pose": -2.0,
                "swing_height": -20.0,
                "feet_slip": -0.2,
                "smooth": -0.01,
                "joint_accel": -2.5e-7,
                "joint_speed": -5e-4,
                "torque": -0.03,
                "joint_limits": -5.0,
                "impact": -0.5,
                "strut_limit": -0.5,
                "fall": -2.0,
            },
            steps=400_000_000,
            push_speed=0.4,
            push_every=(5.0, 10.0),
            init_std=0.8,
            gait=Gait(period=(0.7, 0.52), stance=(0.55, 0.38), swing_height=(0.04, 0.08), speeds=(0.5, 1.0)),
            episode=20.0,
            commands=Commands(forward=(-0.4, 1.5), sideways=0.3, turning=1.0),
            curriculum=(0.6, 0.5),
            run_practice=0.35,
            impact=(1.3, 2.6),
        ),
        Skill(
            key="jump",
            title="Jump",
            summary="Jump on command and land standing.",
            detail=(
                "The robot stands until a jump is asked for, then crouches, springs off both feet, "
                "and lands back on its feet and its shock absorbers. Practice asks for a jump every "
                "few seconds. On a laptop GPU it takes about an hour."
            ),
            terms=(*_COMMON[:2], "jump", *_COMMON[2:]),
            weights={
                "alive": 0.5,
                "jump_up": 6.0,
                "airborne": 4.0,
                "jump_height": 40.0,
                "grounded": -4.0,  # Feet stay down between jumps: no hopping in place.
                "upright": -2.0,
                "height": -10.0,
                "vertical_speed": -1.0,
                "wobble": -0.05,
                "stay": -0.5,
                "pose": -0.3,
                "hip_pose": -1.0,
                "waist_pose": -2.0,  # Without these it learned to wind its waist and hips up.
                "feet_slip": -0.1,
                "smooth": -0.03,
                "joint_accel": -2.5e-7,
                "joint_speed": -5e-4,
                "torque": -0.03,
                "joint_limits": -5.0,
                "impact": -0.3,
                "strut_limit": -1.0,  # Land on the legs, not on the shock absorbers' end stops.
                "fall": -10.0,
            },
            steps=200_000_000,
            push_speed=0.2,
            push_every=(4.0, 8.0),
            init_std=0.8,
            episode=12.0,
            impact=(3.0, 3.0),
            jumping=Jumping(),
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Robustness:
    """Differences between practice robots, so the policy copes with the real one."""

    friction: tuple[float, float] = (0.5, 1.5)
    added_mass: tuple[float, float] = (-1.0, 2.5)  # kg at the torso: the virtual mass's uncertainty.
    strength: tuple[float, float] = (0.9, 1.1)  # Servo stiffness and damping scale.
    strut_spread: float = 0.1  # Each shock's spring and damping vary by this share.
    push_speed: float | None = None
    push_every: tuple[float, float] | None = None
    noise: float = 1.0
    delay: float = 0.01  # s

    def __post_init__(self) -> None:
        for name in ("friction", "added_mass", "strength"):
            low, high = getattr(self, name)
            if not (math.isfinite(low) and math.isfinite(high) and low <= high):
                raise ValueError(f"{name} needs a lowest value no higher than its highest")
        if self.friction[0] <= 0 or self.strength[0] <= 0:
            raise ValueError("Friction and motor strength must stay positive")
        if not 0 <= self.strut_spread <= 0.5:
            raise ValueError("Shock absorber spread is 0 to 50%")
        if self.push_speed is not None and not 0 <= self.push_speed <= 3:
            raise ValueError("Pushes change the speed by 0 to 3 m/s")
        if not 0 <= self.noise <= 3 or not 0 <= self.delay <= 0.05:
            raise ValueError("Noise is 0 to 3 times typical, and delay 0 to 50 ms")


@dataclass(frozen=True, slots=True)
class Codesign:
    """Choosing the hardware while learning to control it (cross-entropy search).

    A pool of candidate designs is drawn from a distribution over the chosen
    values; each practice robot takes one from the pool when it starts a try. The
    policy senses the design, so one network learns to drive them all. Every few
    rounds the best-scoring designs pull the distribution toward them and it
    narrows, until the design settles; the policy then fine-tunes on it alone.
    """

    enabled: bool = False
    params: tuple[str, ...] = ()  # Design values to search; empty: all of the robot's.
    pool: int = 128  # Candidate designs alive at once.
    warmup: float = 0.15  # Share of the practice before the first update.
    finetune: float = 0.15  # Share at the end on the chosen design alone.
    updates: int = 24  # How many times the distribution moves.
    elite: float = 0.25  # Share of designs that pull the distribution.
    smoothing: float = 0.35  # How far each update moves it.
    init_std: float = 0.35  # Starting spread, as a share of each range.
    min_std: float = 0.03
    min_episodes: int = 4  # Tries a design needs before it is judged.
    target_height: float | None = None  # m; None: let the physics choose.
    height_weight: float = 4.0  # Points per metre off the target height, per second.

    def __post_init__(self) -> None:
        if self.pool < 8:
            raise ValueError("The design pool needs at least 8 designs")
        if not 0 <= self.warmup < 0.9 or not 0 <= self.finetune < 0.9 or self.warmup + self.finetune >= 0.95:
            raise ValueError("Warm-up and fine-tuning must leave time to search")
        if not 0 < self.elite < 1:
            raise ValueError("The elite share is between 0 and 1")


@dataclass(frozen=True, slots=True)
class TrainingSetup:
    """Everything a training run needs, kept in its folder to continue or repeat it."""

    robot: str = "luma_1"
    skill: str = "walk"
    weights: Mapping[str, float] = field(default_factory=dict)
    commands: Commands | None = None  # None: the skill's own speeds.
    robustness: Robustness = Robustness()
    codesign: Codesign = Codesign()
    design: Mapping[str, float] = field(default_factory=dict)  # Fixed design values.
    arms: bool = False  # Let the policy move the arms too.
    robots: int = 1024
    steps: int | None = None
    rollout: int = 24
    episode: float | None = None
    seed: int = 1
    ppo: PPOSettings | None = None

    def __post_init__(self) -> None:
        if self.skill not in SKILLS:
            raise ValueError(f"No skill named {self.skill!r}")
        unknown = [key for key in self.weights if key not in REWARDS]
        if unknown:
            raise ValueError(f"No reward named {unknown[0]!r}")
        if not 1 <= self.robots <= 16384:
            raise ValueError("Practise with 1 to 16384 robots at once")
        if self.steps is not None and self.steps < 1:
            raise ValueError("Practise for at least one move")
        if not 1 <= self.rollout <= 1000:
            raise ValueError("Rollouts are 1 to 1000 moves")
        if self.episode is not None and not 1.0 <= self.episode <= 600.0:
            raise ValueError("Tries last 1 to 600 seconds")

    @property
    def about(self) -> Skill:
        return SKILLS[self.skill]

    @property
    def command_ranges(self) -> Commands:
        return self.commands if self.commands is not None else self.about.commands

    @property
    def total_steps(self) -> int:
        return self.steps if self.steps is not None else self.about.steps

    @property
    def episode_seconds(self) -> float:
        return self.episode if self.episode is not None else self.about.episode

    @property
    def reward_weights(self) -> dict[str, float]:
        return {**self.about.weights, **self.weights}

    @property
    def settings(self) -> PPOSettings:
        return self.ppo if self.ppo is not None else PPOSettings(init_std=self.about.init_std)

    @property
    def push_speed(self) -> float:
        speed = self.robustness.push_speed
        return self.about.push_speed if speed is None else speed

    @property
    def push_every(self) -> tuple[float, float]:
        every = self.robustness.push_every
        return self.about.push_every if every is None else every

    def to_dict(self) -> dict:
        return {
            "robot": self.robot,
            "skill": self.skill,
            "weights": dict(self.weights),
            "commands": None if self.commands is None else self.commands.to_dict(),
            "robustness": asdict(self.robustness),
            "codesign": asdict(self.codesign),
            "design": dict(self.design),
            "arms": self.arms,
            "robots": self.robots,
            "steps": self.steps,
            "rollout": self.rollout,
            "episode": self.episode,
            "seed": self.seed,
            "ppo": None if self.ppo is None else asdict(self.ppo),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    @classmethod
    def from_dict(cls, document: Mapping) -> TrainingSetup:
        try:
            ppo = document.get("ppo")
            return cls(
                robot=str(document.get("robot", "luma_1")),
                skill=str(document["skill"]),
                weights={str(k): float(v) for k, v in (document.get("weights") or {}).items()},
                commands=None if not document.get("commands") else Commands.from_dict(document["commands"]),
                robustness=Robustness(**_tuples(Robustness, document.get("robustness") or {})),
                codesign=Codesign(**_tuples(Codesign, document.get("codesign") or {})),
                design={str(k): float(v) for k, v in (document.get("design") or {}).items()},
                arms=bool(document.get("arms", False)),
                robots=int(document.get("robots", 1024)),
                steps=None if document.get("steps") is None else int(document["steps"]),
                rollout=int(document.get("rollout", 24)),
                episode=None if document.get("episode") is None else float(document["episode"]),
                seed=int(document.get("seed", 1)),
                ppo=None if ppo is None else PPOSettings(**_tuples(PPOSettings, ppo)),
            )
        except (KeyError, TypeError) as error:
            raise ValueError(f"Not a readable training setup: {error}") from None

    @classmethod
    def from_json(cls, text: str) -> TrainingSetup:
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as error:
            raise ValueError(f"Not a readable training setup: {error}") from None


def _tuples(kind, values: Mapping) -> dict:
    """JSON turns tuples into lists; turn them back for the fields of ``kind``."""
    names = {item.name for item in fields(kind)}
    return {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in values.items()
        if key in names
    }


def catalogue() -> dict:
    """Skills and rewards as the web app shows them."""
    return {
        "skills": [
            {
                "key": s.key,
                "title": s.title,
                "summary": s.summary,
                "detail": s.detail,
                "steps": s.steps,
                "episode": s.episode,
                "weights": dict(s.weights),
                "gait": None if s.gait is None else s.gait.to_dict(),
                "commands": s.commands.to_dict() if "command" in s.terms else None,
                "jumps": s.jumping is not None,
            }
            for s in SKILLS.values()
        ],
        "rewards": [{"key": r.key, "label": r.label, "help": r.help} for r in REWARDS.values()],
        "robustness": asdict(Robustness()),
        "codesign": asdict(Codesign()),
        "commands": Commands().to_dict(),
    }
