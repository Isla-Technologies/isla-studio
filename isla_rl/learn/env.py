"""Many simulated robots practising a skill at once, for training.

Each robot practises on a lean copy of the robot: only the soles touch the floor
and no shape is drawn, which makes a step several times quicker than the display
model. On the processor, MuJoCo's rollout steps a copy for each robot across the
cores and NumPy works out every robot's senses and rewards at once. On an NVIDIA
GPU, MuJoCo Warp steps them all together and CuPy does the same arithmetic there
(see gpu.py). The policy decides 50 times a second; the physics takes five 4 ms
steps between.

Motors follow a torque-speed envelope: full torque up to a corner speed, then
less, down to none at the no-load speed, while braking always gets full torque.
Past the no-load speed the motor's back-EMF beats the battery, so it brakes
whatever it is asked: a joint flung faster than its motor can turn is slowed,
not left to coast. This is what a geared BLDC on a battery does, and it is what
makes leg length a real trade-off: longer legs need more torque but less joint
speed.

When co-design is on, every robot draws its hardware (rod lengths, shock absorber
values) from the design pool at the start of each try; see codesign.py.

Each robot keeps two clocks. The gait clock runs a stride at a time; its stride
shortens, and each foot's time down with it, as the commanded speed rises from
walking to running (see :class:`isla_rl.learn.policy.Gait`). For a skill that
jumps, the jump clock waits a few seconds, then runs once through a jump: crouch
(the first quarter), take-off, flight and landing, and the rewards for each part
read it.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass

import mujoco
import numpy as np
from mujoco import rollout
from numpy.typing import NDArray

from ..robot.build import VIRTUAL_BODY, Built, build, measure
from ..robot.design import DesignSpace
from ..robot.profile import RobotProfile
from .codesign import DesignPool
from .device import CPU, GPU, Random, array_module, base_kinematics, module_of, to_numpy
from .policy import GYRO_SCALE, SPEED_SCALE, Gait, Policy, frame_matrix, observe, quat_rotate_inverse, size_of
from .ppo import PPO
from .skills import TrainingSetup

RATE = 50.0
PHYSICS_STEP = 0.004
SUBSTEPS = 5
DT = 1.0 / RATE
CONTACT_FORCE = 5.0  # N on a foot that counts as touching.
TAKE_OFF = (0.2, 0.55)  # Share of a jump in which rising is rewarded.
FLIGHT = (0.35, 0.8)  # Share of a jump in which being in the air and high is rewarded.
FALL_TILT = math.cos(math.radians(50))
FALL_RATIO = 0.6  # Hips below this share of standing height: fallen.
COMMAND_SECONDS = 8.0
STILL = 0.05  # Commands smaller than this (m/s, rad/s) ask the robot to stand still, feet down.
ACTION_SCALE = 0.25
ACTION_CLIP = 5.0
STRUT_MARGIN = 0.0008  # m from the end of the stroke counts as on the stop.
SOLVER_ITERATIONS = 12


@dataclass(frozen=True, slots=True)
class Episodes:
    """Tries that ended on one move."""

    returns: NDArray
    seconds: NDArray
    falls: NDArray
    speed_errors: NDArray
    terms: NDArray
    design: NDArray
    generation: NDArray
    impact: NDArray  # Hardest foot strike, times the weight.
    power: NDArray  # Mean mechanical power, W.


def lean_model(profile: RobotProfile, design: dict[str, float]) -> Built:
    built = build(profile, design, purpose="train")
    model = built.model
    model.opt.timestep = PHYSICS_STEP
    model.opt.iterations = SOLVER_ITERATIONS
    model.opt.ls_iterations = SOLVER_ITERATIONS
    return built


def envelope(peak: NDArray, corner: NDArray, no_load: NDArray, speed: NDArray, strength) -> tuple[NDArray, NDArray]:
    """Lowest and highest torque each motor can give at its joint speed.

    Driving the joint on in the direction it turns falls from full torque at the corner
    speed to none at the no-load speed, and below none past it: there the motor brakes
    however it is driven, down to full braking. Torque against the motion is always there.
    """
    xp = module_of(speed)
    top = peak * xp.minimum(strength, 1.0)
    fast = xp.abs(speed)
    span = xp.maximum(no_load - corner, 1e-6)
    motoring = top * xp.clip((no_load - fast) / span, -1.0, 1.0)
    motoring = xp.where(fast <= corner, top, motoring)
    high = xp.where(speed > 0, motoring, top)
    low = xp.where(speed < 0, -motoring, -top)
    return low, high


class CpuPhysics:
    """MuJoCo's rollout: a copy of the model for each robot, stepped across the cores."""

    def __init__(self, model: mujoco.MjModel, count: int, threads: int | None = None) -> None:
        self.models = [copy.copy(model) for _ in range(count)]
        threads = threads or max(1, (os.cpu_count() or 2) - 1)
        self.datas = [mujoco.MjData(model) for _ in range(threads)]
        self.runner = rollout.Rollout(nthread=threads)
        nstate = mujoco.mj_stateSize(model, mujoco.mjtState.mjSTATE_FULLPHYSICS)
        if nstate != 1 + model.nq + model.nv + model.na:
            raise ValueError("Training supports robots without mocap bodies, history or plugins")
        self.nq, self.nv = model.nq, model.nv
        self.state = np.zeros((count, nstate))
        self.qpos = self.state[:, 1 : 1 + model.nq]
        self.qvel = self.state[:, 1 + model.nq : 1 + model.nq + model.nv]
        self._control = np.zeros((count, SUBSTEPS, model.nu))
        self._states = np.zeros((count, SUBSTEPS, nstate))
        self._sensors = np.zeros((count, SUBSTEPS, model.nsensordata))

    def set_rows(self, name: str, ids: NDArray, rows: NDArray, columns: NDArray | None = None) -> None:
        """Write a model field for some robots: ``rows[k]`` goes to robot ``ids[k]``."""
        for robot, row in zip(ids.tolist(), rows, strict=True):
            field = getattr(self.models[robot], name)
            if columns is None:
                field[...] = row
            else:
                field[columns] = row

    def set_forcerange(self, low: NDArray, high: NDArray) -> None:
        for model, lo, hi in zip(self.models, low, high, strict=True):
            fr = model.actuator_forcerange
            fr[:, 0] = lo
            fr[:, 1] = hi

    def place(self, ids: NDArray, qpos: NDArray) -> None:
        self.state[ids] = 0.0
        self.qpos[ids] = qpos

    def step(self, ctrl: NDArray, last_ctrl: NDArray, delay: NDArray) -> None:
        control = self._control
        control[:] = ctrl[:, None, :]
        for substep in range(1, int(delay.max(initial=0)) + 1):
            late = delay >= substep
            control[late, substep - 1] = last_ctrl[late]
        self.runner.rollout(
            self.models,
            self.datas,
            self.state,
            control,
            skip_checks=True,
            nstep=SUBSTEPS,
            state=self._states,
            sensordata=self._sensors,
        )
        self.state[:] = self._states[:, -1]

    def peak(self, adr: list[int]) -> NDArray:
        """Highest reading over the move's steps, for these sensor addresses."""
        return self._sensors[:, :, adr].max(axis=1)

    def any_beyond(self, adr: list[int], low: NDArray, high: NDArray) -> NDArray:
        values = self._sensors[:, :, adr]
        return ((values < low) | (values > high)).any(axis=1)

    @property
    def sensors(self) -> NDArray:
        return self._sensors[:, -1]

    def close(self) -> None:
        self.runner.close()


