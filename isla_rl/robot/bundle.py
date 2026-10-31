"""The robot as the web app draws it: kinematic tree, parts, motors, and light meshes.

The browser runs the same forward kinematics as MuJoCo from joint positions alone,
so the server only streams ``qpos``. It needs each body's frame relative to its
parent, each joint's axis and anchor, each geom's place on its body, and the
meshes. CAD meshes are far denser than a screen needs (a 10 mm bearing can have
25,000 triangles), so each is simplified by vertex clustering, and its feature
edges (creases sharper than ``EDGE_ANGLE``) are found once for the wireframe view.

``meta(...)`` is JSON; ``mesh_blob(...)`` is one binary file the browser slices:
float32 vertices, uint32 triangles, float32 edge segments.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import mujoco
import numpy as np

from .. import paths
from . import geometry as geo
from .build import VIRTUAL_BODY, Built, measure
from .profile import RobotProfile

EDGE_ANGLE = 28.0  # Degrees between faces for a crease to be drawn.
BUNDLE_VERSION = 4
FINE_FACES = 1500  # Meshes this small are kept as they are.


def _label(name: str) -> str:
    """A CAD name as people read it: 'right-hip-yaw-connector_1' -> 'Right hip yaw connector'."""
    text = re.sub(r"_\d+$", "", name)
    text = re.sub(r"[-_]+", " ", text).strip()
    return text[:1].upper() + text[1:] if text else name


def _joint_label(name: str) -> str:
    words = name.split("_")
    side = next((w for w in words if w in ("left", "right")), None)
    rest = " ".join(w for w in words if w not in ("left", "right"))
    return f"{side.capitalize()} {rest}" if side else rest.capitalize()


def simplify(vertices: np.ndarray, faces: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Vertex clustering: merge vertices sharing a grid cell, drop collapsed triangles."""
    if len(faces) <= FINE_FACES or cell <= 0:
        return vertices, faces
    keys = np.floor(vertices / cell).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    merged = np.zeros((len(counts), 3))
    np.add.at(merged, inverse, vertices)
    merged /= counts[:, None]
    f = inverse[faces]
    keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
    f = f[keep]
    if len(f):
        _, first = np.unique(np.sort(f, axis=1), axis=0, return_index=True)
        f = f[np.sort(first)]
    return merged, f


def feature_edges(vertices: np.ndarray, faces: np.ndarray, angle: float = EDGE_ANGLE) -> np.ndarray:
    """Line segments (n, 2, 3) along creases sharper than ``angle`` and along open borders."""
    chosen = feature_edge_pairs(vertices, faces, angle)
    return np.stack([vertices[chosen[:, 0]], vertices[chosen[:, 1]]], axis=1) if len(chosen) else np.zeros((0, 2, 3))


def feature_edge_pairs(vertices: np.ndarray, faces: np.ndarray, angle: float = EDGE_ANGLE) -> np.ndarray:
    """The same creases and borders as :func:`feature_edges`, as vertex index pairs (n, 2)."""
    if len(faces) == 0:
        return np.zeros((0, 2), dtype=int)
    a, b, c = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    normals = np.cross(b - a, c - a)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    owner = np.tile(np.arange(len(faces)), 3)
    edges = np.sort(edges, axis=1)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = np.all(edges[1:] == edges[:-1], axis=1)
    keep = []
    cos_limit = math.cos(math.radians(angle))
    i, n = 0, len(edges)
    starts = np.flatnonzero(np.concatenate([[True], ~same]))
    ends = np.concatenate([starts[1:], [n]])
    for s, e in zip(starts, ends, strict=True):
        if e - s == 1:
            keep.append(s)  # A border.
        else:
            if np.dot(normals[owner[s]], normals[owner[s + 1]]) < cos_limit:
                keep.append(s)
    del i
    return edges[np.array(keep, dtype=int)] if keep else np.zeros((0, 2), dtype=int)


def _cache_key(profile: RobotProfile, built: Built) -> str:
    cad = measure(profile)
    digest = hashlib.sha256(f"{cad['source_hash']}-{BUNDLE_VERSION}-{EDGE_ANGLE}".encode()).hexdigest()
    return digest[:16]


