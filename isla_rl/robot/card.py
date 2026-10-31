"""A robot's card in the hangar: the numbers that describe it, and a blueprint of it.

The blueprint is the robot seen from the front in its standing pose: one outline
per part (the convex hull of its points, flattened onto the view), listed from
the back to the front so filled outlines hide what is behind them, and a point
on every motor. It is small enough to send for every robot in the hangar at once.

Cards are cached per model and profile: building one takes a second or two.
"""

from __future__ import annotations

import hashlib
import json

import mujoco
import numpy as np

from .. import paths
from . import geometry as geo
from .analysis import STAND_DRIFT, stand_drift
from .build import build, measure
from .profile import RobotProfile

CARD_VERSION = 1
MAX_OUTLINE = 40  # Points per part outline.
MIN_AREA = 2e-5  # m²: parts smaller than this in the view (bolts, washers) are left out.


def card(profile: RobotProfile) -> dict:
    """The robot's numbers and blueprint, from the cache when nothing changed."""
    profile_hash = hashlib.sha256(json.dumps(profile.data, sort_keys=True).encode()).hexdigest()[:12]
    key = f"{measure(profile)['source_hash']}-{profile_hash}-{CARD_VERSION}"
    path = paths.CACHE / profile.id / f"card-{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    result = _card(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    return result


def _card(profile: RobotProfile) -> dict:
    built = build(profile, purpose="display")
    model, info = built.model, built.info
    data = mujoco.MjData(model)
    data.qpos[:] = info.stand_qpos
    mujoco.mj_kinematics(model, data)
    base = model.body(info.base).id
    types = profile.actuator_types
    counts: dict[str, int] = {}
    for motor in info.motors:
        counts[motor.type] = counts.get(motor.type, 0) + 1
    actuators = [
        {
            "type": key,
            "label": types[key].get("label", key),
            "count": count,
            "peak_torque": float(types[key].get("peak_torque", 0.0)),
            "gear_ratio": types[key].get("gear_ratio"),
        }
        for key, count in sorted(counts.items(), key=lambda item: -float(types[item[0]].get("peak_torque", 0.0)))
    ]
    drift = stand_drift(model, np.array(info.stand_qpos), np.array(info.stand_ctrl), base)
    return {
        "id": profile.id,
        "name": profile.name,
        "about": profile.about,
        "imported": "imported_from" in profile.data,
        "mass": info.total_mass,
        "cad_mass": info.cad_mass,
        "virtual_mass": info.virtual_mass,
        "height": info.height,
        "hip_height": info.hip_height,
        "motors": len(info.motors),
        "dof": int(model.nv - 6),
        "passive": len(info.passive),
        "feet": len(info.feet),
        "struts": len(info.struts),
        "rods": len(info.rods),
        "actuators": actuators,
        "peak_torque": max((a["peak_torque"] for a in actuators), default=0.0),
        "design": [p.label for p in profile.design.values()],
        "stands_alone": drift < STAND_DRIFT,
        "stand_drift": drift,
        "blueprint": _blueprint(model, data, info, base),
    }


def _hull(points: np.ndarray) -> np.ndarray:
    """Convex hull of 2D points, counter-clockwise (Andrew's monotone chain)."""
    pts = np.unique(np.round(points, 4), axis=0)
    if len(pts) < 3:
        return pts

    def half(sequence: np.ndarray) -> list[np.ndarray]:
        chain: list[np.ndarray] = []
        for p in sequence:
            while len(chain) >= 2:
                a, b = chain[-2], chain[-1]
                if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) > 0:
                    break
                chain.pop()
            chain.append(p)
        return chain

    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def _area(polygon: np.ndarray) -> float:
    x, y = polygon[:, 0], polygon[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _blueprint(model: mujoco.MjModel, data: mujoco.MjData, info, base: int) -> dict:
    rotation = data.xmat[base].reshape(3, 3)
    forward = rotation @ np.array(info.forward, float)
    forward[2] = 0.0
    forward /= max(float(np.linalg.norm(forward)), 1e-9)
    left = np.cross([0.0, 0.0, 1.0], forward)
    origin = np.array([data.xpos[base][0], data.xpos[base][1], 0.0])

    def view(points: np.ndarray) -> np.ndarray:
        # From the front the robot's left is on the viewer's right.
        local = points - origin
        return np.column_stack([-(local @ left), local[:, 2]])

    parts = []
    for body in range(1, model.nbody):
        for g in geo.visual_geoms(model, body):
            points = geo.geom_world_points(model, data, g)
            if len(points) < 3:
                continue
            outline = _hull(view(points))
            if len(outline) < 3 or _area(outline) < MIN_AREA:
                continue
            if len(outline) > MAX_OUTLINE:
                outline = outline[np.linspace(0, len(outline) - 1, MAX_OUTLINE).astype(int)]
            depth = float(np.mean((points - origin) @ forward))
            parts.append((depth, int(geo.link_of(model, body)), outline))
    parts.sort(key=lambda part: part[0])  # Furthest back first.
    motors = []
    for motor in info.motors:
        anchor = data.xanchor[model.joint(motor.joint).id]
        motors.append([round(float(v), 3) for v in view(anchor[None])[0]])
    every = np.concatenate([p[2] for p in parts]) if parts else np.zeros((1, 2))
    low, high = every.min(axis=0), every.max(axis=0)
    return {
        "parts": [{"link": link, "points": np.round(outline, 3).ravel().tolist()} for _, link, outline in parts],
        "motors": motors,
        "bounds": [round(float(low[0]), 3), round(float(low[1]), 3), round(float(high[0]), 3), round(float(high[1]), 3)],
    }
