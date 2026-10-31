"""Physics on an NVIDIA GPU: every practice robot at once, in MuJoCo Warp.

A move is five physics steps. Before each, a Warp kernel picks each robot's
targets (its control delay keeps the last move's for a step or two) and writes
each motor's torque-speed envelope into that robot's force range. After each,
another kernel keeps each foot's firmest touch and notes any shock absorber at
the end of its stroke. The whole move is recorded once as a CUDA graph and then
replayed. CuPy's arithmetic shares the memory and the stream, so nothing is copied.

Each robot carries its own model fields for what varies: foot friction, torso
mass, servo gains, and its hardware design (the bodies the rods slide and the
shock absorbers' springs, damping and free length).

The first run on a computer spends a few minutes while NVIDIA Warp compiles
MuJoCo Warp for its GPU; it keeps what it compiled.
"""

from __future__ import annotations

import mujoco
import mujoco_warp as mjw
import numpy as np
import warp as wp
from mujoco_warp._src.types import vec10

from .device import cupy

cp = cupy()

SUBSTEPS = 5
CONTACTS = 20  # Most contacts one robot's feet make.
CONSTRAINTS = 160  # Most constraint rows one robot needs.
VARIED = (
    "geom_friction",
    "body_mass",
    "body_inertia",
    "body_pos",
    "actuator_gainprm",
    "actuator_biasprm",
    "actuator_forcerange",
    "tendon_stiffness",
    "tendon_damping",
    "tendon_lengthspring",
)

wp.config.log_level = wp.LOG_WARNING
wp.config.quiet = True


@wp.kernel
def _before(
    substep: int,
    delay: wp.array(dtype=int),
    new: wp.array2d(dtype=float),
    old: wp.array2d(dtype=float),
    qvel: wp.array2d(dtype=float),
    dofs: wp.array(dtype=int),
    peak: wp.array(dtype=float),
    corner: wp.array(dtype=float),
    no_load: wp.array(dtype=float),
    strength: wp.array(dtype=float),
    ctrl: wp.array2d(dtype=float),
    forcerange: wp.array2d(dtype=wp.vec2),
):
    world, i = wp.tid()
    if delay[world] > substep:
        ctrl[world, i] = old[world, i]
    else:
        ctrl[world, i] = new[world, i]
    speed = qvel[world, dofs[i]]
    top = peak[i] * wp.min(strength[world], 1.0)
    fast = wp.abs(speed)
    motoring = top
    if fast > corner[i]:
        span = wp.max(no_load[i] - corner[i], 1.0e-6)
        motoring = top * wp.clamp((no_load[i] - fast) / span, -1.0, 1.0)  # Past no-load: braking.
    low = -top
    high = top
    if speed > 0.0:
        high = motoring
    if speed < 0.0:
        low = -motoring
    forcerange[world, i] = wp.vec2(low, high)


@wp.kernel
def _after(
    first: int,
    sensordata: wp.array2d(dtype=float),
    touch_adr: wp.array(dtype=int),
    strut_adr: wp.array(dtype=int),
    strut_low: wp.array(dtype=float),
    strut_high: wp.array(dtype=float),
    touch: wp.array2d(dtype=float),
    on_stop: wp.array2d(dtype=int),
):
    world = wp.tid()
    for k in range(touch_adr.shape[0]):
        value = sensordata[world, touch_adr[k]]
        if first == 1 or value > touch[world, k]:
            touch[world, k] = value
    for k in range(strut_adr.shape[0]):
        length = sensordata[world, strut_adr[k]]
        hit = 0
        if length < strut_low[k] or length > strut_high[k]:
            hit = 1
        if first == 1:
            on_stop[world, k] = hit
        elif hit == 1:
            on_stop[world, k] = 1