def mesh_blob(profile: RobotProfile, built: Built) -> tuple[bytes, list[dict]]:
    """Every visual mesh, simplified, as one binary blob, and where each sits in it."""
    key = _cache_key(profile, built)
    folder = paths.CACHE / profile.id
    blob_path, index_path = folder / f"meshes-{key}.bin", folder / f"meshes-{key}.json"
    if blob_path.exists() and index_path.exists():
        return blob_path.read_bytes(), json.loads(index_path.read_text(encoding="utf-8"))
    model = built.model
    vertices, faces, edges, index = [], [], [], []
    v_off = f_off = e_off = 0
    for mesh in range(model.nmesh):
        start, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
        fstart, fcount = model.mesh_faceadr[mesh], model.mesh_facenum[mesh]
        v = model.mesh_vert[start : start + count].astype(float)
        f = model.mesh_face[fstart : fstart + fcount].astype(np.int64)
        size = float(np.linalg.norm(v.max(axis=0) - v.min(axis=0))) if len(v) else 0.0
        cell = max(size / 55.0, 0.0007)
        sv, sf = simplify(v, f, cell)
        lines = feature_edges(sv, sf)
        vertices.append(sv.astype(np.float32))
        faces.append(sf.astype(np.uint32))
        edges.append(lines.astype(np.float32).reshape(-1, 3))
        index.append(
            {
                "mesh": mesh,
                "name": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, mesh),
                "v": [v_off, len(sv)],
                "f": [f_off, len(sf)],
                "e": [e_off, len(lines)],
                "original_faces": int(fcount),
            }
        )
        v_off += len(sv)
        f_off += len(sf)
        e_off += len(lines)
    v_all = np.concatenate(vertices) if vertices else np.zeros((0, 3), np.float32)
    f_all = np.concatenate(faces) if faces else np.zeros((0, 3), np.uint32)
    e_all = np.concatenate(edges) if edges else np.zeros((0, 3), np.float32)
    header = np.array([len(v_all), len(f_all), len(e_all) // 2], dtype=np.uint32)
    blob = header.tobytes() + v_all.tobytes() + f_all.tobytes() + e_all.tobytes()
    folder.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(blob)
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return blob, index


def meta(profile: RobotProfile, built: Built) -> dict:
    """Everything but the meshes: bodies, joints, geoms, motors, rods, shock absorbers."""
    model, info = built.model, built.info
    data = mujoco.MjData(model)
    data.qpos[:] = info.stand_qpos
    mujoco.mj_forward(model, data)
    name = lambda kind, i: mujoco.mj_id2name(model, kind, i) or ""  # noqa: E731
    motors = {m.joint: m for m in info.motors}
    types = profile.actuator_types

    def link_of(body: int) -> int:
        while body > 0 and model.body_jntnum[body] == 0 and model.body_parentid[body] > 0:
            body = model.body_parentid[body]
        return body

    rod_bodies = {piece["body"] for rod in info.rods for piece in rod.rods}
    bodies = []
    for b in range(model.nbody):
        body_name = name(mujoco.mjtObj.mjOBJ_BODY, b)
        kind = "world" if b == 0 else "part"
        if body_name in rod_bodies:
            kind = "rod"
        elif body_name == VIRTUAL_BODY:
            kind = "virtual"
        elif model.body_jntnum[b] > 0:
            kind = "link"
        bodies.append(
            {
                "id": b,
                "name": body_name,
                "label": "Virtual mass" if body_name == VIRTUAL_BODY else _label(body_name),
                "parent": int(model.body_parentid[b]),
                "pos": model.body_pos[b].round(7).tolist(),
                "quat": model.body_quat[b].round(9).tolist(),
                "mass": float(model.body_mass[b]),
                "link": int(link_of(b)) if b else 0,
                "kind": kind,
                "com": model.body_ipos[b].round(6).tolist(),
            }
        )
    joints = []
    for j in range(model.njnt):
        joint_name = name(mujoco.mjtObj.mjOBJ_JOINT, j)
        motor = motors.get(joint_name)
        kind = profile.actuator_types.get(motor.type, {}) if motor else {}
        joints.append(
            {
                "id": j,
                "name": joint_name,
                "label": _joint_label(joint_name),
                "type": int(model.jnt_type[j]),
                "body": int(model.jnt_bodyid[j]),
                "qpos": int(model.jnt_qposadr[j]),
                "dof": int(model.jnt_dofadr[j]),
                "axis": model.jnt_axis[j].round(9).tolist(),
                "pos": model.jnt_pos[j].round(7).tolist(),
                "range": model.jnt_range[j].tolist() if model.jnt_limited[j] else None,
                "passive": joint_name in info.passive,
                "motor": None
                if motor is None
                else {
                    "type": motor.type,
                    "label": kind.get("label", motor.type),
                    "peak_torque": motor.peak_torque,
                    "kp": motor.kp,
                    "kd": motor.kd,
                    "bus": motor.bus,
                    "node": motor.node,
                    "direction": motor.direction,
                    "zero": motor.zero,
                    "group": motor.group,
                    "low": motor.low,
                    "high": motor.high,
                    "actuator": motor.actuator,
                    "driver": types.get(motor.type, {}).get("driver", ""),
                    "protocol": types.get(motor.type, {}).get("protocol", ""),
                },
            }
        )
    materials = {}
    for g in range(model.ngeom):
        mat = int(model.geom_matid[g])
        if mat >= 0:
            materials[mat] = name(mujoco.mjtObj.mjOBJ_MATERIAL, mat)
    geoms = []
    for g in range(model.ngeom):
        if model.geom_contype[g] or model.geom_conaffinity[g] or not geo.is_drawn(model, g):
            continue
        mat = int(model.geom_matid[g])
        rgba = model.mat_rgba[mat] if mat >= 0 else model.geom_rgba[g]
        geoms.append(
            {
                "id": g,
                "body": int(model.geom_bodyid[g]),
                "type": int(model.geom_type[g]),
                "mesh": int(model.geom_dataid[g]) if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH else -1,
                "pos": model.geom_pos[g].round(7).tolist(),
                "quat": model.geom_quat[g].round(9).tolist(),
                "size": model.geom_size[g].round(7).tolist(),
                "material": materials.get(mat, ""),
                "rgba": [round(float(c), 3) for c in rgba],
            }
        )
    struts = []
    for strut in info.struts:
        upper, lower = model.site(strut.upper_site), model.site(strut.lower_site)
        struts.append(
            {
                "name": strut.name,
                "position": strut.position,
                "side": strut.side,
                "upper": {"body": int(upper.bodyid[0]), "pos": upper.pos.round(7).tolist()},
                "lower": {"body": int(lower.bodyid[0]), "pos": lower.pos.round(7).tolist()},
                "length_min": strut.length_min,
                "length_max": strut.length_max,
                "length_cad": strut.length_cad,
            }
        )
    rods = [
        {
            "name": rod.name,
            "side": rod.side,
            "moved": model.body(rod.moved_body).id,
            "direction": rod.direction,
            "base_pos": rod.base_pos,
            "segment_cad": rod.length_cad,
            "rods": [
                {
                    "body": model.body(piece["body"]).id,
                    "top": piece["top"],
                    "bottom": piece["bottom"],
                    "direction": piece["direction"],
                    "radius": piece["radius"],
                    "length": piece["length"],
                }
                for piece in rod.rods
            ],
        }
        for rod in info.rods
    ]
    links = []
    for b in range(1, model.nbody):
        if model.body_jntnum[b] == 0 and model.body_parentid[b] != 0:
            continue
        members = [x for x in range(1, model.nbody) if link_of(x) == b]
        joint = next((j for j in range(model.njnt) if model.jnt_bodyid[j] == b), -1)
        joint_name = name(mujoco.mjtObj.mjOBJ_JOINT, joint) if joint >= 0 else ""
        links.append(
            {
                "body": b,
                "joint": joint,
                "label": "Pelvis" if b == model.body(info.base).id else _joint_label(joint_name) if joint_name else _label(name(mujoco.mjtObj.mjOBJ_BODY, b)),
                "members": members,
                "mass": float(model.body_mass[members].sum()),
            }
        )
    sites = {}
    for s in range(model.nsite):
        site_name = name(mujoco.mjtObj.mjOBJ_SITE, s)
        if site_name.startswith("isla_sole_") or site_name == info.imu_site:
            sites[site_name] = {"body": int(model.site_bodyid[s]), "pos": model.site_pos[s].round(7).tolist()}
    return {
        "version": BUNDLE_VERSION,
        "robot": profile.to_dict(),
        "info": {
            "base": info.base,
            "torso": info.torso,
            "forward": info.forward,
            "left": info.left,
            "cad_mass": info.cad_mass,
            "virtual_mass": info.virtual_mass,
            "total_mass": info.total_mass,
            "height": info.height,
            "hip_height": info.hip_height,
            "stand_pitch": info.stand_pitch,
            "passive": info.passive,
        },
        "nq": model.nq,
        "nv": model.nv,
        "nu": model.nu,
        "qpos0": model.qpos0.tolist(),
        "stand": info.stand_qpos,
        "bodies": bodies,
        "joints": joints,
        "geoms": geoms,
        "struts": struts,
        "rods": rods,
        "links": links,
        "sites": sites,
        "actuators": [
            {
                "id": a,
                "name": name(mujoco.mjtObj.mjOBJ_ACTUATOR, a),
                "joint": name(mujoco.mjtObj.mjOBJ_JOINT, model.actuator_trnid[a, 0]),
            }
            for a in range(model.nu)
        ],
    }


def write_bundle(profile: RobotProfile, built: Built, folder: Path) -> None:
    """Write meta.json and meshes.bin for a static copy of the web app."""
    folder.mkdir(parents=True, exist_ok=True)
    blob, index = mesh_blob(profile, built)
    document = meta(profile, built)
    document["meshes"] = index
    (folder / "meta.json").write_text(json.dumps(document), encoding="utf-8")
    (folder / "meshes.bin").write_bytes(blob)
