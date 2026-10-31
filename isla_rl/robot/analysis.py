"""Physics of a hardware design, worked out without training: what the Design page shows.

For a set of design values (rod lengths, shock absorber spring, damping and
preload) this computes, from the robot's model:

- size: standing height, hip height, leg length, the body's standing lean;
- ankle: torsional stiffness from the shock absorbers, their net preload torque,
  the ankle travel the stroke allows, the passive-stability ratio and the damping
  ratio of the robot rocking on its ankles;
- motors: the torque each leg motor needs to hold the standing pose and a deeper
  crouch, against its peak, and the joint speed a walk needs against its limit.

The passive-stability ratio is the ankles' combined stiffness over the toppling
stiffness m*g*h of the robot as a rigid inverted pendulum. Below 1 the policy must
balance the robot. Above 1 the ankles could hold a rigid robot, but the leg servos
give way too, so whether it stands unaided is settled by simulating it: two
seconds holding the standing pose, motors on, nothing else helping.
"""

from __future__ import annotations

import copy
import math

import mujoco
import numpy as np

from .build import Built, top_height
from .design import DesignSpace, solve_stand
from .profile import RobotProfile

GRAVITY = 9.81
STAND_TEST = 2.0  # s the standing pose is held in the stand test.
STAND_DRIFT = 10.0  # degrees the body may turn in that time and still be standing.


def stand_drift(model: mujoco.MjModel, qpos: np.ndarray, ctrl: np.ndarray, base: int, seconds: float = STAND_TEST) -> float:
    """Degrees the base turns in ``seconds`` with the motors holding ``ctrl`` from ``qpos``
    (180 when the simulation fails). Runs on a scratch copy at a 2 ms step."""
    scratch = copy.copy(model)
    scratch.opt.timestep = 0.002
    data = mujoco.MjData(scratch)
    data.qpos[:] = qpos
    mujoco.mj_kinematics(scratch, data)
    start = data.xmat[base].reshape(3, 3)[:, 2].copy()
    data.ctrl[:] = ctrl
    while data.time < seconds:
        mujoco.mj_step(scratch, data)
        if data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number or not np.isfinite(data.qpos).all():
            return 180.0
    up = data.xmat[base].reshape(3, 3)[:, 2]
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(up, start))))))


