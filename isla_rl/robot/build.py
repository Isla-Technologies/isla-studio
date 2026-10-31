"""Turn a robot's CAD export into simulation models: one to look at, one to train on.

A CAD export is rarely ready to simulate. Every part is its own body, all of them
collide with each other, there is no standing pose, and parts that matter to how
the robot moves (here: shock absorbers) may be missing. ``build`` fixes that:

- collision: the floor touches everything; each foot touches the floor through a
  box fitted to its sole; every other part only touches the floor, through one box
  per link (display model) or not at all (training model);
- mass: a virtual mass at the torso brings the robot to its real weight;
- shock absorbers: a spring-damper tendon between each strut's two mounts, with
  its stroke as the tendon's limits;
- rods: carbon-fibre rods become cylinders whose length the design sets, and the
  body below each rod slides along the segment by the same amount;
- motors: servo gains and torque limits from the profile's actuator types;
- a ``stand`` keyframe: knees bent, the centre of mass over the ankles, feet flat.

The CAD measurements this needs do not depend on the design and are cached.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import mujoco
import numpy as np

from .. import paths
from . import geometry as geo
from .profile import RobotProfile

FLOOR = "floor"
# Everything the platform adds to a robot is prefixed, so it never meets a name the
# robot already uses (many models have their own "stand" key or "sole" sites).
STAND_KEY = "isla_stand"
VIRTUAL_BODY = "isla_virtual_mass"
GANTRY = "isla_gantry"  # A harness the display model can hang in: a weld to a mocap body.
DISPLAY_TIMESTEP = 0.002
# Collision bits. Two geoms touch when (type1 & affinity2) or (type2 & affinity1).
FLOOR_TYPE, FLOOR_AFFINITY = 1, 0
BODY_TYPE, BODY_AFFINITY = 0, 1  # Robot parts: only the floor.
FOOT_TYPE, FOOT_AFFINITY = 2, 3  # Feet: the floor and each other.
SOLE_FRICTION = (1.0, 0.005, 0.0001)
MEASURE_VERSION = 5
AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


@dataclass
class FootInfo:
    side: str
    body: str
    geom: str
    site: str
    touch: str
    half: list[float]


@dataclass
class StrutInfo:
    name: str
    position: str
    side: str
    tendon: str
    upper_site: str
    lower_site: str
    length_cad: float
    length_min: float
    length_max: float
    stiffness: float  # N/m
    damping: float  # N*s/m
    free_length: float  # m: the coil's length with no load.


@dataclass
class RodInfo:
    name: str
    side: str
    moved_body: str
    parent_body: str
    direction: list[float]  # Unit vector in the parent body's frame.
    base_pos: list[float]  # The moved body's position at zero extension.
    extension: float  # m
    length_cad: float  # Joint-to-joint distance in the CAD, m.
    rods: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MotorInfo:
    joint: str
    actuator: str
    type: str
    peak_torque: float
    kp: float
    kd: float
    bus: str
    node: int
    direction: int
    zero: float
    group: str
    low: float
    high: float


@dataclass
class BuildInfo:
    robot: str
    name: str
    purpose: str
    design: dict[str, float]
    base: str
    torso: str | None
    imu_site: str | None
    forward: list[float]
    left: list[float]
    feet: list[FootInfo]
    struts: list[StrutInfo]
    rods: list[RodInfo]
    motors: list[MotorInfo]
    passive: list[str]
    cad_mass: float
    virtual_mass: float
    total_mass: float
    stand_qpos: list[float] = field(default_factory=list)
    stand_ctrl: list[float] = field(default_factory=list)
    stand_pitch: float = 0.0  # rad the body leans forward standing.
    height: float = 0.0
    hip_height: float = 0.0
    com_height: float = 0.0
    leg_length: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Built:
    spec: mujoco.MjSpec
    model: mujoco.MjModel
    info: BuildInfo


# CAD measurements.


def _source_hash(profile: RobotProfile) -> str:
    digest = hashlib.sha256()
    digest.update(profile.source_path.read_bytes())
    digest.update(json.dumps(
        {k: profile.data.get(k) for k in ("feet", "struts", "rods", "mass", "torso", "base")},
        sort_keys=True,
    ).encode())
    digest.update(str(MEASURE_VERSION).encode())
    return digest.hexdigest()[:16]


def measure(profile: RobotProfile, refresh: bool = False) -> dict[str, Any]:
    """Everything the builder takes from the CAD pose, cached per source model."""
    key = _source_hash(profile)
    cache = paths.CACHE / profile.id / f"cad-{key}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    spec = mujoco.MjSpec.from_file(str(profile.source_path))
    model = spec.compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    result = _measure(profile, model, data)
    result["source_hash"] = key
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def _name(model: mujoco.MjModel, kind: mujoco.mjtObj, index: int) -> str:
    return mujoco.mj_id2name(model, kind, index) or ""


def _measure(profile: RobotProfile, model: mujoco.MjModel, data: mujoco.MjData) -> dict[str, Any]:
    body_id = lambda name: model.body(name).id  # noqa: E731
    result: dict[str, Any] = {"cad_mass": float(mujoco.mj_getTotalmass(model))}

    # Kinematic links: each jointed body with the parts welded below it.
    links: dict[int, list[int]] = {}
    for body in range(1, model.nbody):
        links.setdefault(geo.link_of(model, body), []).append(body)
    # The highest point (top of the head): the robot's height when it stands.
    best, best_body, best_point = -math.inf, 0, None
    drawn = [g for b in range(1, model.nbody) for g in geo.visual_geoms(model, b)]
    for geom in drawn:
        points = geo.geom_world_points(model, data, geom)
        if not len(points):
            continue
        i = int(np.argmax(points[:, 2]))
        if points[i, 2] > best:
            best, best_body, best_point = float(points[i, 2]), int(model.geom_bodyid[geom]), points[i]
    if best_point is not None:
        result["top"] = {
            "body": _name(model, mujoco.mjtObj.mjOBJ_BODY, best_body),
            "local": geo.to_local(model, data, best_body, best_point).tolist(),
            "cad_z": best,
        }

    result["links"] = {
        _name(model, mujoco.mjtObj.mjOBJ_BODY, link): [
            _name(model, mujoco.mjtObj.mjOBJ_BODY, b) for b in bodies
        ]
        for link, bodies in links.items()
    }

    # The torso's centre of mass, where the virtual mass goes.
    torso = profile.torso
    if torso:
        members = links[geo.link_of(model, body_id(torso))]
        masses = model.body_mass[members]
        com = (data.xipos[members] * masses[:, None]).sum(axis=0) / max(masses.sum(), 1e-9)
        result["torso_com"] = com.tolist()
        result["torso_mass"] = float(masses.sum())
        parent = body_id(profile.mass.get("virtual_body") or torso)
        pos, quat = geo.local_pose_under(data, parent, com, np.array([1.0, 0, 0, 0]))
        result["virtual_pos"], result["virtual_quat"] = pos.tolist(), quat.tolist()

    # Feet: a box around each sole, in the foot link's frame, square to the world.
    feet = []
    for foot in profile.feet:
        link = body_id(foot["body"])
        sole = body_id(foot.get("sole", foot["body"]))
        points = geo.body_points(model, data, sole)
        if len(points) == 0:
            points = np.concatenate([geo.body_points(model, data, b) for b in links[link]])
        low, high = geo.aabb(points)
        center = (low + high) / 2
        pos, quat = geo.local_pose_under(data, link, center, np.array([1.0, 0, 0, 0]))
        feet.append(
            {
                "side": foot["side"],
                "body": foot["body"],
                "pos": pos.tolist(),
                "quat": quat.tolist(),
                "half": ((high - low) / 2).tolist(),
                "bottom": float(low[2]),
            }
        )
    result["feet"] = feet

    # Shock absorber mounts: the centre of each mounting shaft.
    struts = []
    for item in profile.struts:
        upper, lower = body_id(item["upper"]), body_id(item["lower"])
        top, bottom = geo.body_center(model, data, upper), geo.body_center(model, data, lower)
        struts.append(
            {
                "name": item["name"],
                "position": item["position"],
                "side": item["side"],
                "upper_body": item["upper"],
                "lower_body": item["lower"],
                "upper_local": geo.to_local(model, data, upper, top).tolist(),
                "lower_local": geo.to_local(model, data, lower, bottom).tolist(),
                "length_cad": float(np.linalg.norm(top - bottom)),
                "upper_world": top.tolist(),
                "lower_world": bottom.tolist(),
            }
        )
    result["struts"] = struts

    # Rods: the segment each changes, and each rod's axis, ends and radius.
    rods = []
    for item in profile.rods:
        start = model.joint(item["from_joint"]).id
        end = model.joint(item["to_joint"]).id
        axis = data.xaxis[start]
        span = data.xanchor[end] - data.xanchor[start]
        span = span - np.dot(span, axis) * axis  # Joint axes are lines: drop the offset along them.
        length = float(np.linalg.norm(span))
        direction = span / length
        moved = body_id(item["moved"])
        parent = int(model.body_parentid[moved])
        pieces = []
        for rod_name in item["rods"]:
            rod = body_id(rod_name)
            points = geo.body_points(model, data, rod)
            mean, rod_axis = geo.principal_axis(points)
            if np.dot(rod_axis, direction) < 0:
                rod_axis = -rod_axis
            t = (points - mean) @ rod_axis
            top_w, bottom_w = mean + t.min() * rod_axis, mean + t.max() * rod_axis
            radial = points - mean - np.outer(t, rod_axis)
            radius = float(np.percentile(np.linalg.norm(radial, axis=1), 98))
            pieces.append(
                {
                    "body": rod_name,
                    "geoms": [
                        _name(model, mujoco.mjtObj.mjOBJ_GEOM, g)
                        for g in range(model.ngeom)
                        if model.geom_bodyid[g] == rod
                    ],
                    "top": geo.to_local(model, data, rod, top_w).tolist(),
                    "bottom": geo.to_local(model, data, rod, bottom_w).tolist(),
                    "direction": geo.dir_to_local(data, rod, direction).tolist(),
                    "radius": radius,
                    "length": float(t.max() - t.min()),
                    "mass": float(model.body_mass[rod]),
                }
            )
        rods.append(
            {
                "name": item["name"],
                "side": item["side"],
                "moved": item["moved"],
                "parent": _name(model, mujoco.mjtObj.mjOBJ_BODY, parent),
                "direction": geo.dir_to_local(data, parent, direction).tolist(),
                "base_pos": model.body_pos[moved].tolist(),
                "length_cad": length,
                "rods": pieces,
            }
        )
    result["rods"] = rods

    # One box per link for the display model's floor contacts (not the feet).
    foot_links = {f["body"] for f in profile.feet}
    rod_bodies = {piece for item in profile.rods for piece in item["rods"]}
    boxes = []
    for link, bodies in links.items():
        name = _name(model, mujoco.mjtObj.mjOBJ_BODY, link)
        if name in foot_links:
            continue
        clouds = [geo.body_points(model, data, b) for b in bodies if _name(model, mujoco.mjtObj.mjOBJ_BODY, b) not in rod_bodies]
        clouds = [c for c in clouds if len(c)]
        if not clouds:
            continue
        points = np.concatenate(clouds)
        mean = points.mean(axis=0)
        _, _, vt = np.linalg.svd(points - mean, full_matrices=False)
        frame = vt.T  # Columns: principal directions.
        if np.linalg.det(frame) < 0:
            frame[:, 2] *= -1
        local = (points - mean) @ frame
        low, high = local.min(axis=0), local.max(axis=0)
        center = mean + frame @ ((low + high) / 2)
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, frame.flatten())
        pos, q = geo.local_pose_under(data, link, center, quat)
        boxes.append({"body": name, "pos": pos.tolist(), "quat": q.tolist(), "half": ((high - low) / 2).tolist()})
    result["link_boxes"] = boxes
    return result


# Building.


def _axis(label: str) -> np.ndarray:
    return np.array(AXES.get(label, (1, 0, 0)), dtype=float)


def build(
    profile: RobotProfile,
    design: dict[str, float] | None = None,
    purpose: str = "display",
) -> Built:
    """A MuJoCo model of the robot with ``design`` applied, for ``display`` or ``train``."""
    if purpose not in ("display", "train"):
        raise ValueError("purpose is display or train")
    design = profile.complete_design(design)
    cad = measure(profile)
    spec = mujoco.MjSpec.from_file(str(profile.source_path))
    spec.modelname = profile.id
    spec.option.timestep = DISPLAY_TIMESTEP
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    _freeze_inertia(spec)

    base = profile.base or _first_body(spec)
    if _ensure_free_joint(spec, base):
        _make_room_in_keys(spec, base)
    _collision(spec, profile, cad, purpose)
    virtual = _virtual_mass(spec, profile, cad)
    feet = _feet(spec, profile, cad)
    rods = _rods(spec, profile, cad, design, purpose)
    struts = _struts(spec, profile, cad, design)
    motors = _motors(spec, profile)
    for key in spec.keys:
        key.ctrl, key.act = [], []  # Sized for the model's own actuators, which may have changed.
    if purpose == "train":
        _strip_visuals(spec)
    else:
        _gantry(spec, base)
    if not any(key.name == STAND_KEY for key in spec.keys):
        spec.add_key(name=STAND_KEY)
    model = spec.compile()

    passive = [
        _name(model, mujoco.mjtObj.mjOBJ_JOINT, j)
        for j in range(model.njnt)
        if model.jnt_type[j] in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE)
        and _name(model, mujoco.mjtObj.mjOBJ_JOINT, j) not in {m.joint for m in motors}
    ]
    info = BuildInfo(
        robot=profile.id,
        name=profile.name,
        purpose=purpose,
        design=design,
        base=base,
        torso=profile.torso,
        imu_site=profile.imu_site,
        forward=_axis(profile.forward).tolist(),
        left=_axis(profile.left).tolist(),
        feet=feet,
        struts=struts,
        rods=rods,
        motors=motors,
        passive=passive,
        cad_mass=float(cad["cad_mass"]),
        virtual_mass=virtual,
        total_mass=float(mujoco.mj_getTotalmass(model)),
    )
    _stand(spec, model, profile, info, cad)
    return Built(spec, model, info)


def _freeze_inertia(spec: mujoco.MjSpec) -> None:
    """Every body keeps the mass the model gives it, and the shapes added here (contact boxes,
    rods) add none. A model whose bodies take their mass from their shapes has it worked
    out once, with the model's own settings, and written into the bodies first."""
    if any(not body.explicitinertial for body in spec.bodies[1:]):
        model = spec.compile()
        for body in spec.bodies[1:]:
            if body.explicitinertial:
                continue
            b = model.body(body.name).id
            body.explicitinertial = True
            body.mass = float(model.body_mass[b])
            body.ipos = model.body_ipos[b].tolist()
            body.iquat = model.body_iquat[b].tolist()
            body.inertia = model.body_inertia[b].tolist()
            body.fullinertia = [math.nan, 0, 0, 0, 0, 0]  # Unset: the principal inertia above holds.
    spec.compiler.inertiafromgeom = mujoco.mjtInertiaFromGeom.mjINERTIAFROMGEOM_FALSE


