"""A robot profile: what the platform knows about a robot beyond its MuJoCo model.

``robot.json`` sits beside the robot's model. It names the base, torso, feet and
IMU; lists every motor with its actuator type and where it sits on the CAN buses;
and, for robots designed in-house, the hardware the co-design search may change:
rod lengths and shock absorbers. A robot imported without a profile gets one by
auto-detection (see :mod:`isla_rl.robot.importer`).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths

FORMAT = "isla-robot"
DESIGN_KINDS = ("rod", "strut_k", "strut_c", "strut_preload")


@dataclass(frozen=True)
class DesignParam:
    """One number of the hardware that co-design may choose."""

    key: str
    label: str
    unit: str
    kind: str
    low: float
    high: float
    default: float
    step: float = 0.0
    help: str = ""
    rod: str | None = None
    position: str | None = None

    def clamp(self, value: float) -> float:
        return min(max(float(value), self.low), self.high)

    def normalize(self, value: float) -> float:
        """0 at the low end of the range, 1 at the high end."""
        span = self.high - self.low
        return 0.5 if span <= 0 else (float(value) - self.low) / span

    def denormalize(self, unit: float) -> float:
        return self.low + float(unit) * (self.high - self.low)

    def snap(self, value: float) -> float:
        """The value rounded to the step a workshop can make."""
        if self.step <= 0:
            return self.clamp(value)
        steps = round((float(value) - self.low) / self.step)
        return self.clamp(self.low + steps * self.step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "kind": self.kind,
            "range": [self.low, self.high],
            "default": self.default,
            "step": self.step,
            "help": self.help,
            "rod": self.rod,
            "position": self.position,
        }


@dataclass(frozen=True)
class Motor:
    joint: str
    type: str
    bus: str = "can0"
    node: int = 1
    direction: int = 1
    zero: float = 0.0
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "joint": self.joint,
            "type": self.type,
            "bus": self.bus,
            "node": self.node,
            "direction": self.direction,
            "zero": self.zero,
            "group": self.group,
        }


@dataclass
class RobotProfile:
    """A robot's profile, loaded from (and saved to) its folder's ``robot.json``."""

    folder: Path
    data: dict[str, Any] = field(default_factory=dict)

    # Loading and saving.

    @classmethod
    def load(cls, robot: str | Path) -> RobotProfile:
        folder = Path(robot)
        if not folder.is_absolute() and not folder.exists():
            folder = paths.ROBOTS / str(robot)
        path = folder / "robot.json"
        if not path.exists():
            alias = _by_alias(str(robot))
            if alias is None:
                raise FileNotFoundError(f"No robot profile at {path}")
            folder, path = alias, alias / "robot.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise ValueError(f"{path} is not an Isla robot profile")
        profile = cls(folder.resolve(), data)
        profile.validate()
        return profile

    def save(self) -> None:
        self.validate()
        path = self.folder / "robot.json"
        text = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
        fd, temporary = tempfile.mkstemp(dir=self.folder, prefix=".robot.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                file.write(text)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def validate(self) -> None:
        if not self.id or not self.name:
            raise ValueError("A robot profile needs an id and a name")
        if not self.source_path.exists():
            raise FileNotFoundError(f"The robot's model is missing: {self.source_path}")
        types = self.actuator_types
        joints = set()
        for motor in self.motors:
            if motor.type not in types:
                raise ValueError(f"Motor {motor.joint} uses unknown actuator type {motor.type!r}")
            if motor.joint in joints:
                raise ValueError(f"Joint {motor.joint} has two motors")
            if motor.direction not in (1, -1):
                raise ValueError(f"Motor {motor.joint}: direction must be 1 or -1")
            joints.add(motor.joint)
        seen = set()
        for motor in self.motors:
            key = (motor.bus, motor.node)
            if key in seen:
                raise ValueError(f"Two motors share node {motor.node} on {motor.bus}")
            seen.add(key)
        for param in self.design.values():
            if param.kind not in DESIGN_KINDS:
                raise ValueError(f"Design value {param.key} has unknown kind {param.kind!r}")
            if not (math.isfinite(param.low) and math.isfinite(param.high) and param.low <= param.high):
                raise ValueError(f"Design value {param.key} needs a valid range")

    # What it says.

    @property
    def id(self) -> str:
        return str(self.data.get("id", self.folder.name))

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.id))

    @property
    def about(self) -> str:
        return str(self.data.get("about", ""))

    @property
    def source_path(self) -> Path:
        return (self.folder / self.data["source"]).resolve()

    @property
    def forward(self) -> str:
        return str(self.data.get("frame", {}).get("forward", "+x"))

    @property
    def left(self) -> str:
        return str(self.data.get("frame", {}).get("left", "+y"))

    @property
    def base(self) -> str | None:
        return self.data.get("base")

    @property
    def torso(self) -> str | None:
        return self.data.get("torso")

    @property
    def imu_site(self) -> str | None:
        return self.data.get("imu_site")

    @property
    def mass(self) -> dict[str, Any]:
        return dict(self.data.get("mass", {}))

    @property
    def feet(self) -> list[dict[str, Any]]:
        return list(self.data.get("feet", []))

    @property
    def actuator_types(self) -> dict[str, dict[str, Any]]:
        return dict(self.data.get("actuator_types", {}))

    @property
    def motors(self) -> list[Motor]:
        return [
            Motor(
                joint=m["joint"],
                type=m["type"],
                bus=str(m.get("bus", "can0")),
                node=int(m.get("node", 1)),
                direction=int(m.get("direction", 1)),
                zero=float(m.get("zero", 0.0)),
                group=str(m.get("group", "")),
            )
            for m in self.data.get("motors", [])
        ]

    def motor(self, joint: str) -> Motor | None:
        return next((m for m in self.motors if m.joint == joint), None)

    @property
    def operating_ranges(self) -> dict[str, tuple[float, float]]:
        ranges = self.data.get("operating_ranges", {})
        return {k: (float(v[0]), float(v[1])) for k, v in ranges.items() if isinstance(v, list)}

    @property
    def stand(self) -> dict[str, Any]:
        return dict(self.data.get("stand", {}))

    @property
    def struts(self) -> list[dict[str, Any]]:
        return list(self.data.get("struts", {}).get("items", []))

    @property
    def strut_stroke(self) -> float:
        """Usable shock absorber stroke, metres."""
        return float(self.data.get("struts", {}).get("stroke_mm", 24.0)) / 1000.0

    @property
    def rods(self) -> list[dict[str, Any]]:
        return list(self.data.get("rods", {}).get("items", []))

    @property
    def design(self) -> dict[str, DesignParam]:
        params = {}
        for key, d in self.data.get("design", {}).items():
            low, high = d.get("range", [0.0, 0.0])
            params[key] = DesignParam(
                key=key,
                label=str(d.get("label", key)),
                unit=str(d.get("unit", "")),
                kind=str(d.get("kind", "")),
                low=float(low),
                high=float(high),
                default=float(d.get("default", low)),
                step=float(d.get("step", 0.0)),
                help=str(d.get("help", "")),
                rod=d.get("rod"),
                position=d.get("position"),
            )
        return params

    def default_design(self) -> dict[str, float]:
        chosen = self.data.get("chosen_design") or {}
        return {
            key: param.clamp(chosen.get(key, param.default)) for key, param in self.design.items()
        }

    def complete_design(self, values: dict[str, float] | None) -> dict[str, float]:
        """Every design value, taking the given ones (clamped) and defaults for the rest."""
        design = self.default_design()
        for key, value in (values or {}).items():
            if key in self.design and value is not None and math.isfinite(float(value)):
                design[key] = self.design[key].clamp(value)
        return design

    def to_dict(self) -> dict[str, Any]:
        """What the web app shows about the robot."""
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.data.get("aliases", [])),
            "about": self.about,
            "frame": {"forward": self.forward, "left": self.left},
            "mass": self.mass,
            "feet": self.feet,
            "actuator_types": self.actuator_types,
            "motors": [m.to_dict() for m in self.motors],
            "operating_ranges": {k: list(v) for k, v in self.operating_ranges.items()},
            "struts": self.data.get("struts", {}),
            "rods": self.data.get("rods", {}),
            "design": {key: p.to_dict() for key, p in self.design.items()},
            "design_values": self.default_design(),
        }


def list_robots() -> list[RobotProfile]:
    found = []
    if paths.ROBOTS.exists():
        for folder in sorted(paths.ROBOTS.iterdir()):
            if (folder / "robot.json").exists():
                try:
                    found.append(RobotProfile.load(folder))
                except (ValueError, FileNotFoundError, KeyError, json.JSONDecodeError):
                    continue
    return found


def _by_alias(name: str) -> Path | None:
    """The folder of the robot that was once called ``name`` (profiles list old ids as aliases)."""
    if not paths.ROBOTS.exists():
        return None
    for folder in paths.ROBOTS.iterdir():
        path = folder / "robot.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if name in data.get("aliases", []):
            return folder
    return None