def analyse(profile: RobotProfile, built: Built, cad: dict, design: dict[str, float]) -> dict:
    """Every number the Design page shows for ``design``, on the training model."""
    model0, info = built.model, built.info
    space = DesignSpace.create(profile, model0, info)
    model = copy.copy(model0)
    vector = space.vector(design)
    space.apply(model, vector)
    data = mujoco.MjData(model)
    pose = solve_stand(model, profile, info)
    data.qpos[:] = pose.qpos
    mujoco.mj_forward(model, data)
    mass = float(mujoco.mj_getTotalmass(model))
    weight = mass * GRAVITY
    base = model.body(info.base).id
    com_height = float(data.subtree_com[base][2])

    result: dict = {
        "design": {k: float(v) for k, v in zip(space.keys, vector, strict=True)},
        "size": {
            "height": top_height(model, data, cad),
            "hip_height": pose.hip_height,
            "com_height": com_height,
            "lean_deg": math.degrees(pose.pitch),
            "mass": mass,
        },
    }
    rods = {}
    for rod in info.rods:
        extension = float(vector[space.keys.index(next(k for k, p in profile.design.items() if p.rod == rod.name))]) / 1000.0 if any(p.rod == rod.name for p in profile.design.values()) else 0.0
        rods[rod.name] = {
            "segment_cad": rod.length_cad,
            "segment": rod.length_cad + extension,
            "rod_length": rod.rods[0]["length"] + extension if rod.rods else None,
        }
    result["rods"] = rods
    result["size"]["leg_length"] = float(sum(r["segment"] for r in rods.values())) if rods else pose.hip_height

    # The ankles.
    stand = profile.stand
    ankles = stand.get("ankles", [])
    forward = np.array(info.forward)
    lean_axis = np.cross([0.0, 0.0, 1.0], forward)
    ankle_info = {}
    if ankles and info.struts:
        joint = model.joint(ankles[0]).id
        adr, dof = model.jnt_qposadr[joint], model.jnt_dofadr[joint]
        sign = float(np.dot(data.xaxis[joint], lean_axis)) or 1.0
        probe = mujoco.MjData(model)

        def torque_at(offset: float) -> float:
            probe.qpos[:] = pose.qpos
            probe.qpos[adr] += offset / sign
            probe.qvel[:] = 0
            mujoco.mj_fwdPosition(model, probe)
            mujoco.mj_fwdVelocity(model, probe)
            return float(probe.qfrc_passive[dof]) * sign

        step = 0.01
        stiffness = -(torque_at(step) - torque_at(-step)) / (2 * step)
        preload_torque = torque_at(0.0)
        # The ankle's travel: how far each way before a shock absorber reaches its stop.
        lengths = []
        for strut in info.struts:
            if strut.side != info.struts[0].side:
                continue
            lengths.append((model.tendon(strut.tendon).id, strut.length_min, strut.length_max))

        def within(offset: float) -> bool:
            probe.qpos[:] = pose.qpos
            probe.qpos[adr] += offset / sign
            mujoco.mj_kinematics(model, probe)
            mujoco.mj_comPos(model, probe)
            mujoco.mj_tendon(model, probe)
            return all(lo - 1e-6 <= probe.ten_length[t] <= hi + 1e-6 for t, lo, hi in lengths)

        def travel(direction: float) -> float:
            low, high = 0.0, 0.8
            if not within(0.0):
                return 0.0
            for _ in range(40):
                middle = (low + high) / 2
                if within(direction * middle):
                    low = middle
                else:
                    high = middle
            return low

        toe_down, toe_up = travel(1.0), travel(-1.0)
        moment_of_inertia = mass * com_height**2
        toppling = mass * GRAVITY * com_height
        damping = 0.0
        for strut in info.struts:
            if strut.side != info.struts[0].side:
                continue
            tendon = model.tendon(strut.tendon).id
            arm = _moment_arm(model, pose.qpos, adr, tendon, sign)
            damping += model.tendon_damping[tendon] * arm**2
        total_k = len(ankles) * stiffness
        margin = total_k - toppling
        omega = math.sqrt(margin / moment_of_inertia) if margin > 0 else 0.0
        zeta = (len(ankles) * damping) / (2 * math.sqrt(max(margin, 1e-9) * moment_of_inertia)) if margin > 0 else 0.0
        struts = []
        for strut in info.struts:
            if strut.side != info.struts[0].side:
                continue
            tendon = model.tendon(strut.tendon).id
            length = float(data.ten_length[tendon])
            k = float(model.tendon_stiffness[tendon])
            free = float(model.tendon_lengthspring[tendon][0])
            struts.append(
                {
                    "position": strut.position,
                    "length": length,
                    "stroke_used": (length - strut.length_min) / max(strut.length_max - strut.length_min, 1e-9),
                    "force": k * (free - length),
                    "rate_n_per_mm": k / 1000.0,
                    "rate_lb_per_in": k * 0.0254 / 4.44822,
                    "damping": float(model.tendon_damping[tendon]),
                    "moment_arm": abs(_moment_arm(model, pose.qpos, adr, tendon, sign)),
                    "eye_to_eye_range": [strut.length_min, strut.length_max],
                }
            )
        ankle_info = {
            "stiffness": stiffness,
            "preload_torque": preload_torque,
            "cop_shift": len(ankles) * preload_torque / weight,
            "toe_down_deg": math.degrees(toe_down),
            "toe_up_deg": math.degrees(toe_up),
            "passive_ratio": total_k / toppling,
            "rocking_hz": omega / (2 * math.pi),
            "damping_ratio": zeta,
            "struts": struts,
        }
    result["ankle"] = ankle_info
    drift = stand_drift(model, pose.qpos, pose.ctrl, base)
    result["standing"] = {"seconds": STAND_TEST, "drift_deg": drift, "unaided": drift < STAND_DRIFT}

    # The motors: torque to hold the standing pose and a crouch, and walking speed.
    result["motors"] = {
        "stand": _holding_torques(model, data, info, weight),
    }
    crouch = copy.deepcopy(profile)
    crouch.data["stand"] = {**stand, "knee_flexion": float(stand.get("knee_flexion", 0.0)) + 0.5}
    try:
        crouch_pose = solve_stand(model, crouch, info)
        crouch_data = mujoco.MjData(model)
        crouch_data.qpos[:] = crouch_pose.qpos
        mujoco.mj_forward(model, crouch_data)
        result["motors"]["crouch"] = _holding_torques(model, crouch_data, info, weight)
        result["motors"]["crouch_lean_deg"] = math.degrees(crouch_pose.pitch)
    except (ValueError, RuntimeError):
        result["motors"]["crouch"] = {}
    peaks = {m.joint: m.peak_torque for m in info.motors}
    result["motors"]["peaks"] = peaks
    worst = max(
        (abs(t) / peaks[j] for j, t in result["motors"].get("crouch", {}).items() if j in peaks),
        default=0.0,
    )
    result["motors"]["crouch_load"] = worst
    speed = _walking_speed(profile, info, result["size"]["leg_length"])
    result["walking"] = speed
    return result


