"""A practice robot: the robot's hardware, simulated, behind the same driver interface.

It answers like real drives: motors take impedance commands within their
torque-speed envelope, report position, speed, torque and a temperature that
rises with current, and can be made to fail (unplugged, overheating, encoder
fault) to rehearse what the robot does then. It hangs in a gantry harness (a
strong spring holding the base) or stands on the floor.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import mujoco
import numpy as np

from ..learn.env import envelope
from ..robot.build import STAND_KEY, build
from ..robot.profile import RobotProfile
from .base import DriverError, ImuState, MotorCommand, MotorState

TIMESTEP = 0.002
AMBIENT = 28.0  # degC
THERMAL_TIME = 90.0  # s
HEATING = 0.012  # degC per (N*m)^2 at steady state


class VirtualDriver:
    name = "Practice robot"

    def __init__(self, profile: RobotProfile, design: dict | None = None, floor: bool = False) -> None:
        self.profile = profile
        self.floor = floor
        self.design = design
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.connected = False
        self.enabled = False
        self.faults: dict[str, str] = {}  # joint -> injected fault
        self.offline: set[str] = set()
        self._commands: dict[str, MotorCommand] = {}
        self._rng = np.random.default_rng(3)

    # Connection.

    def connect(self) -> None:
        built = build(self.profile, self.design, purpose="train")
        model = built.model
        model.opt.timestep = TIMESTEP
        # The drives' own servos replace MuJoCo's: torques go in through qfrc_applied.
        model.actuator_gainprm[:, 0] = 0
        model.actuator_biasprm[:, 1:3] = 0
        data = mujoco.MjData(model)
        mujoco.mj_resetDataKeyframe(model, data, model.key(STAND_KEY).id)
        if not self.floor:
            data.qpos[2] += 0.06  # Feet clear of the floor, hanging.
        mujoco.mj_forward(model, data)
        self.model, self.data, self.info = model, data, built.info
        types = self.profile.actuator_types
        self.joints = [m.joint for m in self.profile.motors]
        self.joint_q = {j: model.jnt_qposadr[model.joint(j).id] for j in self.joints}
        self.joint_v = {j: model.jnt_dofadr[model.joint(j).id] for j in self.joints}
        kinds = {m.joint: types[m.type] for m in self.profile.motors}
        self.peak = {j: float(kinds[j].get("peak_torque", 20.0)) for j in self.joints}
        self.corner = {j: float(kinds[j].get("corner_speed", 1e6)) for j in self.joints}
        self.no_load = {j: max(float(kinds[j].get("no_load_speed", 2e6)), self.corner[j] + 1e-3) for j in self.joints}
        self.temperature = dict.fromkeys(self.joints, AMBIENT)
        self.torque = dict.fromkeys(self.joints, 0.0)
        self.anchor = data.qpos[:7].copy()
        self._stop.clear()
        self.connected = True
        self._thread = threading.Thread(target=self._loop, name="practice-robot", daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.connected = False
        self.enabled = False

    def _require(self) -> None:
        if not self.connected:
            raise DriverError("The practice robot is not connected")

    # Faults, for rehearsal.

    def inject(self, joint: str, fault: str | None) -> None:
        """``offline``, ``overheat``, ``encoder``, or None to clear."""
        with self._lock:
            self.offline.discard(joint)
            self.faults.pop(joint, None)
            if fault == "offline":
                self.offline.add(joint)
            elif fault == "overheat":
                self.temperature[joint] = 95.0
            elif fault:
                self.faults[joint] = fault

    # Driver interface.

    def scan(self) -> list[str]:
        self._require()
        return [j for j in self.joints if j not in self.offline]

    def read(self) -> dict[str, MotorState]:
        self._require()
        with self._lock:
            out = {}
            for joint in self.joints:
                if joint in self.offline:
                    continue
                q = float(self.data.qpos[self.joint_q[joint]]) + float(self._rng.normal(0, 0.0005))
                v = float(self.data.qvel[self.joint_v[joint]]) + float(self._rng.normal(0, 0.01))
                out[joint] = MotorState(q, v, self.torque[joint], round(self.temperature[joint], 1), self.faults.get(joint), 0.002)
            return out

    def imu(self) -> ImuState:
        with self._lock:
            return ImuState(tuple(float(v) for v in self.data.qpos[3:7]), tuple(float(v) for v in self.data.qvel[3:6]), 0.002)

    def enable(self) -> None:
        self._require()
        self.enabled = True

    def command(self, commands: Mapping[str, MotorCommand]) -> None:
        self._require()
        with self._lock:
            self._commands.update(commands)

    def relax(self) -> None:
        self.enabled = False
        with self._lock:
            self._commands.clear()

    def set_zero(self, joint: str) -> float:
        with self._lock:
            return float(self.data.qpos[self.joint_q[joint]])

    def reset_pose(self) -> None:
        with self._lock:
            mujoco.mj_resetDataKeyframe(self.model, self.data, self.model.key(STAND_KEY).id)
            if not self.floor:
                self.data.qpos[2] += 0.06
            self.anchor = self.data.qpos[:7].copy()
            mujoco.mj_forward(self.model, self.data)

    # The simulated hardware.

    def _loop(self) -> None:
        last = time.perf_counter()
        debt = 0.0
        while not self._stop.is_set():
            now = time.perf_counter()
            debt = min(debt + now - last, 0.05)
            last = now
            with self._lock:
                while debt >= TIMESTEP:
                    self._step()
                    debt -= TIMESTEP
            time.sleep(0.001)

    def _step(self) -> None:
        model, data = self.model, self.data
        data.qfrc_applied[:] = 0
        for joint in self.joints:
            q = data.qpos[self.joint_q[joint]]
            v = data.qvel[self.joint_v[joint]]
            tau = 0.0
            cmd = self._commands.get(joint)
            if self.enabled and cmd is not None and joint not in self.offline and joint not in self.faults:
                tau = cmd.kp * (cmd.position - q) + cmd.kd * (cmd.velocity - v) + cmd.torque
                limit = cmd.torque_limit if cmd.torque_limit > 0 else self.peak[joint]
                low, high = envelope(
                    np.array([self.peak[joint]]), np.array([self.corner[joint]]), np.array([self.no_load[joint]]),
                    np.array([v]), 1.0,
                )
                tau = float(np.clip(tau, max(low[0], -limit), min(high[0], limit)))
            else:
                tau = -0.05 * v  # Back-driving a relaxed geared motor.
            self.torque[joint] = tau
            data.qfrc_applied[self.joint_v[joint]] += tau
            steady = AMBIENT + HEATING * tau * tau
            self.temperature[joint] += (steady - self.temperature[joint]) * TIMESTEP / THERMAL_TIME
        if not self.floor:
            # The gantry harness: a stiff spring-damper on the base's position and tilt.
            error = self.anchor[:3] - data.qpos[:3]
            data.xfrc_applied[1, :3] = 4000.0 * error - 200.0 * data.qvel[:3]
            quat = data.qpos[3:7]
            target = self.anchor[3:7]
            diff = np.zeros(3)
            mujoco.mju_subQuat(diff, target, quat)
            data.xfrc_applied[1, 3:6] = 600.0 * diff - 40.0 * data.qvel[3:6]
        mujoco.mj_step(model, data)
        if not np.isfinite(data.qpos).all():
            self.reset_pose()

