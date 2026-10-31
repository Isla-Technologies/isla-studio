"""Hardware design values, turned into model fields fast enough to vary per robot.

Building a model takes a fraction of a second; training changes the design of
thousands of practice robots. A :class:`DesignSpace` knows which compiled-model
fields each design value moves (a rod's length slides the body below it; a shock
absorber's spring, damping and preload set its tendon) and writes them directly.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import mujoco
import numpy as np
from numpy.typing import NDArray

from .build import BuildInfo, strut_values, top_height
from .profile import RobotProfile


@dataclass
class StandPose:
    qpos: NDArray
    ctrl: NDArray
    pitch: float  # rad the body leans forward.
    hip_height: float
    gravity: NDArray  # Down, in the base's axes, standing.


@dataclass
class DesignSpace:
    profile: RobotProfile
    info: BuildInfo
    keys: list[str] = field(default_factory=list)
    lows: NDArray = field(default_factory=lambda: np.zeros(0))
    highs: NDArray = field(default_factory=lambda: np.zeros(0))
    moved: list[int] = field(default_factory=list)  # Body ids slid by rods.
    moved_dir: NDArray = field(default_factory=lambda: np.zeros((0, 3)))
    moved_base: NDArray = field(default_factory=lambda: np.zeros((0, 3)))
    moved_key: list[str | None] = field(default_factory=list)  # Design key per moved body.
    tendons: list[int] = field(default_factory=list)
    tendon_position: list[str] = field(default_factory=list)
    tendon_max: NDArray = field(default_factory=lambda: np.zeros(0))  # Fully extended length.

    @classmethod
    def create(cls, profile: RobotProfile, model: mujoco.MjModel, info: BuildInfo) -> DesignSpace:
        params = profile.design
        keys = list(params)
        space = cls(profile, info, keys)
        space.lows = np.array([params[k].low for k in keys], dtype=float)
        space.highs = np.array([params[k].high for k in keys], dtype=float)
        rod_key = {p.rod: k for k, p in params.items() if p.kind == "rod"}
        for rod in info.rods:
            space.moved.append(model.body(rod.moved_body).id)
            space.moved_key.append(rod_key.get(rod.name))
        space.moved_dir = np.array([r.direction for r in info.rods], dtype=float).reshape(-1, 3)
        space.moved_base = np.array([r.base_pos for r in info.rods], dtype=float).reshape(-1, 3)
        for strut in info.struts:
            space.tendons.append(model.tendon(strut.tendon).id)
            space.tendon_position.append(strut.position)
        space.tendon_max = np.array([s.length_max for s in info.struts], dtype=float)
        return space

    @property
    def size(self) -> int:
        return len(self.keys)

    def vector(self, design: dict[str, float]) -> NDArray:
        full = self.profile.complete_design(design)
        return np.array([full[k] for k in self.keys], dtype=float)

    def as_dict(self, vector: NDArray) -> dict[str, float]:
        return {k: float(v) for k, v in zip(self.keys, vector, strict=True)}

    def normalize(self, vectors: NDArray) -> NDArray:
        span = np.where(self.highs > self.lows, self.highs - self.lows, 1.0)
        return (np.asarray(vectors, float) - self.lows) / span

    def denormalize(self, units: NDArray) -> NDArray:
        return self.lows + np.clip(units, 0.0, 1.0) * (self.highs - self.lows)

    def fields(self, vectors: NDArray) -> dict[str, NDArray]:
        """Model fields for a batch of designs (one row each, in physical units)."""
        vectors = np.atleast_2d(np.asarray(vectors, float))
        n = len(vectors)
        index = {k: i for i, k in enumerate(self.keys)}
        body_pos = np.repeat(self.moved_base[None], n, axis=0)
        for j, key in enumerate(self.moved_key):
            if key is not None:
                body_pos[:, j] += vectors[:, index[key], None] / 1000.0 * self.moved_dir[j]
        stiffness = np.zeros((n, len(self.tendons)))
        damping = np.zeros_like(stiffness)
        free = np.zeros_like(stiffness)
        for j, position in enumerate(self.tendon_position):
            for row in range(n):
                k, c, preload = strut_values(self.profile, self.as_dict(vectors[row]), position)
                stiffness[row, j], damping[row, j] = k, c
                free[row, j] = self.tendon_max[j] + preload
        return {"body_pos": body_pos, "tendon_stiffness": stiffness, "tendon_damping": damping, "tendon_free": free}

    def apply(self, model: mujoco.MjModel, design: dict[str, float] | NDArray) -> None:
        """Write a design into a compiled model, in place."""
        vector = design if isinstance(design, np.ndarray) else self.vector(design)
        f = self.fields(vector[None])
        for j, body in enumerate(self.moved):
            model.body_pos[body] = f["body_pos"][0, j]
        for j, tendon in enumerate(self.tendons):
            model.tendon_stiffness[tendon] = f["tendon_stiffness"][0, j]
            model.tendon_damping[tendon] = f["tendon_damping"][0, j]
            model.tendon_lengthspring[tendon] = [f["tendon_free"][0, j]] * 2


def solve_stand(model: mujoco.MjModel, profile: RobotProfile, info: BuildInfo) -> StandPose:
    """The standing pose: knees bent by the profile's amount, feet flat, ankles where
    the CAD has them (shock absorbers mid-stroke), and the body leaning so the centre
    of mass sits over the centre of pressure. A passive ankle cannot hold a lean: the
    shock absorbers' net torque moves the centre of pressure, and the body follows."""
    data = mujoco.MjData(model)
    stand = profile.stand
    qpos = model.qpos0.copy()
    base_body = model.body(info.base).id
    free = next(
        j for j in range(model.njnt)
        if model.jnt_bodyid[j] == base_body and int(model.jnt_type[j]) == mujoco.mjtJoint.mjJNT_FREE.value
    )
    adr = model.jnt_qposadr[free]
    keyframe = stand.get("keyframe")
    if keyframe and mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, keyframe) >= 0:
        qpos[:] = model.key(keyframe).qpos  # The robot's own standing pose.
    qpos[adr : adr + 2] = 0.0
    if not keyframe:
        qpos[adr + 2] = 0.0
        qpos[adr + 3 : adr + 7] = [1, 0, 0, 0]

    def set_joint(name: str, value: float) -> None:
        qpos[model.jnt_qposadr[model.joint(name).id]] = value

    signs = {k: float(v) for k, v in stand.get("flexion_sign", {}).items()}
    hips, knees, ankles = stand.get("hips", []), stand.get("knees", []), stand.get("ankles", [])
    forward = np.array(info.forward)
    lean_axis = np.cross([0.0, 0.0, 1.0], forward)
    knee_flexion = float(stand.get("knee_flexion", 0.0))
    ankle_offset = float(stand.get("ankle_offset", 0.0))
    data.qpos[:] = model.qpos0
    mujoco.mj_kinematics(model, data)
    pitch = {n: float(np.dot(data.xaxis[model.joint(n).id], lean_axis)) for n in (*hips, *knees, *ankles)}

    def pose(body_pitch: float) -> None:
        qpos[adr + 3 : adr + 7] = [math.cos(body_pitch / 2), *(math.sin(body_pitch / 2) * lean_axis)]
        for hip, knee, ankle in zip(hips, knees, ankles, strict=False):
            knee_angle = signs.get(knee, 1.0) * knee_flexion
            ankle_angle = signs.get(ankle, -signs.get(knee, 1.0)) * ankle_offset
            set_joint(knee, knee_angle)
            set_joint(ankle, ankle_angle)
            rest = body_pitch + pitch[knee] * knee_angle + pitch[ankle] * ankle_angle
            set_joint(hip, -rest / (pitch[hip] or 1.0))
        data.qpos[:] = qpos
        mujoco.mj_kinematics(model, data)
        mujoco.mj_comPos(model, data)

    body_pitch = 0.0
    if hips and knees and ankles:
        weight = float(mujoco.mj_getTotalmass(model)) * float(np.linalg.norm(model.opt.gravity))
        pose(0.0)
        data.qvel[:] = 0
        mujoco.mj_forward(model, data)
        toe = sum(
            float(data.qfrc_passive[model.jnt_dofadr[model.joint(a).id]])
            * float(np.dot(data.xaxis[model.joint(a).id], lean_axis))
            for a in ankles
        )
        offset = toe / weight  # The centre of pressure, ahead of the ankles.

        def lean(p: float) -> float:
            pose(p)
            com = data.subtree_com[base_body]
            middle = np.mean([data.xanchor[model.joint(a).id] for a in ankles], axis=0)
            return float(np.dot(com - middle, forward)) - offset

        low, high = -0.5, 0.8
        f_low, f_high = lean(low), lean(high)
        if f_low * f_high < 0:
            for _ in range(50):
                middle = (low + high) / 2
                f_mid = lean(middle)
                if f_low * f_mid <= 0:
                    high = middle
                else:
                    low, f_low = middle, f_mid
            body_pitch = (low + high) / 2
        pose(body_pitch)
    else:
        data.qpos[:] = qpos
        mujoco.mj_kinematics(model, data)

    lowest = math.inf
    for foot in info.feet:
        g = model.geom(foot.geom).id
        rotation = data.geom_xmat[g].reshape(3, 3)
        lowest = min(lowest, float(data.geom_xpos[g][2] - np.abs(rotation[2]) @ model.geom_size[g]))
    if not math.isfinite(lowest):
        lowest = 0.0
    qpos[adr + 2] -= lowest
    data.qpos[:] = qpos
    mujoco.mj_kinematics(model, data)
    ctrl = np.zeros(model.nu)
    for i in range(model.nu):
        value = qpos[model.jnt_qposadr[model.actuator_trnid[i, 0]]]
        if model.actuator_ctrllimited[i]:
            value = float(np.clip(value, *model.actuator_ctrlrange[i]))
        ctrl[i] = value
    hip_height = (
        float(np.mean([data.xanchor[model.joint(h).id][2] for h in hips])) if hips else float(data.xipos[base_body][2])
    )
    rotation = data.xmat[base_body].reshape(3, 3)
    gravity = rotation.T @ np.array([0.0, 0.0, -1.0])
    return StandPose(qpos, ctrl, body_pitch, hip_height, gravity)


def stand_for(profile: RobotProfile, model: mujoco.MjModel, info: BuildInfo, space: DesignSpace, vector: NDArray) -> StandPose:
    """The standing pose of one design, on a scratch copy of the model."""
    scratch = copy.copy(model)
    space.apply(scratch, vector)
    return solve_stand(scratch, profile, info)


def design_metrics(
    profile: RobotProfile, model: mujoco.MjModel, info: BuildInfo, space: DesignSpace, vector: NDArray, cad: dict
) -> tuple[StandPose, dict[str, float]]:
    """A design's standing pose and the numbers that describe it."""
    scratch = copy.copy(model)
    space.apply(scratch, vector)
    pose = solve_stand(scratch, profile, info)
    data = mujoco.MjData(scratch)
    data.qpos[:] = pose.qpos
    mujoco.mj_kinematics(scratch, data)
    mujoco.mj_comPos(scratch, data)
    metrics = {
        "height": top_height(scratch, data, cad),
        "hip_height": pose.hip_height,
        "com_height": float(data.subtree_com[scratch.body(info.base).id][2]),
        "pitch": math.degrees(pose.pitch),
    }
    return pose, metrics