class PracticeRobots:
    """Robots practising one skill, each in its own simulation, stepped together."""

    def __init__(
        self,
        profile: RobotProfile,
        setup: TrainingSetup,
        device: str = CPU,
        threads: int | None = None,
    ) -> None:
        self.profile, self.setup, self.device = profile, setup, device
        self.xp = xp = array_module(device)
        self.dtype = dtype = np.float32 if device == GPU else np.float64
        self.skill = skill = setup.about
        self.rng = Random.seeded(setup.seed, xp)
        built = lean_model(profile, profile.complete_design(setup.design))
        self.built = built
        model, info = built.model, built.info
        self.model, self.info = model, info
        self.count = n = setup.robots
        self.weight = float(mujoco.mj_getTotalmass(model)) * 9.81
        base = model.body(info.base).id
        free = next(
            j for j in range(model.njnt)
            if model.jnt_bodyid[j] == base and int(model.jnt_type[j]) == mujoco.mjtJoint.mjJNT_FREE.value
        )
        if model.jnt_qposadr[free] != 0 or model.jnt_dofadr[free] != 0:
            raise ValueError("Training needs the robot's free-moving base first in the model")
        self.nq, self.nv, self.nu = model.nq, model.nv, model.nu

        # Which motors the policy drives: legs and waist, or everything.
        motors = info.motors
        drives_arms = setup.arms or not any("arm" in m.group for m in motors)
        driven = [m for m in motors if drives_arms or "arm" not in m.group]
        if not driven:
            raise ValueError("This robot has no motors for the policy to drive")
        self.joints = tuple(m.joint for m in driven)
        act_ids = np.array([model.actuator(m.actuator).id for m in driven])
        joint_ids = [model.joint(m.joint).id for m in driven]
        qpos_ids = np.array([model.jnt_qposadr[j] for j in joint_ids])
        dof_ids = np.array([model.jnt_dofadr[j] for j in joint_ids])
        names = list(self.joints)
        hip_ids = np.array([i for i, nm in enumerate(names) if "hip_roll" in nm or "hip_yaw" in nm], dtype=int)
        waist_ids = np.array([i for i, nm in enumerate(names) if nm.startswith("waist")], dtype=int)
        low, high = model.actuator_ctrlrange[act_ids, 0], model.actuator_ctrlrange[act_ids, 1]
        middle, half = (low + high) / 2, (high - low) / 2
        passive = [model.joint(p).id for p in info.passive]
        types = {m.joint: profile.actuator_types[m.type] for m in motors}
        all_names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, model.actuator_trnid[a, 0]) for a in range(self.nu)]
        peak = np.array([float(types[nm].get("peak_torque", 20.0)) if nm in types else 1e6 for nm in all_names])
        corner = np.array([float(types[nm].get("corner_speed", 1e6)) if nm in types else 1e6 for nm in all_names])
        no_load = np.array([float(types[nm].get("no_load_speed", 2e6)) if nm in types else 2e6 for nm in all_names])
        no_load = np.maximum(no_load, corner + 1e-3)
        actuator_dofs = np.array([model.jnt_dofadr[model.actuator_trnid[a, 0]] for a in range(self.nu)])

        # The design space and the pool of candidate designs.
        self.space = DesignSpace.create(profile, model, info)
        self.pool = DesignPool(
            profile, model, info, self.space, measure(profile), setup.codesign,
            profile.complete_design(setup.design), setup.seed,
        )
        self.senses_design = self.pool.searching
        design_size = self.space.size if self.senses_design else 0
        self.terms = (*skill.terms, "design") if self.senses_design else tuple(skill.terms)

        # Sensors, feet, shock absorbers.
        adr = model.sensor_adr
        self.feet = [f.side for f in info.feet]
        self.touch_adr = [int(adr[model.sensor(f"isla_touch_{s}").id]) for s in self.feet]
        self.sole_pos_adr = [int(adr[model.sensor(f"isla_sole_{s}_pos").id]) for s in self.feet]
        self.sole_vel_adr = [int(adr[model.sensor(f"isla_sole_{s}_vel").id]) for s in self.feet]
        self.strut_adr = [int(adr[model.sensor(f"{s.tendon}_length").id]) for s in info.struts]
        strut_low = np.array([s.length_min for s in info.struts]) + STRUT_MARGIN
        strut_high = np.array([s.length_max for s in info.struts]) - STRUT_MARGIN
        strut_mid = np.array([(s.length_min + s.length_max) / 2 for s in info.struts])
        strut_half = np.array([(s.length_max - s.length_min) / 2 for s in info.struts])
        sole_geoms = np.array([model.geom(f.geom).id for f in info.feet])
        mass_body = model.body(VIRTUAL_BODY).id if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, VIRTUAL_BODY) >= 0 else base
        if mass_body == base and info.torso:
            mass_body = model.body(info.torso).id
        self.mass_body = mass_body
        to_robot = frame_matrix(info.forward, info.left)
        self.to_robot_np = to_robot

        # The pelvis: the middle of the hips, in the base's frame.
        data = mujoco.MjData(model)
        data.qpos[:] = info.stand_qpos
        mujoco.mj_kinematics(model, data)
        hips = [model.joint(h).id for h in profile.stand.get("hips", [])]
        point = np.mean([data.xanchor[h] for h in hips], axis=0) if hips else data.xipos[base]
        pelvis = data.xmat[base].reshape(3, 3).T @ (point - data.xpos[base])

        varied_kp = model.actuator_gainprm[:, 0].copy()
        varied_kd = -model.actuator_biasprm[:, 2].copy()
        if device == GPU:
            from .gpu import GpuPhysics

            self.physics = GpuPhysics(
                model, data, n, actuator_dofs, peak, corner, no_load, self.touch_adr, self.strut_adr,
                strut_low, strut_high, sole_geoms, mass_body, np.array(self.space.moved, dtype=int),
                np.array(self.space.tendons, dtype=int),
            )
        else:
            self.physics = CpuPhysics(model, n, threads)

        def put(array, kind=None):
            return xp.asarray(array, dtype=kind if kind is not None else dtype)

        self.act_ids, self.qpos_ids, self.dof_ids = put(act_ids, int), put(qpos_ids, int), put(dof_ids, int)
        self.hip_ids, self.waist_ids = put(hip_ids, int), put(waist_ids, int)
        self.passive_q = put([model.jnt_qposadr[j] for j in passive], int)
        self.soft_low, self.soft_high = put(middle - 0.9 * half), put(middle + 0.9 * half)
        self.ctrl_low = put(model.actuator_ctrlrange[:, 0])
        self.ctrl_high = put(model.actuator_ctrlrange[:, 1])
        self.peak, self.corner, self.no_load = put(peak), put(corner), put(no_load)
        self.actuator_dofs = put(actuator_dofs, int)
        self.actuator_dofs_np = actuator_dofs
        self.kp0, self.kd0 = put(varied_kp), put(varied_kd)
        self.strut_low, self.strut_high = put(strut_low), put(strut_high)
        self.strut_mid, self.strut_half = put(strut_mid), put(np.maximum(strut_half, 1e-6))
        self.pelvis = put(pelvis)
        self.forward = put(info.forward)
        self.to_robot = put(to_robot.T)
        self.to_robot_rows = put(to_robot)
        self.sole_geoms = sole_geoms
        self.mass0 = float(model.body_mass[mass_body])
        self.inertia0 = model.body_inertia[mass_body].copy()
        self.tendon_ids = np.array(self.space.tendons, dtype=int)
        self.moved_ids = np.array(self.space.moved, dtype=int)
        self.kp0_np, self.kd0_np = varied_kp, varied_kd
        self.inertia0_x = put(self.inertia0)
        self.peak_driven = put(peak[act_ids])
        self.sole_pos_idx = put([[a, a + 1, a + 2] for a in self.sole_pos_adr], int).reshape(-1, 3)
        self.sole_vel_idx = put([[a, a + 1, a + 2] for a in self.sole_vel_adr], int).reshape(-1, 3)
        self.strut_idx = put(self.strut_adr, int)
        self.strut_low_np, self.strut_high_np = strut_low, strut_high
        self.foot_offsets = put([0.5 * (i % 2) for i in range(len(self.feet))])

        a = len(self.joints)
        self.weights = setup.reward_weights
        self.reward_terms = [k for k in self.weights if k != "fall"]
        self.term_weights = put(np.array([self.weights[k] for k in self.reward_terms]) * DT)
        self.obs_size = size_of(self.terms, a, design_size)
        extra = 3 + 1 + 2 * len(self.feet) + 3 + len(passive) + len(info.struts)
        self.critic_size = self.obs_size + extra
        self.noise = put(self._noise_scales(setup.robustness.noise, a, design_size))
        self.max_steps = int(round(setup.episode_seconds * RATE))
        self.gait: Gait | None = skill.gait
        self.jumping = skill.jumping
        self.progress = 0.0  # Share of the run's practice done: the trainer keeps it up to date.

        # Every robot's own state.
        self.steps = xp.zeros(n, int)
        self.actions = xp.zeros((n, a), np.float32)
        self.last_actions = xp.zeros((n, a), np.float32)
        self.last_speeds = xp.zeros((n, a), dtype)
        self.last_ctrl = xp.zeros((n, self.nu), dtype)
        self.base_ctrl = xp.zeros((n, self.nu), dtype)
        self.default = xp.zeros((n, a), dtype)
        self.stand_height = xp.ones(n, dtype)
        self.gravity_ref = xp.zeros((n, 3), dtype)
        self.design_units = xp.zeros((n, max(design_size, 1)), np.float32)
        self.design_index = xp.zeros(n, int)
        self.design_vectors = xp.zeros((n, max(self.space.size, 1)), dtype)
        self.design_generation = xp.zeros(n, int)
        self.command = xp.zeros((n, 3), dtype)
        self.command_timer = xp.zeros(n)
        self.push_timer = xp.zeros(n)
        self.friction = xp.ones(n)
        self.added_mass = xp.zeros(n)
        self.strength = xp.ones(n)
        self.delay = xp.zeros(n, int)
        self.contacts = xp.ones((n, len(self.feet)), bool)
        self.foot_height = xp.zeros((n, len(self.feet)), dtype)
        self.phase = xp.zeros(n, dtype)
        self.jump_on = xp.zeros(n, bool)
        self.jump_share = xp.zeros(n, dtype)
        self.jump_timer = xp.zeros(n, dtype)
        self.returns = xp.zeros(n)
        self.term_sums = xp.zeros((n, len(self.reward_terms) + 1))
        self.speed_error = xp.zeros(n)
        self.impact_peak = xp.zeros(n)
        self.power_sum = xp.zeros(n)
        self.total_steps = 0
        self._pool_generation = -1
        self._forced = xp.zeros(n, bool)
        self._sync_pool()

    # The design pool, where the arithmetic runs.

    def _sync_pool(self) -> None:
        pool = self.pool
        if pool.generation == self._pool_generation:
            return
        xp, dtype = self.xp, self.dtype
        vectors = np.array([c.vector for c in pool.candidates])
        f = self.space.fields(vectors)
        self.pool_body_pos = xp.asarray(f["body_pos"], dtype)
        self.pool_k = xp.asarray(f["tendon_stiffness"], dtype)
        self.pool_c = xp.asarray(f["tendon_damping"], dtype)
        self.pool_free = xp.asarray(f["tendon_free"], dtype)
        self.pool_qpos = xp.asarray(np.array([c.qpos for c in pool.candidates]), dtype)
        ctrl = np.array([c.ctrl for c in pool.candidates])
        self.pool_ctrl = xp.asarray(ctrl, dtype)
        self.pool_default = xp.asarray(ctrl[:, to_numpy(self.act_ids)], dtype)
        self.pool_height = xp.asarray([c.hip_height for c in pool.candidates], dtype)
        gravity = np.array([c.gravity for c in pool.candidates]) @ self.to_robot_np.T
        self.pool_gravity = xp.asarray(gravity, dtype)
        self.pool_units = xp.asarray(np.array([c.units for c in pool.candidates]), np.float32)
        self.pool_vectors = xp.asarray(vectors, dtype)
        self.design_points = xp.zeros(len(pool.candidates))
        self.design_seconds = xp.zeros(len(pool.candidates))
        if self._pool_generation >= 0:
            self._forced[:] = True  # Everyone starts again on the new pool.
        self._pool_generation = pool.generation

    def pool_changed(self) -> None:
        """Take a new pool: every robot restarts on it at the next move."""
        self._sync_pool()

    def design_scores(self):
        """Points and robot-seconds each candidate of the current pool has collected."""
        return to_numpy(self.design_points).copy(), to_numpy(self.design_seconds).copy()

    @property
    def state(self) -> NDArray:
        physics = self.physics
        if isinstance(physics, CpuPhysics):
            return physics.state
        xp = self.xp
        return xp.concatenate([xp.zeros((self.count, 1), self.dtype), physics.qpos, physics.qvel], 1)

    def close(self) -> None:
        self.physics.close()

    # Starting tries.

    def reset(self) -> tuple[NDArray, NDArray]:
        self._reset(self.xp.arange(self.count))
        return self._observe()

    def _reset(self, ids) -> None:
        xp, robust = self.xp, self.setup.robustness
        k = len(ids)
        if not k:
            return
        self._sync_pool()
        pick = xp.asarray(self.pool.pick(k))
        self.design_index[ids] = pick
        self.design_generation[ids] = self.pool.generation
        if self.space.size:
            self.design_vectors[ids] = self.pool_vectors[pick]
        if self.senses_design:
            self.design_units[ids] = self.pool_units[pick]
        # Everything random a new try needs, in one draw.
        struts, joints = len(self.tendon_ids), len(self.joints)
        u = self.rng.random((k, 6 + 2 * struts + joints))

        def lerp(pair, column):
            return pair[0] + (pair[1] - pair[0]) * column

        self.friction[ids] = lerp(robust.friction, u[:, 0])
        self.added_mass[ids] = lerp(robust.added_mass, u[:, 1])
        self.strength[ids] = lerp(robust.strength, u[:, 2])
        most = int(round(robust.delay / PHYSICS_STEP))
        self.delay[ids] = xp.floor(u[:, 3] * (most + 1)).astype(int)
        self.push_timer[ids] = lerp(self.setup.push_every, u[:, 4])
        spread = robust.strut_spread
        k_scale = 1.0 + spread * (2.0 * u[:, 6 : 6 + struts] - 1.0) if struts else None
        c_scale = 1.0 + spread * (2.0 * u[:, 6 + struts : 6 + 2 * struts] - 1.0) if struts else None
        self._vary(ids, pick, k_scale, c_scale)

        qpos = self.pool_qpos[pick]
        default = self.pool_default[pick]
        start = default + (0.1 * u[:, 6 + 2 * struts :] - 0.05)
        qpos[:, self.qpos_ids] = start
        qpos[:, 2] += 0.003  # A hair above the floor.
        self.physics.place(ids, qpos)
        ctrl = self.pool_ctrl[pick]
        self.base_ctrl[ids] = ctrl
        ctrl[:, self.act_ids] = start
        self.last_ctrl[ids] = xp.clip(ctrl, self.ctrl_low, self.ctrl_high)
        self.default[ids] = default
        self.stand_height[ids] = self.pool_height[pick]
        self.gravity_ref[ids] = self.pool_gravity[pick]
        self.steps[ids] = 0
        self.actions[ids] = 0.0
        self.last_actions[ids] = 0.0
        self.last_speeds[ids] = 0.0
        self.contacts[ids] = True
        self.foot_height[ids] = 0.0
        self.phase[ids] = 0.0
        self.jump_on[ids] = False
        self.jump_share[ids] = 0.0
        if self.jumping is not None:
            first = self.jumping.first
            self.jump_timer[ids] = first[0] + (first[1] - first[0]) * self.rng.random(k)
        self.returns[ids] = 0.0
        self.term_sums[ids] = 0.0
        self.speed_error[ids] = 0.0
        self.impact_peak[ids] = 0.0
        self.power_sum[ids] = 0.0
        self._new_commands(ids)

    def _vary(self, ids, pick, k_scale, c_scale) -> None:
        """Write each starting robot's design and differences into its physics."""
        mass = self.mass0 + self.added_mass[ids]
        inertia = self.inertia0_x[None, :] * (mass / self.mass0)[:, None]
        strength = self.strength[ids]
        body_pos = self.pool_body_pos[pick]
        k = self.pool_k[pick] * k_scale if k_scale is not None else None
        c = self.pool_c[pick] * c_scale if c_scale is not None else None
        free = self.pool_free[pick]
        physics = self.physics
        if not isinstance(physics, CpuPhysics):
            physics.vary(ids, self.friction[ids], mass, inertia, strength, body_pos, k, c, free)
            return
        robots = to_numpy(ids).tolist()
        friction, mass, inertia, strength = (to_numpy(v) for v in (self.friction[ids], mass, inertia, strength))
        bp = to_numpy(body_pos)
        kn, cn, fn = (to_numpy(v) if v is not None else None for v in (k, c, free))
        for row, robot in enumerate(robots):
            m = physics.models[robot]
            m.geom_friction[self.sole_geoms, 0] = friction[row]
            m.body_mass[self.mass_body] = mass[row]
            m.body_inertia[self.mass_body] = inertia[row]
            m.actuator_gainprm[:, 0] = self.kp0_np * strength[row]
            m.actuator_biasprm[:, 1] = -self.kp0_np * strength[row]
            m.actuator_biasprm[:, 2] = -self.kd0_np * strength[row]
            if len(self.moved_ids):
                m.body_pos[self.moved_ids] = bp[row]
            if kn is not None:
                m.tendon_stiffness[self.tendon_ids] = kn[row]
                m.tendon_damping[self.tendon_ids] = cn[row]
                m.tendon_lengthspring[self.tendon_ids, 0] = fn[row]
                m.tendon_lengthspring[self.tendon_ids, 1] = fn[row]

    def _new_commands(self, ids) -> None:
        k = len(ids)
        self.command_timer[ids] = COMMAND_SECONDS
        if "command" not in self.skill.terms or not k:
            return
        xp, commands, skill = self.xp, self.setup.command_ranges, self.skill
        u = self.rng.random((k, 5))
        low, high = commands.forward
        if skill.curriculum is not None:  # Top speed opens up as the practice goes on.
            start, share = skill.curriculum
            high = min(high, start + (high - start) * min(1.0, self.progress / max(share, 1e-6)))
        forward = low + (high - low) * u[:, 0]
        if skill.run_practice > 0:  # Some commands near the top speed reached so far.
            fast = 0.75 * high + 0.25 * high * u[:, 0]
            forward = xp.where(u[:, 4] < skill.run_practice, fast, forward)
        command = xp.stack(
            [
                forward,
                commands.sideways * (2 * u[:, 1] - 1),
                commands.turning * (2 * u[:, 2] - 1),
            ],
            axis=1,
        )
        small = xp.linalg.norm(command[:, :2], axis=1, keepdims=True) < 0.08
        command[:, :2] = xp.where(small, 0.0, command[:, :2])
        command = xp.where((u[:, 3] < 0.1)[:, None], 0.0, command)  # Sometimes: stand still.
        self.command[ids] = command

    # One move.

    def step(self, actions):
        n, xp = self.count, self.xp
        actions = xp.clip(xp.asarray(actions, self.dtype), -ACTION_CLIP, ACTION_CLIP)
        ctrl = self.base_ctrl.copy()
        ctrl[:, self.act_ids] = self.default + ACTION_SCALE * actions
        xp.clip(ctrl, self.ctrl_low, self.ctrl_high, out=ctrl)
        physics = self.physics
        if isinstance(physics, CpuPhysics):
            speed = physics.qvel[:, self.actuator_dofs_np]
            low, high = envelope(self.peak, self.corner, self.no_load, speed, self.strength[:, None])
            physics.set_forcerange(low, high)
        physics.step(ctrl, self.last_ctrl, self.delay)
        self.last_ctrl[:] = ctrl
        self.last_actions[:] = self.actions
        self.actions[:] = actions
        self.steps += 1
        self.total_steps += n
        self._tick()

        sensed = self._measure(ctrl)
        terms = self._rewards(sensed)
        points = terms @ self.term_weights
        xp.maximum(points, 0.0, out=points)  # Only good moments score; falling is extra.
        fell = sensed["fell"]
        fall_points = self.weights.get("fall", 0.0) * fell
        points = points + fall_points
        self.returns += points
        if self.pool.searching:
            current = self.design_generation == self._pool_generation
            index = self.design_index[current]
            if self.xp is np:
                np.add.at(self.design_points, index, points[current])
                np.add.at(self.design_seconds, index, DT)
            else:
                from cupyx import scatter_add

                scatter_add(self.design_points, index, points[current].astype(self.design_points.dtype))
                scatter_add(self.design_seconds, index, self.xp.full(index.shape, DT, self.design_seconds.dtype))
        self.term_sums[:, :-1] += terms * self.term_weights
        self.term_sums[:, -1] += fall_points
        if "command" in self.skill.terms:
            self.speed_error += xp.linalg.norm(self.command[:, :2] - sensed["speed"][:, :2], axis=1)
        xp.maximum(self.impact_peak, sensed["impact"], out=self.impact_peak)
        self.power_sum += sensed["power"]
        self.last_speeds[:] = sensed["velocities"]
        self.contacts[:] = sensed["contacts"]
        self.foot_height[:] = sensed["foot_height"]

        timeouts = ((self.steps >= self.max_steps) | self._forced) & ~fell
        self._forced[:] = False
        dones = fell | timeouts
        ended = xp.flatnonzero(dones)
        if len(ended):
            steps_done = xp.maximum(self.steps[ended], 1).astype(self.dtype)
            # One copy back to the processor for everything the tries report.
            table = to_numpy(
                xp.concatenate(
                    [
                        xp.stack(
                            [
                                self.returns[ended],
                                steps_done * DT,
                                fell[ended].astype(self.dtype),
                                self.speed_error[ended] / steps_done,
                                self.design_index[ended].astype(self.dtype),
                                self.design_generation[ended].astype(self.dtype),
                                self.impact_peak[ended],
                                self.power_sum[ended] / steps_done,
                            ],
                            axis=1,
                        ),
                        self.term_sums[ended],
                    ],
                    axis=1,
                )
            ).astype(np.float64)
            episodes = Episodes(
                returns=table[:, 0],
                seconds=table[:, 1],
                falls=table[:, 2] > 0.5,
                speed_errors=table[:, 3],
                terms=table[:, 8:],
                design=table[:, 4].astype(int),
                generation=table[:, 5].astype(int),
                impact=table[:, 6],
                power=table[:, 7],
            )
            self._reset(ended)
        else:
            empty = np.zeros(0)
            episodes = Episodes(empty, empty, empty.astype(bool), empty, np.zeros((0, len(self.reward_terms) + 1)),
                                empty.astype(int), empty.astype(int), empty, empty)
        self.command_timer -= DT
        renew = xp.flatnonzero(self.command_timer <= 0)
        if len(renew):
            self._new_commands(renew)
        self._push()
        obs, critic = self._observe()
        return obs, critic, points.astype(np.float32), dones, timeouts, episodes

    def _push(self) -> None:
        self.push_timer -= DT
        due = self.xp.flatnonzero(self.push_timer <= 0)
        if not len(due):
            return
        speed, every = self.setup.push_speed, self.setup.push_every
        u = self.rng.random((len(due), 3))
        self.physics.qvel[due, 0:2] += speed * (2 * u[:, :2] - 1)
        self.push_timer[due] = every[0] + (every[1] - every[0]) * u[:, 2]

    def _run_share(self):
        """0 walking to 1 running, for each robot's commanded speed."""
        if self.gait is None:
            return self.xp.zeros(self.count, self.dtype)
        return self.gait.run_share(self.command[:, 0]).astype(self.dtype)

    def _tick(self) -> None:
        """Advance every robot's gait clock and jump clock by one move."""
        if self.gait is not None:
            self.phase[:] = (self.phase + DT / self.gait.period_at(self._run_share())) % 1.0
        jumping = self.jumping
        if jumping is None:
            return
        xp = self.xp
        self.jump_share[:] = xp.where(self.jump_on, self.jump_share + DT / jumping.seconds, 0.0)
        landed = self.jump_on & (self.jump_share >= 1.0)
        self.jump_timer[:] = xp.where(self.jump_on, self.jump_timer, self.jump_timer - DT)
        start = ~self.jump_on & (self.jump_timer <= 0.0)
        if bool(landed.any()):
            every = jumping.every
            pause = every[0] + (every[1] - every[0]) * self.rng.random(self.count)
            self.jump_timer[:] = xp.where(landed, pause, self.jump_timer)
            self.jump_share[:] = xp.where(landed, 0.0, self.jump_share)
        self.jump_on[:] = (self.jump_on & ~landed) | start

    def _kinematics(self, qpos, qvel) -> dict:
        """The base's motion and what follows from it, in one pass."""
        speed, yaw_rate, height, gravity, turn = base_kinematics(
            qpos, qvel, self.pelvis, self.forward, self.to_robot_rows
        )
        return {"speed": speed, "yaw_rate": yaw_rate, "height": height, "gravity": gravity, "turn": turn}

    def _measure(self, ctrl) -> dict:
        xp, physics = self.xp, self.physics
        qpos, qvel = physics.qpos, physics.qvel
        s = self._kinematics(qpos, qvel)
        positions, velocities = qpos[:, self.qpos_ids], qvel[:, self.dof_ids]
        torque = (self.kp0[self.act_ids] * (ctrl[:, self.act_ids] - positions) - self.kd0[self.act_ids] * velocities) * self.strength[:, None]
        torque = xp.clip(torque, -self.peak_driven, self.peak_driven)
        sensors = physics.sensors
        if isinstance(physics, CpuPhysics):
            touch = xp.asarray(physics.peak(self.touch_adr))
            on_stop = (
                xp.asarray(physics.any_beyond(self.strut_adr, self.strut_low_np, self.strut_high_np))
                if self.strut_adr else xp.zeros((self.count, 0), bool)
            )
        else:
            touch, on_stop = physics.touch, physics.on_stop
        upright = s["gravity"][:, 2] < -FALL_TILT
        s.update(
            positions=positions,
            velocities=velocities,
            torque=torque,
            power=xp.sum(xp.abs(torque * velocities), axis=1),
            contacts=touch > CONTACT_FORCE,
            impact=xp.max(touch, axis=1) / self.weight,
            on_stop=on_stop,
            foot_height=sensors[:, self.sole_pos_idx[:, 2]],
            foot_speed=sensors[:, self.sole_vel_idx[:, :2]],
            fell=(s["height"] < FALL_RATIO * self.stand_height) | ~upright,
        )
        return s

    def _rewards(self, s: dict):
        xp = self.xp
        columns = []
        speed, gravity = s["speed"], s["gravity"]
        positions, velocities, contacts = s["positions"], s["velocities"], s["contacts"]
        share = self._run_share()
        settled = (~self.jump_on).astype(self.dtype)  # Standing between jumps (always, without jumps).
        between = lambda window: (self.jump_on & (self.jump_share >= window[0]) & (self.jump_share < window[1])).astype(self.dtype)  # noqa: E731
        for key in self.reward_terms:
            if key == "alive":
                value = xp.ones(self.count)
            elif key == "track_speed":
                error = xp.sum((self.command[:, :2] - speed[:, :2]) ** 2, axis=1)
                value = xp.exp(-error / 0.25)
            elif key == "track_turn":
                value = xp.exp(-((self.command[:, 2] - s["yaw_rate"]) ** 2) / 0.25)
            elif key == "feet_phase":
                legs = (self.phase[:, None] + self.foot_offsets[None, :]) % 1.0
                stance = self.gait.stance_at(share)[:, None] if self.gait is not None else 0.55
                # Asked to stay put, both feet stay down: no marching on the spot.
                still = xp.max(xp.abs(self.command), axis=1) < STILL
                value = xp.sum(contacts == ((legs < stance) | still[:, None]), axis=1).astype(float)
            elif key == "upright":
                value = xp.sum((gravity[:, :2] - self.gravity_ref[:, :2]) ** 2, axis=1)
            elif key == "height":
                value = xp.minimum(s["height"] - self.stand_height, 0.0) ** 2 * settled
            elif key == "vertical_speed":
                value = speed[:, 2] ** 2 * (1.0 - share) * settled  # Running and jumping bounce.
            elif key == "wobble":
                value = xp.sum(s["turn"][:, :2] ** 2, axis=1)
            elif key == "stay":
                value = xp.sum(speed[:, :2] ** 2, axis=1)
            elif key == "pose":
                value = xp.sum((positions - self.default) ** 2, axis=1) * settled
            elif key == "hip_pose":
                value = xp.sum((positions - self.default)[:, self.hip_ids] ** 2, axis=1)
            elif key == "waist_pose":
                value = xp.sum((positions - self.default)[:, self.waist_ids] ** 2, axis=1)
            elif key == "swing_height":
                lift = self.gait.swing_at(share)[:, None] if self.gait is not None else 0.04
                value = xp.sum((s["foot_height"] - lift) ** 2 * ~contacts, axis=1)
            elif key == "feet_slip":
                value = xp.sum(xp.sum(s["foot_speed"] ** 2, axis=2) * contacts, axis=1)
            elif key == "smooth":
                value = xp.sum((self.actions - self.last_actions) ** 2, axis=1)
            elif key == "joint_accel":
                value = xp.sum(((velocities - self.last_speeds) / DT) ** 2, axis=1)
            elif key == "joint_speed":
                value = xp.sum(velocities**2, axis=1)
            elif key == "torque":
                value = xp.sum((s["torque"] / self.peak_driven) ** 2, axis=1)
            elif key == "power":
                value = s["power"] / self.weight
            elif key == "joint_limits":
                value = xp.sum(xp.maximum(self.soft_low - positions, 0) + xp.maximum(positions - self.soft_high, 0), axis=1)
            elif key == "impact":
                walk, run = self.skill.impact
                value = xp.maximum(s["impact"] - (walk + (run - walk) * share), 0.0) ** 2
            elif key == "strut_limit":
                value = xp.sum(s["on_stop"], axis=1).astype(float) if s["on_stop"].shape[1] else xp.zeros(self.count)
            elif key == "jump_up":
                value = xp.maximum(speed[:, 2], 0.0) * between(TAKE_OFF)
            elif key == "airborne":
                value = xp.all(~contacts, axis=1).astype(self.dtype) * between(FLIGHT)
            elif key == "jump_height":
                value = xp.maximum(s["height"] - self.stand_height, 0.0) * between(FLIGHT)
            elif key == "grounded":
                value = xp.sum(~contacts, axis=1).astype(self.dtype) * settled
            else:
                raise ValueError(f"No reward named {key!r}")
            columns.append(value)
        return xp.stack(columns, axis=1) if columns else xp.zeros((self.count, 0))

    # What the policy and the critic sense.

    def _noise_scales(self, level: float, joints: int, design: int) -> NDArray:
        scales = {"gyro": 0.2 * GYRO_SCALE, "gravity": 0.05, "joints": 0.01, "speeds": 1.5 * SPEED_SCALE}
        parts = [np.full(size_of((term,), joints, design), scales.get(term, 0.0) * level) for term in self.terms]
        return np.concatenate(parts)

    def _observe(self):
        physics = self.physics
        qpos, qvel = physics.qpos, physics.qvel
        clean = observe(
            self.terms,
            self.to_robot_np,
            qpos[:, 3:7],
            qvel[:, 3:6],
            qpos[:, self.qpos_ids],
            qvel[:, self.dof_ids],
            self.default,
            self.actions,
            command=self.command,
            phase=self.phase,
            design=self.design_units if self.senses_design else None,
            jump=self.xp.stack([self.jump_on.astype(self.dtype), self.jump_share], axis=1),
        )
        noise = (2.0 * self.rng.random(clean.shape) - 1.0) * self.noise
        return clean + noise.astype(np.float32), self._critic(clean, qpos, qvel)

    def _critic(self, clean, qpos, qvel):
        xp = self.xp
        s = self._kinematics(qpos, qvel)
        sensors = self.physics.sensors
        struts = (sensors[:, self.strut_idx] - self.strut_mid) / self.strut_half if self.strut_adr else xp.zeros((self.count, 0))
        passive = qpos[:, self.passive_q] if len(self.passive_q) else xp.zeros((self.count, 0))
        extra = xp.concatenate(
            [
                s["speed"] * 2.0,
                (s["height"] - self.stand_height)[:, None],
                self.contacts.astype(self.dtype),
                self.foot_height * 10.0,
                self.friction[:, None],
                (self.added_mass / 5.0)[:, None],
                self.strength[:, None],
                passive,
                struts,
            ],
            axis=1,
        )
        return xp.concatenate([clean, extra.astype(np.float32)], axis=1)

    # The result.

    def policy(self, ppo: PPO, name: str, about: str = "", stats: dict | None = None) -> Policy:
        """The policy as trained so far, for the design the search has chosen."""
        chosen = self.pool.chosen() if self.pool.searching else self.profile.complete_design(self.setup.design)
        vector = self.space.vector(chosen)
        from ..robot.design import design_metrics

        pose, metrics = design_metrics(self.profile, self.model, self.info, self.space, vector, measure(self.profile))
        act_ids = to_numpy(self.act_ids).tolist()
        kp0, kd0 = to_numpy(self.kp0), to_numpy(self.kd0)
        hold = {}
        for a in range(self.nu):
            if a in set(act_ids):
                continue
            joint = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, self.model.actuator_trnid[a, 0])
            hold[joint] = float(pose.ctrl[a])
        default = tuple(float(pose.ctrl[a]) for a in act_ids)
        params = self.profile.design
        return Policy(
            name=name,
            robot=self.profile.id,
            skill=self.setup.skill,
            joints=self.joints,
            default=default,
            hold=hold,
            gains=tuple((float(kp0[a]), float(kd0[a])) for a in act_ids),
            terms=self.terms,
            network=ppo.actor.numpy(),
            obs_mean=to_numpy(ppo.obs_normalizer.mean).astype(np.float32),
            obs_std=to_numpy(ppo.obs_normalizer.std).astype(np.float32),
            to_robot=tuple(tuple(float(v) for v in row) for row in self.to_robot_np),
            action_scale=ACTION_SCALE,
            action_clip=ACTION_CLIP,
            rate=RATE,
            gait=self.gait,
            jump_seconds=self.jumping.seconds if self.jumping is not None else None,
            commands=self.setup.command_ranges if "command" in self.skill.terms else None,
            design=chosen,
            design_keys=tuple(self.space.keys) if self.senses_design else (),
            design_ranges=tuple((params[k].low, params[k].high) for k in self.space.keys) if self.senses_design else (),
            about=about,
            stats={**(stats or {}), "design_metrics": metrics},
        )


def _quat_multiply(a, b):
    aw, ax, ay, az = a.T
    bw, bx, by, bz = b.T
    return module_of(a).stack(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        axis=1,
    )


__all__ = ["DT", "RATE", "Episodes", "PracticeRobots", "envelope", "lean_model", "quat_rotate_inverse"]