@wp.kernel
def _vary(
    ids: wp.array(dtype=int),
    friction: wp.array(dtype=float),
    mass: wp.array(dtype=float),
    inertia: wp.array(dtype=wp.vec3),
    strength: wp.array(dtype=float),
    body_pos: wp.array2d(dtype=wp.vec3),
    stiffness: wp.array2d(dtype=float),
    damping: wp.array2d(dtype=float),
    free: wp.array2d(dtype=float),
    soles: wp.array(dtype=int),
    mass_body: int,
    moved: wp.array(dtype=int),
    tendons: wp.array(dtype=int),
    kp0: wp.array(dtype=float),
    kd0: wp.array(dtype=float),
    nu: int,
    geom_friction: wp.array2d(dtype=wp.vec3),
    body_mass: wp.array2d(dtype=float),
    body_inertia: wp.array2d(dtype=wp.vec3),
    model_body_pos: wp.array2d(dtype=wp.vec3),
    gainprm: wp.array2d(dtype=vec10),
    biasprm: wp.array2d(dtype=vec10),
    tendon_stiffness: wp.array2d(dtype=float),
    tendon_damping: wp.array2d(dtype=float),
    tendon_lengthspring: wp.array2d(dtype=wp.vec2),
    strength_out: wp.array(dtype=float),
):
    """Write one starting robot's differences and design into its model fields."""
    row = wp.tid()
    world = ids[row]
    for g in range(soles.shape[0]):
        f = geom_friction[world, soles[g]]
        f[0] = friction[row]
        geom_friction[world, soles[g]] = f
    body_mass[world, mass_body] = mass[row]
    body_inertia[world, mass_body] = inertia[row]
    s = strength[row]
    strength_out[world] = s
    for a in range(nu):
        gain = gainprm[world, a]
        gain[0] = kp0[a] * s
        gainprm[world, a] = gain
        bias = biasprm[world, a]
        bias[1] = -kp0[a] * s
        bias[2] = -kd0[a] * s
        biasprm[world, a] = bias
    for j in range(moved.shape[0]):
        model_body_pos[world, moved[j]] = body_pos[row, j]
    for t in range(tendons.shape[0]):
        tendon_stiffness[world, tendons[t]] = stiffness[row, t]
        tendon_damping[world, tendons[t]] = damping[row, t]
        tendon_lengthspring[world, tendons[t]] = wp.vec2(free[row, t], free[row, t])