def _moment_arm(model: mujoco.MjModel, qpos: np.ndarray, adr: int, tendon: int, sign: float) -> float:
    """dL/dq of a tendon about the ankle, by central difference."""
    data = mujoco.MjData(model)
    h = 1e-4

    def length(offset: float) -> float:
        data.qpos[:] = qpos
        data.qpos[adr] += offset / sign
        mujoco.mj_kinematics(model, data)
        mujoco.mj_comPos(model, data)
        mujoco.mj_tendon(model, data)
        return float(data.ten_length[tendon])

    return (length(h) - length(-h)) / (2 * h)


def _holding_torques(model: mujoco.MjModel, data: mujoco.MjData, info, weight: float) -> dict[str, float]:
    """Motor torque each leg joint needs to hold this pose, each foot carrying half the weight.

    Static balance: motors + springs + floor = gravity. The floor pushes up under
    each ankle's centre of pressure; the generalised force it makes is J^T f.
    """
    probe = copy.copy(data)
    probe.qvel[:] = 0
    probe.qacc[:] = 0
    mujoco.mj_forward(model, probe)
    mujoco.mj_rne(model, probe, 0, probe.qfrc_bias)
    gravity = probe.qfrc_bias.copy()
    ground = np.zeros(model.nv)
    feet = info.feet
    for foot in feet:
        body = model.body(foot.body).id
        site = model.site(foot.site).id
        point = probe.site_xpos[site].copy()
        jac = np.zeros((3, model.nv))
        mujoco.mj_jac(model, probe, jac, None, point, body)
        ground += jac.T @ np.array([0.0, 0.0, weight / max(len(feet), 1)])
    needed = gravity - ground - probe.qfrc_passive
    torques = {}
    for motor in info.motors:
        joint = model.joint(motor.joint).id
        if "arm" in motor.group:
            continue
        torques[motor.joint] = float(needed[model.jnt_dofadr[joint]])
    return torques


def _walking_speed(profile: RobotProfile, info, leg_length: float) -> dict:
    """How fast the hips must swing to walk, against the macro actuator's no-load speed."""
    types = profile.actuator_types
    hip = next((m for m in info.motors if "hip_pitch" in m.joint), None)
    no_load = float(types.get(hip.type, {}).get("no_load_speed", 6.0)) if hip else 6.0
    period = 0.7
    rows = []
    for speed in (0.3, 0.5, 0.8, 1.0):
        step = speed * period / 2
        amplitude = math.asin(min(0.99, step / 2 / max(leg_length, 1e-3)))
        swing = 2 * math.pi / period * amplitude * 1.6  # Swing leg moves ~1.6x the mean rate.
        rows.append({"speed": speed, "hip_rate": swing, "share": swing / no_load})
    return {"period": period, "no_load_speed": no_load, "rows": rows}
