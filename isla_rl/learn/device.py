"""Where training's numbers live: this computer's processor, or an NVIDIA GPU.

On the processor NumPy holds them. On an NVIDIA GPU, CuPy does, NVIDIA Warp and
MuJoCo Warp step the physics, and training runs many times faster. The GPU
needs these installed (Windows and Linux with an NVIDIA GPU; macOS trains on
the processor), which the Train page does on request::

    python -m pip install -r requirements-gpu.txt

Without them, or without an NVIDIA GPU, training runs on the processor. Either
way a trained policy is plain NumPy: the robot runs it with nothing more.
"""

from __future__ import annotations

import importlib.util
import warnings
from types import ModuleType

import numpy as np
from numpy.typing import NDArray

CPU = "cpu"
GPU = "gpu"
AUTO = "auto"
DEVICES = (AUTO, GPU, CPU)
PACKAGES = ("cupy", "warp", "mujoco_warp")  # What training on a GPU imports.


def gpu_installed() -> bool:
    """Whether the packages for GPU training are installed, without importing them."""
    return all(importlib.util.find_spec(name) is not None for name in PACKAGES)


def cupy() -> ModuleType:
    """CuPy, imported quietly: NVIDIA's CUDA libraries come as Python packages here,
    which it finds without the CUDA_PATH it warns about."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="CUDA path could not be detected")
        import cupy as module
    return module


def gpu_problem() -> str | None:
    """Why training cannot use an NVIDIA GPU here, or None when it can.

    This imports CuPy and asks the driver, so it takes a moment: ask once.
    """
    if not gpu_installed():
        return "NVIDIA GPU support is not installed: the Train page installs it"
    try:
        if cupy().cuda.runtime.getDeviceCount() < 1:
            return "No NVIDIA GPU found"
    except Exception as error:  # The driver, CUDA, or CuPy itself.  # noqa: BLE001
        return f"The NVIDIA GPU cannot be used: {error}"
    return None


def choose(device: str) -> str:
    """The device to train on: ``auto`` picks the GPU when it can be used."""
    if device not in DEVICES:
        raise ValueError(f"Train on one of {', '.join(DEVICES)}, not {device!r}")
    if device == AUTO:
        return GPU if gpu_problem() is None else CPU
    if device == GPU:
        problem = gpu_problem()
        if problem is not None:
            raise ValueError(problem)
    return device


def gpu_name() -> str:
    name = cupy().cuda.runtime.getDeviceProperties(0)["name"]
    return name.decode() if isinstance(name, bytes) else str(name)


def array_module(device: str) -> ModuleType:
    """NumPy, or CuPy for the GPU: the same functions, where the numbers live."""
    if device == GPU:
        return cupy()
    return np


def module_of(array) -> ModuleType:
    """The array module an array belongs to."""
    if type(array).__module__.split(".")[0] == "cupy":
        return cupy()
    return np


def to_numpy(array) -> NDArray:
    """A copy on this computer, from either device; NumPy arrays pass as they are."""
    if isinstance(array, np.ndarray):
        return array
    if hasattr(array, "get"):
        return array.get()
    return np.asarray(array)


class Random:
    """Random numbers from NumPy, or from CuPy's cuRAND generator on the GPU.

    CuPy's newer Generator interface costs a few hundred microseconds a call on
    Windows; the legacy cuRAND RandomState is several times cheaper, and drawing
    everything a reset needs in one call cheaper still.
    """

    def __init__(self, generator, xp: ModuleType = np) -> None:
        self.generator = generator
        self.xp = xp

    @classmethod
    def seeded(cls, seed: int | None, xp: ModuleType = np) -> Random:
        if xp is np:
            return cls(np.random.default_rng(seed), np)
        return cls(xp.random.RandomState(seed), xp)

    def random(self, size):
        if self.xp is np:
            return self.generator.random(size)
        return self.generator.random_sample(size)

    def uniform(self, low, high, size):
        if self.xp is np:
            return self.generator.uniform(low, high, size)
        return low + (high - low) * self.generator.random_sample(size)

    def integers(self, low: int, high: int, size):
        if self.xp is np:
            return self.generator.integers(low, high, size)
        draw = self.generator.random_sample(size)
        return (low + self.xp.floor(draw * (high - low))).astype(int)

    def normal(self, shape, dtype=np.float32):
        if self.xp is np:
            return self.generator.standard_normal(shape).astype(dtype)
        return self.generator.standard_normal(shape, dtype=dtype)

    def permutation(self, count: int):
        if self.xp is np:
            return self.generator.permutation(count)
        return self.xp.argsort(self.generator.random_sample(count))


_QUAT2MAT: dict = {}


def quat_to_mat(quaternion):
    """Rotation matrices (n, 3, 3) from unit quaternions (n, 4), on either device.

    Columns are the body's axes in the world, as MuJoCo's ``xmat``. On the GPU
    this is one kernel instead of some forty array operations.
    """
    xp = module_of(quaternion)
    if xp is np:
        w, x, y, z = quaternion.T
        return np.stack(
            [
                1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
                2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
                2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
            ],
            axis=1,
        ).reshape(-1, 3, 3)
    kernel = _QUAT2MAT.get("kernel")
    if kernel is None:
        kernel = _QUAT2MAT["kernel"] = xp.ElementwiseKernel(
            "raw T q",
            "raw T R",
            """
            const T w = q[4*i], x = q[4*i+1], y = q[4*i+2], z = q[4*i+3];
            R[9*i+0] = 1 - 2*(y*y + z*z); R[9*i+1] = 2*(x*y - w*z); R[9*i+2] = 2*(x*z + w*y);
            R[9*i+3] = 2*(x*y + w*z); R[9*i+4] = 1 - 2*(x*x + z*z); R[9*i+5] = 2*(y*z - w*x);
            R[9*i+6] = 2*(x*z - w*y); R[9*i+7] = 2*(y*z + w*x); R[9*i+8] = 1 - 2*(x*x + y*y);
            """,
            "isla_quat2mat",
        )
    q = xp.ascontiguousarray(quaternion)
    out = xp.empty((q.shape[0], 3, 3), dtype=q.dtype)
    kernel(q, out, size=q.shape[0])
    return out


_KINEMATICS: dict = {}


def base_kinematics(qpos, qvel, pelvis, forward, to_robot):
    """What training reads off each robot's base, in one pass.

    Returns ``(speed, yaw_rate, height, gravity, turn)``: the pelvis's velocity in
    the heading frame (forward, left, up; m/s), the turning rate about the vertical
    (rad/s), the pelvis's height, which way is down in the robot's axes, and the
    base's rotation rate in the robot's axes. ``pelvis`` is the hip midpoint in the
    base's frame, ``forward`` the base's forward axis, ``to_robot`` the matrix whose
    rows are the robot's forward, left and up in the base's axes.
    """
    xp = module_of(qpos)
    if xp is np:
        w, x, y, z = qpos[:, 3], qpos[:, 4], qpos[:, 5], qpos[:, 6]
        rotation = quat_to_mat(qpos[:, 3:7])
        gyro = qvel[:, 3:6]
        pelvis_world = rotation @ pelvis
        spin = (rotation @ gyro[:, :, None])[:, :, 0]
        velocity = qvel[:, 0:3] + np.cross(spin, pelvis_world)
        fwd = rotation @ forward
        fwd[:, 2] = 0.0
        fwd /= np.maximum(np.linalg.norm(fwd, axis=1, keepdims=True), 1e-6)
        speed = np.stack(
            [
                fwd[:, 0] * velocity[:, 0] + fwd[:, 1] * velocity[:, 1],
                fwd[:, 0] * velocity[:, 1] - fwd[:, 1] * velocity[:, 0],
                velocity[:, 2],
            ],
            axis=1,
        )
        del w, x, y, z
        gravity = -rotation[:, 2, :] @ np.asarray(to_robot).T
        turn = gyro @ np.asarray(to_robot).T
        return speed, spin[:, 2], qpos[:, 2] + pelvis_world[:, 2], gravity, turn
    kernel = _KINEMATICS.get("kernel")
    if kernel is None:
        kernel = _KINEMATICS["kernel"] = xp.ElementwiseKernel(
            "raw T qpos, raw T qvel, int32 nq, int32 nv, raw T pel, raw T fw, raw T tr",
            "raw T speed, raw T yaw, raw T height, raw T grav, raw T turn",
            """
            const int a = i * nq, b = i * nv;
            const T w = qpos[a+3], x = qpos[a+4], y = qpos[a+5], z = qpos[a+6];
            const T R00 = 1 - 2*(y*y + z*z), R01 = 2*(x*y - w*z), R02 = 2*(x*z + w*y);
            const T R10 = 2*(x*y + w*z), R11 = 1 - 2*(x*x + z*z), R12 = 2*(y*z - w*x);
            const T R20 = 2*(x*z - w*y), R21 = 2*(y*z + w*x), R22 = 1 - 2*(x*x + y*y);
            const T gx = qvel[b+3], gy = qvel[b+4], gz = qvel[b+5];
            const T px = R00*pel[0] + R01*pel[1] + R02*pel[2];
            const T py = R10*pel[0] + R11*pel[1] + R12*pel[2];
            const T pz = R20*pel[0] + R21*pel[1] + R22*pel[2];
            const T sx = R00*gx + R01*gy + R02*gz;
            const T sy = R10*gx + R11*gy + R12*gz;
            const T sz = R20*gx + R21*gy + R22*gz;
            const T vx = qvel[b] + sy*pz - sz*py;
            const T vy = qvel[b+1] + sz*px - sx*pz;
            const T vz = qvel[b+2] + sx*py - sy*px;
            T fx = R00*fw[0] + R01*fw[1] + R02*fw[2];
            T fy = R10*fw[0] + R11*fw[1] + R12*fw[2];
            T norm = sqrt(fx*fx + fy*fy);
            if (norm < (T)1e-6) norm = (T)1e-6;
            fx /= norm; fy /= norm;
            speed[3*i] = fx*vx + fy*vy;
            speed[3*i+1] = fx*vy - fy*vx;
            speed[3*i+2] = vz;
            yaw[i] = sz;
            height[i] = qpos[a+2] + pz;
            const T dx = -R20, dy = -R21, dz = -R22;
            for (int r = 0; r < 3; r++) {
                grav[3*i+r] = tr[3*r]*dx + tr[3*r+1]*dy + tr[3*r+2]*dz;
                turn[3*i+r] = tr[3*r]*gx + tr[3*r+1]*gy + tr[3*r+2]*gz;
            }
            """,
            "isla_base_kinematics",
        )
    n = qpos.shape[0]
    dtype = qpos.dtype
    speed = xp.empty((n, 3), dtype)
    yaw = xp.empty(n, dtype)
    height = xp.empty(n, dtype)
    gravity = xp.empty((n, 3), dtype)
    turn = xp.empty((n, 3), dtype)
    kernel(
        qpos, qvel, np.int32(qpos.shape[1]), np.int32(qvel.shape[1]),
        xp.asarray(pelvis, dtype), xp.asarray(forward, dtype), xp.ascontiguousarray(xp.asarray(to_robot, dtype)),
        speed, yaw, height, gravity, turn, size=n,
    )
    return speed, yaw, height, gravity, turn