def _first_body(spec: mujoco.MjSpec) -> str:
    return spec.worldbody.first_body().name


def _ensure_free_joint(spec: mujoco.MjSpec, base: str) -> bool:
    """Give the base a free joint, as a URDF's root lacks one. True when one was added."""
    body = spec.body(base)
    if any(j.type == mujoco.mjtJoint.mjJNT_FREE for j in body.joints):
        return False
    body.add_freejoint(name=f"{base}_free")
    return True


def _make_room_in_keys(spec: mujoco.MjSpec, base: str) -> None:
    """Keyframes written before the base had a free joint: room for it in front when it comes
    first (the base is the first body and the new joint its only one), else the zero pose."""
    first = spec.worldbody.first_body()
    in_front = first is not None and first.name == base and len(spec.body(base).joints) == 1
    for key in spec.keys:
        if in_front:
            if len(key.qpos):
                key.qpos = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, *key.qpos]
            if len(key.qvel):
                key.qvel = [0.0] * 6 + list(key.qvel)
        else:
            key.qpos, key.qvel = [], []


def _collision(spec: mujoco.MjSpec, profile: RobotProfile, cad: dict, purpose: str) -> None:
    floor_named = False
    drawn = lambda g: g.group < 3 and (g.rgba[3] >= 0.01 or bool(g.material))  # noqa: E731
    for body in spec.bodies:
        geoms = list(body.geoms)
        # A body drawn by its own visual geoms (most URDF and MJCF exports) has
        # colliding geoms that only copy them.
        has_visuals = any(drawn(g) and not (g.contype or g.conaffinity) for g in geoms)
        for geom in geoms:
            if geom.type == mujoco.mjtGeom.mjGEOM_PLANE and not floor_named:
                geom.name = FLOOR
                geom.contype, geom.conaffinity = FLOOR_TYPE, FLOOR_AFFINITY
                geom.margin = 0.0  # A model's default margin would stop MuJoCo Warp (GPU training).
                floor_named = True
                continue
            if not (geom.contype or geom.conaffinity):
                continue
            if not drawn(geom) or has_visuals or geom.type == mujoco.mjtGeom.mjGEOM_PLANE:
                spec.delete(geom)  # Collision copies overlap and slow everything: boxes replace them.
            else:
                geom.contype, geom.conaffinity = 0, 0  # Its only shape: keep it to draw.
    if not any(g.name == FLOOR for g in spec.geoms):
        floor = spec.worldbody.add_geom(name=FLOOR, type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.1], margin=0.0)
        floor.contype, floor.conaffinity = FLOOR_TYPE, FLOOR_AFFINITY
    if purpose != "display":
        return
    for box in cad["link_boxes"]:
        geom = spec.body(box["body"]).add_geom(
            name=f"isla_box_{box['body']}",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            pos=box["pos"],
            quat=box["quat"],
            size=box["half"],
            group=3,
            margin=0.0,
            rgba=[1, 1, 1, 0],
        )
        geom.contype, geom.conaffinity = BODY_TYPE, BODY_AFFINITY


