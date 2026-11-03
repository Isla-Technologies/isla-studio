"""A trained policy: what it senses, its network, and how it moves the motors.

Fifty times a second the policy reads the robot's state as the robot itself
measures it: the base's rotation rate and which way is down (IMU), each driven
joint's angle and speed (motor encoders), the command (such as a walking speed),
its own last action, a gait clock (whose stride shortens from walking to running
as the commanded speed rises), a jump clock for a policy that jumps, and, for a
policy trained while the hardware design varied, the design it is running on. Its network turns these into an
offset from a base pose for every joint it drives; the motors' servos hold those
targets. :func:`observe` builds that input the same way in training, in the live
simulation and on the robot, so a policy always meets what it learned from.

Directions follow robotics practice (REP 103): x forward, y left, z up. A robot
profile says which of its base's axes point forward and left; ``to_robot`` turns
the base's axes into these.

A policy file is plain JSON with float32 arrays in base64: NumPy alone runs it,
so the robot's computer needs nothing more.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from .device import module_of
from .network import MLP

FORMAT = "isla-policy"
VERSION = 1
GYRO_SCALE = 0.25
SPEED_SCALE = 0.05
COMMAND_SCALE = np.array([2.0, 2.0, 0.25])
TERMS = ("gyro", "gravity", "command", "joints", "speeds", "actions", "phase", "jump", "design")
# The most a walking command changes per second: forward, sideways (m/s), turning (rad/s).
RAMP = np.array([1.0, 0.8, 1.5])
_ON_DEVICE: dict = {}


def frame_matrix(forward: Sequence[float], left: Sequence[float]) -> NDArray:
    """Rows: the robot's forward, left and up directions in its base's axes."""
    f = np.asarray(forward, float)
    left_axis = np.asarray(left, float)
    up = np.cross(f, left_axis)
    return np.stack([f, left_axis, up])


def like(constant: NDArray, array) -> NDArray:
    """``constant`` where ``array`` lives, in its precision."""
    xp = module_of(array)
    if xp is np:
        return constant
    key = (id(constant), array.dtype.str)
    value = _ON_DEVICE.get(key)
    if value is None:
        value = _ON_DEVICE[key] = xp.asarray(constant, dtype=array.dtype)
    return value


def quat_rotate_inverse(quaternion: NDArray, vector: NDArray) -> NDArray:
    """World vectors in bodies' frames; one row per body."""
    xp = module_of(quaternion)
    w = quaternion[:, :1]
    u = -quaternion[:, 1:4]
    t = 2.0 * xp.cross(u, vector)
    return vector + w * t + xp.cross(u, t)


def gravity_in_base(quaternion: NDArray) -> NDArray:
    """Which way is down in each base's own axes: (0, 0, -1) when upright."""
    xp = module_of(quaternion)
    down = xp.zeros((quaternion.shape[0], 3), dtype=np.result_type(quaternion.dtype, np.float32))
    down[:, 2] = -1.0
    return quat_rotate_inverse(quaternion, down)


def size_of(terms: Sequence[str], joints: int, design: int = 0) -> int:
    sizes = {
        "gyro": 3,
        "gravity": 3,
        "command": 3,
        "joints": joints,
        "speeds": joints,
        "actions": joints,
        "phase": 2,
        "jump": 3,
        "design": design,
    }
    return sum(sizes[term] for term in terms)


def observe(
    terms: Sequence[str],
    to_robot: NDArray,
    quaternion: NDArray,
    gyro: NDArray,
    positions: NDArray,
    velocities: NDArray,
    default: NDArray,
    last_actions: NDArray,
    command: NDArray | None = None,
    phase: NDArray | None = None,
    design: NDArray | None = None,
    jump: NDArray | None = None,
) -> NDArray[np.float32]:
    """A policy's input for a batch of robots, one row each.

    ``quaternion`` is the base's orientation (w, x, y, z) in the world, ``gyro``
    its rotation rate in its own axes (rad/s), ``to_robot`` the matrix from
    :func:`frame_matrix`. ``phase`` is a share of the gait cycle, 0 to 1;
    ``design`` the hardware design scaled to 0..1 per value; ``jump`` one row
    per robot of (jumping now: 0 or 1, share of the jump done: 0 to 1).
    """
    xp = module_of(quaternion)
    turn = like(np.asarray(to_robot, float).T, quaternion)
    parts = []
    for term in terms:
        if term == "gyro":
            parts.append(gyro @ turn * GYRO_SCALE)
        elif term == "gravity":
            parts.append(gravity_in_base(quaternion) @ turn)
        elif term == "command":
            parts.append(command * like(COMMAND_SCALE, command))
        elif term == "joints":
            parts.append(positions - default)
        elif term == "speeds":
            parts.append(velocities * SPEED_SCALE)
        elif term == "actions":
            parts.append(last_actions)
        elif term == "phase":
            angle = 2.0 * math.pi * phase
            parts.append(xp.stack([xp.sin(angle), xp.cos(angle)], axis=1))
        elif term == "jump":
            angle = 2.0 * math.pi * jump[:, 1]
            parts.append(xp.stack([jump[:, 0], xp.sin(angle) * jump[:, 0], xp.cos(angle) * jump[:, 0]], axis=1))
        elif term == "design":
            parts.append(design * 2.0 - 1.0)
        else:
            raise ValueError(f"A policy cannot sense {term!r}")
    return xp.concatenate(parts, axis=1).astype(np.float32)


