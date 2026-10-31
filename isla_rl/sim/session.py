"""The robot simulated in real time, as the web app shows it and the robot follows it.

One MuJoCo simulation of the display model runs on its own thread at 500 steps a
second, paced to the wall clock. It can:

- ``stand``: hold the standing pose on the motors' servos;
- ``pose``: hold joint targets set from the web app;
- ``policy``: let a trained policy drive it, steered by a walking command;
- ``limp``: motors off (the robot falls, as a real one would);
- ``mirror``: show the real robot's measured pose instead of simulating.

A robot that cannot stand by holding a pose (most need a policy to balance)
hangs in a gantry harness, as real robots do during bring-up: a soft weld holds
its base where it stands. Running a policy lets go.

Motors follow the same torque-speed envelope as in training, but the physics steps
every 2 ms instead of 4: watching a policy here is a test in different physics,
as robot teams test before hardware. Frames go out to listeners 30 times a second.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

import mujoco
import numpy as np

from ..learn.env import envelope
from ..learn.policy import Policy, PolicyRunner
from ..robot.analysis import STAND_DRIFT, stand_drift
from ..robot.build import GANTRY, STAND_KEY, Built, build
from ..robot.profile import RobotProfile

TIMESTEP = 0.002
FRAME_RATE = 30.0
MODES = ("stand", "pose", "policy", "limp", "mirror")
MAX_CATCH_UP = 0.05  # s of simulation one loop may catch up.


class LiveSession:
    def __init__(self, profile: RobotProfile, design: dict[str, float] | None = None) -> None:
        self.profile = profile
        self.lock = threading.RLock()
        self.listeners: list[Callable[[dict], None]] = []
        self.mode = "stand"
        self.playing = True
        self.rate = 1.0  # Real-time factor.
        self.command = np.zeros(3)
        self.runner: PolicyRunner | None = None
        self.policy: Policy | None = None
        self.policy_name = ""
        self._push: tuple[np.ndarray, float] | None = None
        self._mirror: dict | None = None
        self.gantry = False
        self.stands_alone = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.message = ""
        self.load(design)

    # The model.

    def load(self, design: dict[str, float] | None = None) -> None:
        built = build(self.profile, design, purpose="display")
        with self.lock:
            self._adopt(built)

    def _adopt(self, built: Built) -> None:
        self.built = built
        self.model = model = built.model
        model.opt.timestep = TIMESTEP
        self.data = mujoco.MjData(model)
        self.info = built.info
        self.design = dict(built.info.design)
        types = self.profile.actuator_types
        joints = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, model.actuator_trnid[a, 0]) for a in range(model.nu)]
        motor_type = {m.joint: m.type for m in self.info.motors}
        self.actuator_joints = joints
        self.peak = np.array([float(types[motor_type[j]].get("peak_torque", 20)) if j in motor_type else 1e6 for j in joints])
        self.corner = np.array([float(types[motor_type[j]].get("corner_speed", 1e6)) if j in motor_type else 1e6 for j in joints])
        self.no_load = np.maximum(
            np.array([float(types[motor_type[j]].get("no_load_speed", 2e6)) if j in motor_type else 2e6 for j in joints]),
            self.corner + 1e-3,
        )
        self.actuator_dofs = np.array([model.jnt_dofadr[model.actuator_trnid[a, 0]] for a in range(model.nu)])
        self.actuator_qpos = np.array([model.jnt_qposadr[model.actuator_trnid[a, 0]] for a in range(model.nu)])
        self.joint_ids = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, j): j for j in range(model.njnt)}
        self.base = model.body(self.info.base).id
        self.torso = model.body(self.info.torso).id if self.info.torso else self.base
        self.stand_ctrl = np.array(self.info.stand_ctrl)
        self.targets = self.stand_ctrl.copy()
        self.touch = {f.side: model.sensor_adr[model.sensor(f.touch).id] for f in self.info.feet}
        self.struts = [(s, model.tendon(s.tendon).id) for s in self.info.struts]
        free = next(j for j in range(model.njnt) if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE)
        self.free_q = int(model.jnt_qposadr[free])
        self.harness = model.equality(GANTRY).id
        self.hook = int(model.body_mocapid[model.body(GANTRY).id])
        self.stands_alone = self._stands_alone()
        self.gantry = not self.stands_alone
        self.reset()

    def _stands_alone(self) -> bool:
        """Whether the motors alone hold the robot up in its standing pose."""
        qpos = self.model.key_qpos[self.model.key(STAND_KEY).id]
        return stand_drift(self.model, qpos, self.stand_ctrl, self.base) < STAND_DRIFT

    def reset(self) -> None:
        with self.lock:
            key = self.model.key(STAND_KEY).id
            mujoco.mj_resetDataKeyframe(self.model, self.data, key)
            self._hang(self.gantry)
            mujoco.mj_forward(self.model, self.data)
            self.targets = self.stand_ctrl.copy()
            if self.policy is not None:
                self.runner = PolicyRunner(self.policy, self.design)
                self.runner.set_command(*self.command)
            self._push = None

    # Controls.

    def set_design(self, design: dict[str, float]) -> None:
        built = build(self.profile, design, purpose="display")
        with self.lock:
            qpos_ok = built.model.nq == self.model.nq
            self._adopt(built)
            del qpos_ok

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"No mode {mode!r}")
        with self.lock:
            if mode == "policy" and self.policy is None:
                raise ValueError("Choose a policy first")
            if mode == "policy":
                self.runner = PolicyRunner(self.policy, self.design)
                self.runner.set_command(*self.command)
                self._hang(False)  # The policy balances the robot itself.
            if mode == "pose" and self.mode != "pose":
                self.targets = np.array([self.data.qpos[q] for q in self.actuator_qpos])
            self.mode = mode

    def set_gantry(self, on: bool) -> None:
        with self.lock:
            self._hang(bool(on))

    def _hang(self, on: bool) -> None:
        """Hang the robot in the gantry, at its standing height and upright, over where it is."""
        if on:
            stand = self.model.key_qpos[self.model.key(STAND_KEY).id]
            q = self.free_q
            self.data.mocap_pos[self.hook] = [*self.data.qpos[q : q + 2], stand[q + 2]]
            self.data.mocap_quat[self.hook] = stand[q + 3 : q + 7]
        self.data.eq_active[self.harness] = on
        self.gantry = on

    def set_policy(self, policy: Policy | None, name: str = "") -> None:
        with self.lock:
            self.policy = policy
            self.policy_name = name
            self.runner = PolicyRunner(policy, self.design) if policy is not None else None
            if self.runner is not None:
                self.runner.set_command(*self.command)
            if policy is None and self.mode == "policy":
                self.mode = "stand"

    def jump(self) -> None:
        """Start a jump, when the policy running can jump."""
        with self.lock:
            if self.mode != "policy" or self.runner is None:
                raise ValueError("To jump, watch a jump policy first: Train, pick a jump run, Watch policy")
            if not self.runner.can_jump:
                raise ValueError(f"{self.policy_name} never learned to jump: watch a jump policy to jump")
            if not self.runner.jump():
                raise ValueError("Already jumping: wait for it to land")

    def set_targets(self, targets: dict[str, float]) -> None:
        with self.lock:
            for joint, value in targets.items():
                if joint in self.actuator_joints:
                    i = self.actuator_joints.index(joint)
                    low, high = self.model.actuator_ctrlrange[i]
                    self.targets[i] = float(np.clip(value, low, high)) if self.model.actuator_ctrllimited[i] else float(value)
            if self.mode not in ("pose", "mirror"):
                self.mode = "pose"

    def set_command(self, forward: float, left: float, turn: float) -> None:
        with self.lock:
            self.command = np.array([forward, left, turn], dtype=float)
            if self.runner is not None:
                self.runner.set_command(*self.command)

    def push_robot(self, force: tuple[float, float, float], seconds: float) -> None:
        """Push the torso with ``force`` (N, world axes) for ``seconds``."""
        with self.lock:
            self._push = (np.asarray(force, float), self.data.time + max(0.01, float(seconds)))

    def mirror(self, joints: dict[str, float], orientation: list[float] | None = None) -> None:
        """Show the real robot's measured joints (and IMU orientation, when known)."""
        with self.lock:
            self._mirror = {"joints": dict(joints), "orientation": orientation}

    # Running.

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="live-sim", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        last = time.perf_counter()
        sim_debt = 0.0
        next_frame = last
        while not self._stop.is_set():
            now = time.perf_counter()
            elapsed = now - last
            last = now
            with self.lock:
                if self.mode == "mirror":
                    self._apply_mirror()
                elif self.playing:
                    sim_debt = min(sim_debt + elapsed * self.rate, MAX_CATCH_UP)
                    steps = int(sim_debt / TIMESTEP)
                    for _ in range(steps):
                        self._step()
                    sim_debt -= steps * TIMESTEP
                frame = self.frame() if now >= next_frame else None
            if frame is not None:
                next_frame = now + 1.0 / FRAME_RATE
                for listener in list(self.listeners):
                    try:
                        listener(frame)
                    except Exception:  # A listener's failure must not stop the simulation.  # noqa: BLE001
                        pass
            time.sleep(0.002)

    def _step(self) -> None:
        model, data = self.model, self.data
        if self.mode == "policy" and self.runner is not None:
            joints = {name: float(data.qpos[model.jnt_qposadr[j]]) for name, j in self.joint_ids.items() if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE}
            speeds = {name: float(data.qvel[model.jnt_dofadr[j]]) for name, j in self.joint_ids.items() if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE}
            free = model.jnt_qposadr[next(j for j in range(model.njnt) if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE)]
            targets = self.runner.step(TIMESTEP, data.qpos[free + 3 : free + 7], data.qvel[3:6], joints, speeds)
            for i, joint in enumerate(self.actuator_joints):
                if joint in targets:
                    self.targets[i] = targets[joint]
        if self.mode == "stand":
            ctrl = self.stand_ctrl
        else:
            ctrl = self.targets
        if self.mode == "limp":
            data.ctrl[:] = 0
            model.actuator_forcerange[:, 0] = 0
            model.actuator_forcerange[:, 1] = 0
        else:
            low, high = model.actuator_ctrlrange[:, 0], model.actuator_ctrlrange[:, 1]
            data.ctrl[:] = np.where(model.actuator_ctrllimited.astype(bool), np.clip(ctrl, low, high), ctrl)
            speed = data.qvel[self.actuator_dofs]
            lo, hi = envelope(self.peak, self.corner, self.no_load, speed, 1.0)
            model.actuator_forcerange[:, 0] = lo
            model.actuator_forcerange[:, 1] = hi
        data.xfrc_applied[:] = 0
        if self._push is not None:
            force, until = self._push
            if data.time < until:
                data.xfrc_applied[self.torso, :3] = force
            else:
                self._push = None
        mujoco.mj_step(model, data)
        if not np.isfinite(data.qpos).all():
            self.message = "The simulation became unstable and was reset."
            self.reset()

    def _apply_mirror(self) -> None:
        if self._mirror is None:
            return
        model, data = self.model, self.data
        for joint, value in self._mirror["joints"].items():
            j = self.joint_ids.get(joint)
            if j is not None:
                data.qpos[model.jnt_qposadr[j]] = value
        orientation = self._mirror.get("orientation")
        if orientation is not None and len(orientation) == 4:
            data.qpos[3:7] = orientation
        mujoco.mj_kinematics(model, data)
        mujoco.mj_comPos(model, data)
        mujoco.mj_tendon(model, data)

    # What listeners get.

    def frame(self) -> dict:
        model, data = self.model, self.data
        motors = []
        for i in range(len(self.actuator_joints)):
            motors.append(
                [
                    round(float(data.qpos[self.actuator_qpos[i]]), 5),
                    round(float(data.qvel[self.actuator_dofs[i]]), 4),
                    round(float(data.actuator_force[i]), 3),
                    round(float(data.ctrl[i]), 5),
                ]
            )
        struts = []
        for _, tendon in self.struts:
            length = float(data.ten_length[tendon])
            force = float(model.tendon_stiffness[tendon] * (model.tendon_lengthspring[tendon][0] - length) - model.tendon_damping[tendon] * data.ten_velocity[tendon])
            struts.append([round(length, 5), round(force, 2)])
        base_up = data.xmat[self.base].reshape(3, 3)[:, 2]
        return {
            "type": "frame",
            "source": "mirror" if self.mode == "mirror" else "sim",
            "t": round(float(data.time), 4),
            "qpos": [round(float(v), 5) for v in data.qpos],
            "motors": motors,
            "struts": struts,
            "touch": {side: round(float(data.sensordata[adr]), 1) for side, adr in self.touch.items()},
            "com": [round(float(v), 4) for v in data.subtree_com[self.base]],
            "tilt": round(math.degrees(math.acos(max(-1.0, min(1.0, float(base_up[2]))))), 2),
            "mode": self.mode,
            "gantry": self.gantry,
            "stands_alone": self.stands_alone,
            "playing": self.playing,
            "policy": self.policy_name,
            "steers": bool(self.mode == "policy" and self.runner is not None and self.runner.policy.commands is not None),
            "can_jump": bool(self.mode == "policy" and self.runner is not None and self.runner.can_jump),
            "jumping": bool(self.runner is not None and self.runner.jumping is not None),
            "command": [round(float(v), 3) for v in self.command],
            "message": self.message,
        }