def _virtual_mass(spec: mujoco.MjSpec, profile: RobotProfile, cad: dict) -> float:
    settings = profile.mass
    target = settings.get("target_total")
    if not target or "torso_com" not in cad:
        return 0.0
    added = float(target) - float(cad["cad_mass"])
    if added <= 1e-6:
        return 0.0
    parent_name = settings.get("virtual_body") or profile.torso
    box = np.array(settings.get("virtual_box", [0.2, 0.12, 0.2]), dtype=float)
    body = spec.body(parent_name).add_body(name=VIRTUAL_BODY, pos=cad["virtual_pos"], quat=cad["virtual_quat"])
    body.explicitinertial = True
    body.mass = added
    body.ipos = [0, 0, 0]
    body.inertia = geo.box_inertia(added, box).tolist()
    return added


def _feet(spec: mujoco.MjSpec, profile: RobotProfile, cad: dict) -> list[FootInfo]:
    feet = []
    for foot in cad["feet"]:
        side, body = foot["side"], spec.body(foot["body"])
        half = np.array(foot["half"], dtype=float)
        half[2] = max(half[2], 0.004)
        geom = body.add_geom(
            name=f"isla_sole_{side}",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            pos=foot["pos"],
            quat=foot["quat"],
            size=half.tolist(),
            friction=list(SOLE_FRICTION),
            condim=3,
            margin=0.0,
            group=3,
            rgba=[1, 1, 1, 0],
        )
        geom.contype, geom.conaffinity = FOOT_TYPE, FOOT_AFFINITY
        rotation = geo.quat_to_mat(np.array(foot["quat"]))
        bottom = np.array(foot["pos"]) - rotation @ np.array([0.0, 0.0, half[2]])
        body.add_site(name=f"isla_sole_{side}", pos=bottom.tolist(), quat=foot["quat"], size=[0.005, 0.005, 0.005])
        # A box a little larger than the sole senses the floor pushing on the foot.
        body.add_site(
            name=f"isla_touch_{side}",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            pos=foot["pos"],
            quat=foot["quat"],
            size=(half + np.array([0.003, 0.003, 0.006])).tolist(),
            rgba=[1, 1, 1, 0],
        )
        for kind, name, target in (
            (mujoco.mjtSensor.mjSENS_TOUCH, f"isla_touch_{side}", f"isla_touch_{side}"),
            (mujoco.mjtSensor.mjSENS_FRAMEPOS, f"isla_sole_{side}_pos", f"isla_sole_{side}"),
            (mujoco.mjtSensor.mjSENS_FRAMELINVEL, f"isla_sole_{side}_vel", f"isla_sole_{side}"),
        ):
            sensor = spec.add_sensor(name=name, type=kind)
            sensor.objtype, sensor.objname = mujoco.mjtObj.mjOBJ_SITE, target
        feet.append(FootInfo(side, foot["body"], f"isla_sole_{side}", f"isla_sole_{side}", f"isla_touch_{side}", half.tolist()))
    return feet