class GpuPhysics:
    """What the processor's rollout does, for every robot at once on the GPU."""

    def __init__(
        self,
        model: mujoco.MjModel,
        data: mujoco.MjData,
        count: int,
        actuator_dofs: np.ndarray,
        peak: np.ndarray,
        corner: np.ndarray,
        no_load: np.ndarray,
        touch_adr: list[int],
        strut_adr: list[int],
        strut_low: np.ndarray,
        strut_high: np.ndarray,
        sole_geoms: np.ndarray,
        mass_body: int,
        moved: np.ndarray,
        tendons: np.ndarray,
    ) -> None:
        wp.init()
        self.stream = cp.cuda.Stream.from_external(wp.get_stream())
        self.stream.use()
        self.count, self.nu = count, model.nu
        mujoco.mj_forward(model, data)
        self.model = mjw.put_model(model, batch_sizes={name: count for name in VARIED})
        self.model.opt.warn_overflow = 0  # A fallen robot's solver may hit its limits: fine.
        self.data = mjw.put_data(model, data, nworld=count, nconmax=CONTACTS, njmax=CONSTRAINTS)
        d = self.data
        self.qpos, self.qvel = cp.asarray(d.qpos), cp.asarray(d.qvel)
        self.sensors = cp.asarray(d.sensordata)
        self._warmstart, self._time = cp.asarray(d.qacc_warmstart), cp.asarray(d.time)
        as_int = lambda values: wp.array(np.asarray(values, dtype=np.int32), dtype=int)  # noqa: E731
        as_float = lambda values: wp.array(np.asarray(values, dtype=np.float32), dtype=float)  # noqa: E731
        self._dofs = as_int(actuator_dofs)
        self._peak, self._corner, self._no_load = as_float(peak), as_float(corner), as_float(no_load)
        self._touch_adr = as_int(touch_adr)
        self._strut_adr = as_int(strut_adr if len(strut_adr) else [0])
        self._strut_low = as_float(strut_low if len(strut_low) else [-1e9])
        self._strut_high = as_float(strut_high if len(strut_high) else [1e9])
        self._struts = len(strut_adr)
        self._new = wp.zeros((count, model.nu), dtype=float)
        self._old = wp.zeros((count, model.nu), dtype=float)
        self._delay = wp.zeros(count, dtype=int)
        self._strength = wp.ones(count, dtype=float)
        self._touch = wp.zeros((count, max(len(touch_adr), 1)), dtype=float)
        self._on_stop = wp.zeros((count, max(len(strut_adr), 1)), dtype=int)
        self.new, self.old = cp.asarray(self._new), cp.asarray(self._old)
        self.delay_cp, self.strength_cp = cp.asarray(self._delay), cp.asarray(self._strength)
        self.touch = cp.asarray(self._touch)[:, : len(touch_adr)]
        self._on_stop_cp = cp.asarray(self._on_stop)
        self.new[:] = cp.asarray(d.ctrl)
        self.old[:] = cp.asarray(d.ctrl)
        # Staging for _vary: CuPy writes the rows, one kernel scatters them.
        self._soles, self._mass_body = as_int(sole_geoms), int(mass_body)
        self._moved = as_int(moved if len(moved) else [0])
        self._tendons = as_int(tendons if len(tendons) else [0])
        self._n_moved, self._n_tendons = len(moved), len(tendons)
        self._kp0 = as_float(model.actuator_gainprm[:, 0])
        self._kd0 = as_float(-model.actuator_biasprm[:, 2])
        rows = max(self._n_moved, 1), max(self._n_tendons, 1)
        self._stage = {
            "ids": wp.zeros(count, dtype=int),
            "friction": wp.zeros(count, dtype=float),
            "mass": wp.zeros(count, dtype=float),
            "inertia": wp.zeros(count, dtype=wp.vec3),
            "strength": wp.zeros(count, dtype=float),
            "body_pos": wp.zeros((count, rows[0]), dtype=wp.vec3),
            "stiffness": wp.zeros((count, rows[1]), dtype=float),
            "damping": wp.zeros((count, rows[1]), dtype=float),
            "free": wp.zeros((count, rows[1]), dtype=float),
        }
        self._stage_cp = {key: cp.asarray(value) for key, value in self._stage.items()}
        self._move()  # Compiles MuJoCo Warp's kernels: minutes, the first time.
        with wp.ScopedCapture() as capture:
            self._move()
        self._graph = capture.graph

    @property
    def on_stop(self):
        return self._on_stop_cp[:, : self._struts].astype(bool)

    def _move(self) -> None:
        d, m = self.data, self.model
        for substep in range(SUBSTEPS):
            wp.launch(
                _before,
                dim=(self.count, self.nu),
                inputs=[
                    substep, self._delay, self._new, self._old, d.qvel, self._dofs,
                    self._peak, self._corner, self._no_load, self._strength,
                ],
                outputs=[d.ctrl, m.actuator_forcerange],
            )
            mjw.step(m, d)
            wp.launch(
                _after,
                dim=self.count,
                inputs=[
                    int(substep == 0), d.sensordata, self._touch_adr, self._strut_adr,
                    self._strut_low, self._strut_high,
                ],
                outputs=[self._touch, self._on_stop],
            )

    def vary(self, ids, friction, mass, inertia, strength, body_pos, stiffness, damping, free) -> None:
        """Write each starting robot's differences and design into its model fields."""
        k = len(ids)
        st = self._stage_cp
        st["ids"][:k] = ids
        st["friction"][:k] = friction
        st["mass"][:k] = mass
        st["inertia"][:k] = inertia
        st["strength"][:k] = strength
        if self._n_moved:
            st["body_pos"][:k, : self._n_moved] = body_pos
        if stiffness is not None and self._n_tendons:
            st["stiffness"][:k, : self._n_tendons] = stiffness
            st["damping"][:k, : self._n_tendons] = damping
            st["free"][:k, : self._n_tendons] = free
        g, m = self._stage, self.model
        wp.launch(
            _vary,
            dim=k,
            inputs=[
                g["ids"], g["friction"], g["mass"], g["inertia"], g["strength"], g["body_pos"],
                g["stiffness"], g["damping"], g["free"], self._soles, self._mass_body,
                self._moved if self._n_moved else wp.zeros(0, dtype=int),
                self._tendons if self._n_tendons else wp.zeros(0, dtype=int),
                self._kp0, self._kd0, self.nu,
            ],
            outputs=[
                m.geom_friction, m.body_mass, m.body_inertia, m.body_pos, m.actuator_gainprm,
                m.actuator_biasprm, m.tendon_stiffness, m.tendon_damping, m.tendon_lengthspring,
                self._strength,
            ],
        )

    def place(self, ids, qpos) -> None:
        self.qpos[ids] = qpos
        self.qvel[ids] = 0.0
        self._warmstart[ids] = 0.0
        self._time[ids] = 0.0

    def step(self, ctrl, last_ctrl, delay) -> None:
        self.new[:] = ctrl
        self.old[:] = last_ctrl
        self.delay_cp[:] = delay
        wp.capture_launch(self._graph)

    def close(self) -> None:
        self._graph = None
        self.stream.synchronize()
