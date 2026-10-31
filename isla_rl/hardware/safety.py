"""Turn wished-for joint targets into motor commands that respect every limit.

Each control tick the filter clamps every target inside the joint's range less a
margin, moves it no faster than the top speed, scales stiffness and damping by
the allowed share (ramping up over the soft start after arming), and caps torque.
``check`` names, in plain words, the first reason the robot must stop.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .base import MotorCommand, MotorState
from .config import SafetyLimits


@dataclass(frozen=True, slots=True)
class JointLimits:
    low: float
    high: float
    peak_torque: float
    kp: float
    kd: float


class SafetyFilter:
    def __init__(self, joints: Mapping[str, JointLimits], limits: SafetyLimits) -> None:
        self.joints = dict(joints)
        self.limits = limits
        self.targets: dict[str, float] = {}

    def start(self, measured: Mapping[str, float]) -> None:
        """Begin at each joint's measured position, so arming never makes the robot jump."""
        self.targets = {joint: float(measured[joint]) for joint in self.joints if joint in measured}

    def commands(self, desired: Mapping[str, float], dt: float, since_armed: float) -> dict[str, MotorCommand]:
        limits = self.limits
        ramp = 1.0 if limits.soft_start <= 0 else min(1.0, max(0.0, since_armed) / limits.soft_start)
        share = limits.stiffness_fraction * ramp
        step = limits.max_speed * dt
        out = {}
        for joint, spec in self.joints.items():
            if joint not in self.targets:
                continue
            low, high = spec.low + limits.limit_margin, spec.high - limits.limit_margin
            if low > high:
                low = high = (spec.low + spec.high) / 2
            last = self.targets[joint]
            goal = min(max(float(desired.get(joint, last)), low), high)
            target = min(max(goal, last - step), last + step)
            self.targets[joint] = target
            out[joint] = MotorCommand(
                position=target,
                kp=spec.kp * share,
                kd=spec.kd * share,
                torque_limit=spec.peak_torque * limits.torque_fraction,
            )
        return out

    def check(self, states: Mapping[str, MotorState], armed: bool) -> str | None:
        """The reason the robot must stop now, or None when every reading is healthy."""
        limits = self.limits
        for joint in self.joints:
            state = states.get(joint)
            if state is None:
                return f"{joint}: its motor is not answering"
            if state.age > limits.state_timeout:
                return f"{joint}: no data for {state.age * 1000:.0f} ms"
            if state.fault:
                return f"{joint}: the drive reports {state.fault}"
            if state.temperature is not None and state.temperature > limits.max_temperature:
                return f"{joint}: {state.temperature:.0f} degC is over the {limits.max_temperature:.0f} degC limit"
            if armed and joint in self.targets:
                error = abs(state.position - self.targets[joint])
                if error > limits.max_tracking_error:
                    return f"{joint}: {error:.2f} rad from its target"
        return None
