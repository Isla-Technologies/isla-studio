"""Per-robot hardware settings: how to reach the motors, and the safety limits.

Kept in ``settings/<robot>-hardware.json``. The motor map (which CAN bus and node
each joint's drive sits on, its direction and zero) lives in the robot's profile,
``robot.json``, beside the model, because it describes the robot itself.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import paths


@dataclass
class BusConfig:
    """One CAN bus: which adapter carries it."""

    name: str = "can0"
    interface: str = "slcan"  # python-can interface: slcan, gs_usb, pcan, socketcan, kvaser, virtual
    channel: str = ""  # COM5, /dev/ttyACM0, can0, PCAN_USBBUS1...
    bitrate: int = 1_000_000


@dataclass
class ProtocolConfig:
    """How one actuator type's drives are spoken to."""

    protocol: str = "mit"  # mit | odrive
    gear_ratio: float = 1.0  # Motor turns per joint turn (ODrive reports motor turns).
    p_max: float = 12.5  # MIT ranges, as configured in the drive (at the joint).
    v_max: float = 65.0
    kp_max: float = 500.0
    kd_max: float = 5.0
    t_max: float = 60.0


@dataclass
class SafetyLimits:
    """What the safety filter allows. Change only while the robot is disarmed."""

    max_speed: float = 2.0  # rad/s: how fast a target may move.
    torque_fraction: float = 0.5  # Share of each motor's peak torque allowed.
    stiffness_fraction: float = 0.6  # Share of the trained servo stiffness and damping.
    soft_start: float = 2.0  # s: stiffness ramps up after arming.
    limit_margin: float = 0.05  # rad kept clear of each joint's range.
    state_timeout: float = 0.1  # s without data before the robot stops.
    max_temperature: float = 80.0  # degC
    max_tracking_error: float = 0.7  # rad between target and joint before stopping.

    def validate(self) -> None:
        checks = (
            (0.05 <= self.max_speed <= 30, "Top speed is 0.05 to 30 rad/s"),
            (0.0 < self.torque_fraction <= 1.0, "Torque share is above 0 and at most 1"),
            (0.0 < self.stiffness_fraction <= 1.0, "Stiffness share is above 0 and at most 1"),
            (0.0 <= self.soft_start <= 30, "Soft start is 0 to 30 s"),
            (0.0 <= self.limit_margin <= 0.5, "Limit margin is 0 to 0.5 rad"),
            (0.01 <= self.state_timeout <= 2.0, "Data timeout is 10 ms to 2 s"),
            (20 <= self.max_temperature <= 150, "Temperature limit is 20 to 150 degC"),
            (0.05 <= self.max_tracking_error <= math.pi, "Tracking stop is 0.05 to pi rad"),
        )
        for ok, message in checks:
            if not ok:
                raise ValueError(message)


@dataclass
class HardwareSettings:
    robot: str = "luma_1"
    driver: str = "virtual"  # virtual | can | remote
    buses: list[BusConfig] = field(default_factory=list)
    protocols: dict[str, ProtocolConfig] = field(default_factory=dict)  # Per actuator type.
    remote: str = "192.168.4.1:47210"  # The robot computer, for the remote driver.
    virtual_floor: bool = False  # Practice robot on the floor (True) or in a gantry.
    limits: SafetyLimits = field(default_factory=SafetyLimits)

    @property
    def path(self) -> Path:
        return paths.SETTINGS / f"{self.robot}-hardware.json"

    def save(self) -> None:
        self.limits.validate()
        paths.SETTINGS.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    def to_dict(self) -> dict:
        return {
            "robot": self.robot,
            "driver": self.driver,
            "buses": [asdict(b) for b in self.buses],
            "protocols": {k: asdict(v) for k, v in self.protocols.items()},
            "remote": self.remote,
            "virtual_floor": self.virtual_floor,
            "limits": asdict(self.limits),
        }

    @classmethod
    def from_dict(cls, d: dict) -> HardwareSettings:
        settings = cls(
            robot=str(d.get("robot", "luma_1")),
            driver=str(d.get("driver", "virtual")),
            buses=[BusConfig(**b) for b in d.get("buses", [])],
            protocols={k: ProtocolConfig(**v) for k, v in d.get("protocols", {}).items()},
            remote=str(d.get("remote", "192.168.4.1:47210")),
            virtual_floor=bool(d.get("virtual_floor", False)),
            limits=SafetyLimits(**d.get("limits", {})),
        )
        settings.limits.validate()
        return settings

    @classmethod
    def load(cls, robot: str, profile=None) -> HardwareSettings:
        path = paths.SETTINGS / f"{robot}-hardware.json"
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        return cls.defaults(robot, profile)

    @classmethod
    def defaults(cls, robot: str, profile=None) -> HardwareSettings:
        settings = cls(robot=robot)
        if profile is not None:
            buses = sorted({m.bus for m in profile.motors})
            settings.buses = [BusConfig(name=b, channel="") for b in buses]
            for key, kind in profile.actuator_types.items():
                peak = float(kind.get("peak_torque", 20.0))
                settings.protocols[key] = ProtocolConfig(
                    protocol=str(kind.get("protocol", "mit")),
                    gear_ratio=float(kind.get("gear_ratio", 1.0) or 1.0),
                    t_max=max(peak * 1.2, 1.0),
                )
        return settings