def _pack(array: NDArray) -> dict:
    array = np.ascontiguousarray(array, dtype="<f4")
    return {"shape": list(array.shape), "data": base64.b64encode(array.tobytes()).decode("ascii")}


def _unpack(packed: Mapping) -> NDArray[np.float32]:
    data = base64.b64decode(packed["data"])
    return np.frombuffer(data, dtype="<f4").reshape(packed["shape"]).astype(np.float32)


@dataclass(frozen=True, slots=True)
class Commands:
    """Speeds a walking policy learned to follow: forward, sideways, turning."""

    forward: tuple[float, float] = (-0.3, 0.6)  # m/s
    sideways: float = 0.25  # m/s either way
    turning: float = 0.8  # rad/s either way

    def clamp(self, command: Sequence[float]) -> NDArray:
        forward, left, turn = (float(v) for v in command)
        return np.array(
            [
                min(max(forward, self.forward[0]), self.forward[1]),
                min(max(left, -self.sideways), self.sideways),
                min(max(turn, -self.turning), self.turning),
            ]
        )

    def to_dict(self) -> dict:
        return {"forward": list(self.forward), "sideways": self.sideways, "turning": self.turning}

    @classmethod
    def from_dict(cls, d: Mapping | None) -> Commands:
        if not d:
            return cls()
        return cls(tuple(float(v) for v in d["forward"]), float(d["sideways"]), float(d["turning"]))


@dataclass(frozen=True, slots=True)
class Gait:
    """The gait clock. Each foot is down for a share of every stride, the feet half a
    stride apart; as the commanded forward speed rises from ``speeds[0]`` to
    ``speeds[1]``, the stride eases from its walking values to its running ones:
    shorter, with less time down (below half: both feet are off the floor at once),
    and the swinging foot lifted higher."""

    period: tuple[float, float] = (0.7, 0.7)  # s per stride: walking, running.
    stance: tuple[float, float] = (0.55, 0.55)  # Share of a stride each foot is down.
    swing_height: tuple[float, float] = (0.04, 0.04)  # m a swinging foot lifts.
    speeds: tuple[float, float] = (0.5, 1.0)  # m/s where walking ends and running begins.

    def run_share(self, forward):
        """0 walking to 1 running, for a forward speed (m/s), one per robot."""
        xp = module_of(forward)
        low, high = self.speeds
        return xp.clip((forward - low) / max(high - low, 1e-6), 0.0, 1.0)

    @staticmethod
    def _blend(pair: tuple[float, float], share):
        return pair[0] + (pair[1] - pair[0]) * share

    def period_at(self, share):
        return self._blend(self.period, share)

    def stance_at(self, share):
        return self._blend(self.stance, share)

    def swing_at(self, share):
        return self._blend(self.swing_height, share)

    def to_dict(self) -> dict:
        return {"period": list(self.period), "stance": list(self.stance), "swing_height": list(self.swing_height), "speeds": list(self.speeds)}

    @classmethod
    def from_dict(cls, d: Mapping) -> Gait:
        return cls(*(tuple(float(v) for v in d[key]) for key in ("period", "stance", "swing_height", "speeds")))


