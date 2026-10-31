"""Small geometry helpers on compiled MuJoCo models: frames, mesh points, boxes."""

from __future__ import annotations

import mujoco
import numpy as np
from numpy.typing import NDArray


def quat_conj(q: NDArray) -> NDArray:
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=float)


def quat_mul(a: NDArray, b: NDArray) -> NDArray:
    out = np.zeros(4)
    mujoco.mju_mulQuat(out, np.asarray(a, float), np.asarray(b, float))
    return out


def quat_to_mat(q: NDArray) -> NDArray:
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, np.asarray(q, float))
    return m.reshape(3, 3)


def to_local(model: mujoco.MjModel, data: mujoco.MjData, body: int, point: NDArray) -> NDArray:
    """A world point expressed in a body's frame."""
    rotation = data.xmat[body].reshape(3, 3)
    return rotation.T @ (np.asarray(point, float) - data.xpos[body])


def dir_to_local(data: mujoco.MjData, body: int, direction: NDArray) -> NDArray:
    return data.xmat[body].reshape(3, 3).T @ np.asarray(direction, float)


def local_pose_under(data: mujoco.MjData, parent: int, pos: NDArray, quat: NDArray):
    """Position and orientation, given in the world, relative to ``parent``."""
    rotation = data.xmat[parent].reshape(3, 3)
    local_pos = rotation.T @ (np.asarray(pos, float) - data.xpos[parent])
    local_quat = quat_mul(quat_conj(data.xquat[parent]), np.asarray(quat, float))
    return local_pos, local_quat


def geom_world_vertices(model: mujoco.MjModel, data: mujoco.MjData, geom: int) -> NDArray:
    """A mesh geom's vertices in the world, as posed in ``data``."""
    mesh = int(model.geom_dataid[geom])
    start, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
    vertices = model.mesh_vert[start : start + count].astype(float)
    rotation = data.geom_xmat[geom].reshape(3, 3)
    return data.geom_xpos[geom] + vertices @ rotation.T


def is_drawn(model: mujoco.MjModel, geom: int) -> bool:
    """A geom that draws the robot: not a plane, not an invisible collision copy."""
    if model.geom_type[geom] in (mujoco.mjtGeom.mjGEOM_PLANE, mujoco.mjtGeom.mjGEOM_HFIELD):
        return False
    if model.geom_group[geom] >= 3:
        return False
    material = int(model.geom_matid[geom])
    alpha = model.mat_rgba[material][3] if material >= 0 else model.geom_rgba[geom][3]
    return alpha > 0.01


def visual_geoms(model: mujoco.MjModel, body: int, group: int | None = None) -> list[int]:
    """Geoms that draw ``body``: those that do not collide, or every drawn geom when the
    model has no separate visual geoms (its collision shapes are what you see)."""
    own = [g for g in range(model.ngeom) if model.geom_bodyid[g] == body and (group is None or model.geom_group[g] == group)]
    visual = [g for g in own if is_drawn(model, g) and model.geom_contype[g] == 0 and model.geom_conaffinity[g] == 0]
    if visual:
        return visual
    return [g for g in own if is_drawn(model, g)]


def geom_world_points(model: mujoco.MjModel, data: mujoco.MjData, geom: int) -> NDArray:
    """Points outlining a geom in the world: mesh vertices, or a primitive's corners and rims."""
    kind = model.geom_type[geom]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        return geom_world_vertices(model, data, geom)
    size = model.geom_size[geom]
    if kind == mujoco.mjtGeom.mjGEOM_BOX:
        local = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * size
    elif kind == mujoco.mjtGeom.mjGEOM_SPHERE:
        r = size[0]
        local = np.array([[r, 0, 0], [-r, 0, 0], [0, r, 0], [0, -r, 0], [0, 0, r], [0, 0, -r]])
    elif kind == mujoco.mjtGeom.mjGEOM_ELLIPSOID:
        local = np.array([[size[0], 0, 0], [-size[0], 0, 0], [0, size[1], 0], [0, -size[1], 0], [0, 0, size[2]], [0, 0, -size[2]]])
    elif kind in (mujoco.mjtGeom.mjGEOM_CYLINDER, mujoco.mjtGeom.mjGEOM_CAPSULE):
        r, h = size[0], size[1]
        angles = np.linspace(0, 2 * np.pi, 12, endpoint=False)
        rim = np.stack([np.cos(angles) * r, np.sin(angles) * r], axis=1)
        local = np.concatenate([np.column_stack([rim, np.full(12, z)]) for z in (-h, h)])
        if kind == mujoco.mjtGeom.mjGEOM_CAPSULE:
            local = np.concatenate([local, [[0, 0, h + r], [0, 0, -h - r]]])
    else:
        return np.zeros((0, 3))
    rotation = data.geom_xmat[geom].reshape(3, 3)
    return data.geom_xpos[geom] + local @ rotation.T


def body_points(model: mujoco.MjModel, data: mujoco.MjData, body: int) -> NDArray:
    """Points outlining everything that draws one body, in the world."""
    parts = [geom_world_points(model, data, g) for g in visual_geoms(model, body)]
    parts = [p for p in parts if len(p)]
    return np.concatenate(parts) if parts else np.zeros((0, 3))


def aabb(points: NDArray) -> tuple[NDArray, NDArray]:
    return points.min(axis=0), points.max(axis=0)


def body_center(model: mujoco.MjModel, data: mujoco.MjData, body: int) -> NDArray:
    """The centre of a body's visual bounding box in the world (its COM when it has no mesh)."""
    points = body_points(model, data, body)
    if len(points) == 0:
        return data.xipos[body].copy()
    low, high = aabb(points)
    return (low + high) / 2


def principal_axis(points: NDArray) -> tuple[NDArray, NDArray]:
    """Mean and unit principal direction of a point cloud (a rod's axis)."""
    mean = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - mean, full_matrices=False)
    return mean, vt[0]


def link_of(model: mujoco.MjModel, body: int) -> int:
    """The nearest body at or above ``body`` that moves on a joint (or the world's child)."""
    while body > 0 and model.body_jntnum[body] == 0 and model.body_parentid[body] > 0:
        body = model.body_parentid[body]
    return body


def subtree(model: mujoco.MjModel, root: int) -> list[int]:
    found = [root]
    for body in range(root + 1, model.nbody):
        parent = model.body_parentid[body]
        if parent in found:
            found.append(body)
    return found


def box_inertia(mass: float, size: NDArray) -> NDArray:
    """Principal inertia of a solid box with full edge lengths ``size``."""
    a, b, c = (float(v) for v in size)
    return mass / 12.0 * np.array([b * b + c * c, a * a + c * c, a * a + b * b])


def cylinder_inertia(mass: float, radius: float, length: float) -> NDArray:
    """Principal inertia of a solid cylinder along its local z axis."""
    side = mass * (3 * radius * radius + length * length) / 12.0
    return np.array([side, side, 0.5 * mass * radius * radius])


def quat_from_z(direction: NDArray) -> NDArray:
    """A quaternion turning +z onto ``direction``."""
    z = np.array([0.0, 0.0, 1.0])
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    axis = np.cross(z, d)
    s = np.linalg.norm(axis)
    c = float(np.dot(z, d))
    if s < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0]) if c > 0 else np.array([0.0, 1.0, 0.0, 0.0])
    angle = np.arctan2(s, c)
    axis = axis / s
    return np.concatenate([[np.cos(angle / 2)], np.sin(angle / 2) * axis])