def _rods(
    spec: mujoco.MjSpec, profile: RobotProfile, cad: dict, design: dict[str, float], purpose: str
) -> list[RodInfo]:
    extensions = {
        param.rod: design[key] / 1000.0
        for key, param in profile.design.items()
        if param.kind == "rod" and param.rod
    }
    rods = []
    for item in cad["rods"]:
        extension = float(extensions.get(item["name"], 0.0))
        direction = np.array(item["direction"])
        base_pos = np.array(item["base_pos"])
        spec.body(item["moved"]).pos = (base_pos + extension * direction).tolist()
        pieces = []
        for piece in item["rods"]:
            body = spec.body(piece["body"])
            for geom_name in piece["geoms"]:
                geom = spec.geom(geom_name)
                if geom is not None:
                    spec.delete(geom)
            top = np.array(piece["top"])
            bottom = np.array(piece["bottom"]) + extension * np.array(piece["direction"])
            length = float(np.linalg.norm(bottom - top))
            mass = piece["mass"] * length / max(piece["length"], 1e-6)
            body.explicitinertial = True
            body.fullinertia = [math.nan, 0, 0, 0, 0, 0]  # Unset: the cylinder's inertia replaces the CAD's.
            body.mass = mass
            body.ipos = ((top + bottom) / 2).tolist()
            body.iquat = geo.quat_from_z(bottom - top).tolist()
            body.inertia = geo.cylinder_inertia(mass, piece["radius"], length).tolist()
            if purpose == "display":
                body.add_geom(
                    name=f"{piece['body']}__rod",
                    type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                    fromto=[*top.tolist(), *bottom.tolist()],
                    size=[piece["radius"], 0, 0],
                    material="carbon_fiber_dark_gray" if spec.material("carbon_fiber_dark_gray") else "",
                    contype=0,
                    conaffinity=0,
                    group=2,
                )
            pieces.append({**piece, "length_now": length, "bottom_now": bottom.tolist()})
        rods.append(
            RodInfo(
                name=item["name"],
                side=item["side"],
                moved_body=item["moved"],
                parent_body=item["parent"],
                direction=item["direction"],
                base_pos=item["base_pos"],
                extension=extension,
                length_cad=item["length_cad"],
                rods=pieces,
            )
        )
    return rods