@dataclass(frozen=True, slots=True)
class Policy:
    """A trained policy, ready to run on the simulation or the robot."""

    name: str
    robot: str
    skill: str
    joints: tuple[str, ...]  # Driven joints, in the order of the actions.
    default: tuple[float, ...]  # Each joint's target at zero action.
    hold: dict[str, float]  # Targets of the robot's other motors.
    gains: tuple[tuple[float, float], ...]  # Servo kp, kd it learned with.
    terms: tuple[str, ...]
    network: MLP
    obs_mean: NDArray
    obs_std: NDArray
    to_robot: tuple[tuple[float, ...], ...]
    action_scale: float = 0.25
    action_clip: float = 5.0
    rate: float = 50.0
    gait: Gait | None = None
    jump_seconds: float | None = None  # How long a jump takes, crouch to landing, for a policy that jumps.
    commands: Commands | None = None
    design: dict[str, float] = field(default_factory=dict)  # Hardware it was trained for.
    design_keys: tuple[str, ...] = ()  # Values it senses, in order.
    design_ranges: tuple[tuple[float, float], ...] = ()
    about: str = ""
    stats: dict = field(default_factory=dict)
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        count = len(self.joints)
        if not count or len(self.default) != count or len(self.gains) != count:
            raise ValueError("A policy needs a base pose and gains for each of its joints")
        expected = size_of(self.terms, count, len(self.design_keys))
        if self.network.sizes[0] != expected or self.network.sizes[-1] != count:
            raise ValueError("The policy's network does not fit what it senses and drives")
        if len(self.obs_mean) != expected or len(self.obs_std) != expected:
            raise ValueError("The policy's input scaling does not fit what it senses")

    def design_vector(self, design: Mapping[str, float] | None = None) -> NDArray:
        values = {**self.design, **(design or {})}
        out = []
        for key, (low, high) in zip(self.design_keys, self.design_ranges, strict=True):
            span = high - low
            out.append(0.5 if span <= 0 else (float(values.get(key, low)) - low) / span)
        return np.clip(np.array(out, dtype=np.float32), 0.0, 1.0)

    def act(self, obs: NDArray) -> NDArray[np.float32]:
        """Actions for a batch of inputs; no exploration."""
        scaled = np.clip((obs - self.obs_mean) / self.obs_std, -10.0, 10.0).astype(np.float32)
        return self.network(scaled)

    def to_json(self) -> str:
        document = {
            "format": FORMAT,
            "version": VERSION,
            "name": self.name,
            "robot": self.robot,
            "skill": self.skill,
            "created": self.created,
            "about": self.about,
            "joints": list(self.joints),
            "default": [round(v, 6) for v in self.default],
            "hold": {k: round(v, 6) for k, v in self.hold.items()},
            "gains": [[round(kp, 4), round(kd, 4)] for kp, kd in self.gains],
            "terms": list(self.terms),
            "to_robot": [list(row) for row in self.to_robot],
            "action_scale": self.action_scale,
            "action_clip": self.action_clip,
            "rate": self.rate,
            "gait": None if self.gait is None else self.gait.to_dict(),
            "jump_seconds": self.jump_seconds,
            "commands": None if self.commands is None else self.commands.to_dict(),
            "design": self.design,
            "design_keys": list(self.design_keys),
            "design_ranges": [list(r) for r in self.design_ranges],
            "stats": self.stats,
            "input": {"mean": _pack(self.obs_mean), "std": _pack(self.obs_std)},
            "network": [
                {"weight": _pack(w), "bias": _pack(b)}
                for w, b in zip(self.network.weights, self.network.biases, strict=True)
            ],
        }
        return json.dumps(document, separators=(",", ":")) + "\n"

    @classmethod
    def from_document(cls, document: Mapping) -> Policy:
        try:
            if document.get("format") != FORMAT:
                raise ValueError("not an Isla policy")
            network = MLP.__new__(MLP)
            network.weights = [_unpack(layer["weight"]) for layer in document["network"]]
            network.biases = [_unpack(layer["bias"]) for layer in document["network"]]
            network.sizes = (network.weights[0].shape[0], *(w.shape[1] for w in network.weights))
            return cls(
                name=str(document["name"]),
                robot=str(document.get("robot", "")),
                skill=str(document["skill"]),
                joints=tuple(document["joints"]),
                default=tuple(float(v) for v in document["default"]),
                hold={str(k): float(v) for k, v in document.get("hold", {}).items()},
                gains=tuple((float(kp), float(kd)) for kp, kd in document["gains"]),
                terms=tuple(document["terms"]),
                network=network,
                obs_mean=_unpack(document["input"]["mean"]),
                obs_std=_unpack(document["input"]["std"]),
                to_robot=tuple(tuple(float(v) for v in row) for row in document["to_robot"]),
                action_scale=float(document["action_scale"]),
                action_clip=float(document["action_clip"]),
                rate=float(document["rate"]),
                gait=_gait_of(document),
                jump_seconds=None if document.get("jump_seconds") is None else float(document["jump_seconds"]),
                commands=None if document.get("commands") is None else Commands.from_dict(document["commands"]),
                design={str(k): float(v) for k, v in (document.get("design") or {}).items()},
                design_keys=tuple(document.get("design_keys", [])),
                design_ranges=tuple(tuple(float(v) for v in r) for r in document.get("design_ranges", [])),
                about=str(document.get("about", "")),
                stats=dict(document.get("stats") or {}),
                created=str(document.get("created", "")),
            )
        except (KeyError, TypeError, IndexError, AttributeError) as error:
            raise ValueError(f"Not a readable policy: {error}") from None

    @classmethod
    def load(cls, path) -> Policy:
        from pathlib import Path

        return cls.from_document(json.loads(Path(path).read_text(encoding="utf-8")))


