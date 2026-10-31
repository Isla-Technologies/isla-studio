"""What every robot driver provides: motor states in, impedance commands out.

Everything is keyed by the joint a motor drives and expressed at the joint
(output shaft), in radians, rad/s and N*m. A driver converts to its own units
(motor turns, gear ratios, raw counts) and applies each motor's direction and zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MotorState:
    """One motor's latest measurement at its joint."""

    position: float
    velocity: float = 0.0
    torque: float = 0.0
    temperature: float | None = None  # degC, when the motor reports it.
    fault: str | None = None
    age: float = 0.0  # Seconds since the motor last answered.


@dataclass(frozen=True, slots=True)
class MotorCommand:
    """An impedance command: tau = kp (position - q) + kd (velocity - dq) + torque,
    limited to +/- ``torque_limit`` at the joint."""

    position: float
    velocity: float = 0.0
    kp: float = 0.0
    kd: float = 0.0
    torque: float = 0.0
    torque_limit: float = 0.0


@dataclass(frozen=True, slots=True)
class ImuState:
    quaternion: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)  # w, x, y, z
    gyro: tuple[float, float, float] = (0.0, 0.0, 0.0)  # rad/s, in the IMU's (base's) frame
    age: float = 0.0


@dataclass
class DriverInfo:
    """What the Connect page shows about a driver."""

    key: str
    label: str
    detail: str
    available: bool = True
    problem: str = ""
    settings: dict = field(default_factory=dict)


class DriverError(RuntimeError):
    """The robot could not be reached, or an action is not allowed right now."""


class RobotDriver(Protocol):
    name: str

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def scan(self) -> list[str]:
        """Joints whose motors answer."""
        ...

    def read(self) -> dict[str, MotorState]: ...

    def imu(self) -> ImuState | None: ...

    def enable(self) -> None:
        """Put every motor in closed-loop control, ready for commands."""
        ...

    def command(self, commands: Mapping[str, MotorCommand]) -> None: ...

    def relax(self) -> None:
        """Stop driving every motor (zero torque, or the drive's idle state)."""
        ...

    def set_zero(self, joint: str) -> float:
        """Make the joint's present position read zero; returns the new zero offset."""
        ...