def strut_values(profile: RobotProfile, design: dict[str, float], position: str) -> tuple[float, float, float]:
    """Spring rate (N/m), damping (N*s/m) and preload (m) of the front or back struts."""
    k = c = preload = None
    for key, param in profile.design.items():
        if param.position != position:
            continue
        if param.kind == "strut_k":
            k = design[key] * 1000.0
        elif param.kind == "strut_c":
            c = design[key]
        elif param.kind == "strut_preload":
            preload = design[key] / 1000.0
    return (k if k is not None else 12000.0, c if c is not None else 150.0, preload if preload is not None else 0.003)


def _struts(spec: mujoco.MjSpec, profile: RobotProfile, cad: dict, design: dict[str, float]) -> list[StrutInfo]:
    struts = []
    half_stroke = profile.strut_stroke / 2
    for item in cad["struts"]:
        name = f"isla_strut_{item['name']}"
        spec.body(item["upper_body"]).add_site(name=f"{name}_upper", pos=item["upper_local"], size=[0.004, 0, 0])
        spec.body(item["lower_body"]).add_site(name=f"{name}_lower", pos=item["lower_local"], size=[0.004, 0, 0])
        k, c, preload = strut_values(profile, design, item["position"])
        length_cad = item["length_cad"]
        low, high = length_cad - half_stroke, length_cad + half_stroke
        free = high + preload
        tendon = spec.add_tendon(name=name)
        tendon.stiffness = [k, 0.0, 0.0]  # MuJoCo 3.15: linear term, then polynomial terms.
        tendon.damping = [c, 0.0, 0.0]
        tendon.springlength = [free, free]
        tendon.limited = mujoco.mjtLimited.mjLIMITED_TRUE
        tendon.range = [low, high]
        tendon.width = 0.005
        tendon.rgba = [0.95, 0.95, 0.95, 1.0]
        tendon.wrap_site(f"{name}_upper")
        tendon.wrap_site(f"{name}_lower")
        sensor = spec.add_sensor(name=f"{name}_length", type=mujoco.mjtSensor.mjSENS_TENDONPOS)
        sensor.objtype, sensor.objname = mujoco.mjtObj.mjOBJ_TENDON, name
        struts.append(
            StrutInfo(
                name=item["name"],
                position=item["position"],
                side=item["side"],
                tendon=name,
                upper_site=f"{name}_upper",
                lower_site=f"{name}_lower",
                length_cad=length_cad,
                length_min=low,
                length_max=high,
                stiffness=k,
                damping=c,
                free_length=free,
            )
        )
    return struts