def _gait_of(document: Mapping) -> Gait | None:
    """A policy file's gait clock; files from before the clock eased with speed give one period."""
    if document.get("gait"):
        return Gait.from_dict(document["gait"])
    period = document.get("gait_period")
    return Gait(period=(float(period), float(period))) if period else None


class PolicyRunner:
    """Runs a policy on one robot: senses, decides at its rate, holds targets between.

    ``set_command`` sets the walking speed asked for; the speed followed eases
    toward it no faster than ``RAMP``, and the gait clock follows the speed as in
    training. ``jump`` starts a jump, for a policy that jumps. Call :meth:`step` as
    often as you like with fresh readings; it returns targets for every motor.
    """

    def __init__(self, policy: Policy, design: Mapping[str, float] | None = None) -> None:
        self.policy = policy
        self.command = np.zeros(3)
        self.speed: NDArray | None = None
        self.elapsed = 0.0
        self.phase = 0.0
        self.jumping: float | None = None  # Share of the jump done, while jumping.
        self._since = math.inf
        self._actions = np.zeros(len(policy.joints), np.float32)
        self._targets = dict(zip(policy.joints, policy.default, strict=True))
        self._design = policy.design_vector(design).reshape(1, -1)
        self._to_robot = np.asarray(policy.to_robot, float)

    def set_command(self, forward: float, left: float, turn: float) -> None:
        commands = self.policy.commands
        self.command = commands.clamp((forward, left, turn)) if commands is not None else np.zeros(3)

    @property
    def can_jump(self) -> bool:
        return bool(self.policy.jump_seconds) and "jump" in self.policy.terms

    def jump(self) -> bool:
        """Start a jump; False when the policy cannot jump or is mid-jump."""
        if not self.can_jump or self.jumping is not None:
            return False
        self.jumping = 0.0
        return True

    def step(
        self,
        dt: float,
        quaternion: Sequence[float],
        gyro: Sequence[float],
        joints: Mapping[str, float],
        velocities: Mapping[str, float],
    ) -> dict[str, float]:
        policy = self.policy
        self.elapsed += dt
        self._since += dt
        if self.speed is None:
            self.speed = self.command.copy()
        else:
            most = RAMP * dt
            self.speed = self.speed + np.clip(self.command - self.speed, -most, most)
        gait = policy.gait
        if gait is not None:
            share = gait.run_share(np.array([self.speed[0]]))[0]
            self.phase = (self.phase + dt / float(gait.period_at(share))) % 1.0
        if self.jumping is not None:
            self.jumping += dt / float(policy.jump_seconds)
            if self.jumping >= 1.0:
                self.jumping = None
        if self._since >= 1.0 / policy.rate - 1e-9:
            self._since = 0.0
            names = policy.joints
            default = np.asarray(policy.default).reshape(1, -1)
            obs = observe(
                policy.terms,
                self._to_robot,
                np.asarray(quaternion, float).reshape(1, 4),
                np.asarray(gyro, float).reshape(1, 3),
                np.array([[joints.get(j, d) for j, d in zip(names, policy.default, strict=True)]]),
                np.array([[velocities.get(j, 0.0) for j in names]]),
                default,
                self._actions.reshape(1, -1),
                command=self.speed.reshape(1, 3),
                phase=np.array([self.phase]),
                design=self._design,
                jump=np.array([[0.0, 0.0] if self.jumping is None else [1.0, self.jumping]]),
            )
            actions = np.clip(policy.act(obs)[0], -policy.action_clip, policy.action_clip)
            self._actions = actions.astype(np.float32)
            targets = default[0] + policy.action_scale * actions
            self._targets = {name: float(t) for name, t in zip(names, targets, strict=True)}
        return {**policy.hold, **self._targets}