def _motors(spec: mujoco.MjSpec, profile: RobotProfile) -> list[MotorInfo]:
    by_joint = {}
    for actuator in spec.actuators:
        if actuator.target:
            by_joint[actuator.target] = actuator
    ranges = profile.operating_ranges
    motors = []
    for motor in profile.motors:
        kind = profile.actuator_types[motor.type]
        joint = spec.joint(motor.joint)
        if joint is None:
            raise ValueError(f"The model has no joint {motor.joint}")
        actuator = by_joint.get(motor.joint)
        if actuator is None:
            actuator = spec.add_actuator(name=f"{motor.joint}_{motor.type}", target=motor.joint)
            actuator.trntype = mujoco.mjtTrn.mjTRN_JOINT
        kp, kd = float(kind.get("kp", 50.0)), float(kind.get("kd", 2.0))
        peak = float(kind.get("peak_torque", 20.0))
        actuator.set_to_position(kp=kp, kv=kd)
        actuator.forcelimited = mujoco.mjtLimited.mjLIMITED_TRUE
        actuator.forcerange = [-peak, peak]
        if motor.joint in ranges:
            low, high = ranges[motor.joint]
        elif joint.limited == mujoco.mjtLimited.mjLIMITED_TRUE or (joint.range[0] < joint.range[1]):
            low, high = float(joint.range[0]), float(joint.range[1])
        else:
            low, high = -math.pi, math.pi
        if low >= high:
            low, high = -math.pi, math.pi
        actuator.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE
        actuator.ctrlrange = [low, high]
        # Rotor inertia and joint losses, when the actuator type knows them; else the model's own.
        if "armature" in kind:
            joint.armature = float(kind["armature"])
        if "damping" in kind:
            joint.damping = [float(kind["damping"]), 0.0, 0.0]
        if "frictionloss" in kind:
            joint.frictionloss = float(kind["frictionloss"])
        motors.append(
            MotorInfo(
                joint=motor.joint,
                actuator=actuator.name,
                type=motor.type,
                peak_torque=peak,
                kp=kp,
                kd=kd,
                bus=motor.bus,
                node=motor.node,
                direction=motor.direction,
                zero=motor.zero,
                group=motor.group,
                low=low,
                high=high,
            )
        )
    # Actuators the profile does not list are removed: every motor must be known.
    listed = {m.actuator for m in motors}
    for actuator in list(spec.actuators):
        if actuator.name not in listed:
            spec.delete(actuator)
    return motors


def _gantry(spec: mujoco.MjSpec, base: str) -> None:
    """A mocap body the base can be welded to, off until switched on. Soft, like a harness."""
    spec.worldbody.add_body(name=GANTRY, mocap=True)
    weld = spec.add_equality(name=GANTRY, type=mujoco.mjtEq.mjEQ_WELD, objtype=mujoco.mjtObj.mjOBJ_BODY)
    weld.name1, weld.name2 = base, GANTRY
    weld.data = [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1]  # Anchor, then the gantry's pose on the base: the same.
    weld.solref = [0.05, 1.0]
    weld.active = False


def _strip_visuals(spec: mujoco.MjSpec) -> None:
    """The training copy: only what touches the floor stays."""
    for geom in list(spec.geoms):
        if geom.contype or geom.conaffinity:
            continue
        spec.delete(geom)
    for mesh in list(spec.meshes):
        spec.delete(mesh)
    for light in list(spec.lights):
        spec.delete(light)


# The standing pose.


def _stand(spec: mujoco.MjSpec, model: mujoco.MjModel, profile: RobotProfile, info: BuildInfo, cad: dict) -> None:
    from .design import solve_stand  # The solver also serves the design search.

    pose = solve_stand(model, profile, info)
    key = model.key(STAND_KEY).id
    model.key_qpos[key] = pose.qpos
    model.key_ctrl[key] = pose.ctrl
    spec.key(STAND_KEY).qpos = pose.qpos.tolist()
    spec.key(STAND_KEY).ctrl = pose.ctrl.tolist()
    info.stand_qpos = pose.qpos.tolist()
    info.stand_ctrl = pose.ctrl.tolist()
    info.stand_pitch = pose.pitch
    info.hip_height = pose.hip_height
    data = mujoco.MjData(model)
    data.qpos[:] = pose.qpos
    mujoco.mj_kinematics(model, data)
    mujoco.mj_comPos(model, data)
    info.com_height = float(data.subtree_com[model.body(info.base).id][2])
    info.height = top_height(model, data, cad)
    sides = {r.side for r in info.rods}
    if sides:
        side = sorted(sides)[0]
        info.leg_length = float(sum(r.length_cad + r.extension for r in info.rods if r.side == side))
    else:
        info.leg_length = info.hip_height


def top_height(model: mujoco.MjModel, data: mujoco.MjData, cad: dict) -> float:
    """Height of the robot's highest CAD point (top of the head) as posed in ``data``."""
    top = cad.get("top")
    if not top:
        return float("nan")
    body = model.body(top["body"]).id
    point = data.xpos[body] + data.xmat[body].reshape(3, 3) @ np.array(top["local"])
    return float(point[2])


def _free_joint(model: mujoco.MjModel, base: str) -> int:
    body = model.body(base).id
    for j in range(model.jnt_bodyid.size):
        if model.jnt_bodyid[j] == body and model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
            return j
    raise ValueError(f"{base} has no free joint")


def to_xml(built: Built) -> str:
    """The built model as MJCF, meshes referenced from the robot's source folder."""
    return built.spec.to_xml()
