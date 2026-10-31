"""Bring any legged robot into the hangar: an MJCF or URDF model, its meshes, a detected profile.

The platform needs to know a few things a model file does not say outright: which
body is the base, where the feet are, which way the robot faces, how its motors
group into limbs and sit on CAN buses, and how it stands. The importer reads them
off the model:

- base: the body with the free joint (a URDF's root, which then gets one);
- stance: the model's own keyframe if it has one, else its zero pose;
- feet: the parts that reach lowest, split into a left and a right group;
- facing: feet reach further ahead of their ankles than behind, so the axis
  they point along is forward;
- motors: every actuated joint, with the model's torque limit and servo gains (or
  gains estimated from the torque, for torque motors), a limb from the joint's
  name and a default CAN map, one bus per limb. A model with no actuators (every
  URDF: MuJoCo does not read URDF transmissions) gets a motor on each joint,
  as strong as the joint's effort limit.

Everything detected is written to the robot's ``robot.json`` and can be corrected there.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from .. import paths
from . import geometry as geo
from .profile import FORMAT, RobotProfile

MESH_EXTENSIONS = (".stl", ".obj", ".msh")
LEG = ("hip", "knee", "ankle", "thigh", "shin", "foot", "leg", "toe")
ARM = ("shoulder", "elbow", "wrist", "forearm", "arm", "hand", "finger", "grip")
WAIST = ("waist", "torso", "spine", "chest", "trunk")
HEAD = ("neck", "head")
BUSES = {"left_leg": "can0", "right_leg": "can1", "left_arm": "can2", "right_arm": "can3"}
FOOT_BAND = 0.02  # m above the lowest point that still counts as a foot.
SEARCH_LIMIT = 20_000  # Files looked at when searching for a mesh by name.
ACRONYMS = {"urdf", "mjcf", "mk", "dof", "imu", "ai", "rl", "cad", "usa", "uk"}


class ImportProblem(ValueError):
    """The model cannot be imported, with what to do about it."""


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return value or "robot"


def pretty(text: str) -> str:
    """A model's name as people write it: 'kinetic_mk1' -> 'Kinetic MK1'."""
    words = [w for w in re.split(r"[_\-\s]+", text) if w]
    return " ".join(w.upper() if any(c.isdigit() for c in w) or w.lower() in ACRONYMS else w.capitalize() for w in words) or "Robot"


def _side(name: str) -> str:
    n = name.lower()
    if "left" in n or re.search(r"(^|[_\-.])l([_\-.]|$)", n):
        return "left"
    if "right" in n or re.search(r"(^|[_\-.])r([_\-.]|$)", n):
        return "right"
    return ""


def limb(joint: str) -> str:
    """The limb a joint belongs to, from its name: ``left_leg``, ``waist``, ``other``..."""
    n = joint.lower()
    if any(k in n for k in LEG):
        part = "leg"
    elif any(k in n for k in ARM):
        part = "arm"
    elif any(k in n for k in WAIST):
        part = "waist"
    elif any(k in n for k in HEAD):
        part = "head"
    else:
        part = "other"
    side = _side(n)
    return f"{side}_{part}" if side and part in ("leg", "arm") else part


def _load_spec(source: Path) -> mujoco.MjSpec:
    """Read MJCF or URDF. A URDF keeps its visual meshes and its root link (MuJoCo
    drops the first and fuses the second into the world by default), and the root
    gets the free joint a URDF cannot write."""
    if source.suffix.lower() != ".urdf":
        return mujoco.MjSpec.from_file(str(source))
    text = source.read_text(encoding="utf-8")
    text = re.sub(r"package://[^/\"']+/", "", text)
    if "<mujoco>" not in text:
        text = re.sub(r"(<robot\b[^>]*>)", r'\1<mujoco><compiler discardvisual="false" fusestatic="false"/></mujoco>', text, count=1)
    with tempfile.TemporaryDirectory(prefix="isla-urdf-") as folder:
        fixed = Path(folder) / source.name  # The model's own folder may be read-only.
        fixed.write_text(text, encoding="utf-8")
        spec = mujoco.MjSpec.from_file(str(fixed))
    spec.compiler.fusestatic = False
    root = spec.worldbody.first_body()
    if root is not None and not any(j.type == mujoco.mjtJoint.mjJNT_FREE for j in root.joints):
        root.add_freejoint(name=f"{root.name}_free")
    return spec


def _find(base: Path, folder: Path, file: str) -> Path | None:
    """A mesh or texture file as the model names it, or else by its name under the model's
    folder or the one above it (ROS packages keep urdf/ and meshes/ side by side)."""
    for candidate in (base / folder / file, base / file, Path(file)):
        if candidate.is_file():
            return candidate
    name = Path(file).name.lower()
    roots = [base] if base.parent in (base, Path.home(), Path(base.anchor)) else [base, base.parent]
    for root in roots:
        for seen, candidate in enumerate(root.rglob("*")):
            if seen > SEARCH_LIMIT:
                break
            if candidate.name.lower() == name and candidate.is_file():
                return candidate
    return None


def _copy_assets(spec: mujoco.MjSpec, source: Path, target: Path) -> None:
    """Copy every mesh and texture file beside the model and point the spec at the copies."""
    base = source.parent
    meshdir = Path(spec.meshdir) if spec.meshdir else Path()
    texturedir = Path(spec.texturedir) if spec.texturedir else Path()
    (target / "meshes").mkdir(parents=True, exist_ok=True)
    copied: dict[Path, str] = {}
    for mesh in spec.meshes:
        if not mesh.file:
            continue  # Vertices written in the model itself.
        found = _find(base, meshdir, mesh.file)
        if found is None:
            raise ImportProblem(f"Mesh file not found: {mesh.file}. Keep the model's meshes beside it.")
        if found.suffix.lower() not in MESH_EXTENSIONS:
            raise ImportProblem(f"{found.name}: MuJoCo reads STL, OBJ and MSH meshes. Convert the {found.suffix} meshes first.")
        if found not in copied:
            name, n = found.name, 1
            while name.lower() in {v.lower() for v in copied.values()}:
                name = f"{found.stem}_{n}{found.suffix}"
                n += 1
            shutil.copy2(found, target / "meshes" / name)
            copied[found] = name
        mesh.file = copied[found]
    for texture in spec.textures:
        if texture.file:
            found = _find(base, texturedir, texture.file)
            if found is None:
                raise ImportProblem(f"Texture file not found: {texture.file}")
            (target / "textures").mkdir(exist_ok=True)
            shutil.copy2(found, target / "textures" / found.name)
            texture.file = found.name
    spec.meshdir = "meshes/"
    spec.texturedir = "textures/"
    spec.modelfiledir = str(target)  # Writing the model compiles it: from the copies, not the source.


def _add_motors(spec: mujoco.MjSpec) -> int:
    """A torque motor on every hinge and slide joint, when no actuator drives any of them."""
    driven = {a.target for a in spec.actuators if a.trntype == mujoco.mjtTrn.mjTRN_JOINT}
    joints = [j for j in spec.joints if j.type in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE)]
    if any(j.name in driven for j in joints):
        return 0
    for joint in joints:
        effort = float(max(abs(joint.actfrcrange[0]), abs(joint.actfrcrange[1])))
        actuator = spec.add_actuator(name=f"{joint.name}_motor", target=joint.name)
        actuator.trntype = mujoco.mjtTrn.mjTRN_JOINT
        actuator.set_to_motor()
        actuator.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE
        actuator.ctrlrange = [-effort, effort] if effort > 0 else [-20.0, 20.0]
    return len(joints)


def _body_name(model: mujoco.MjModel, body: int) -> str:
    return mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body) or f"body{body}"


def _detect(model: mujoco.MjModel) -> dict:
    """Base, stance, feet, facing, IMU and torso, read off a compiled model."""
    data = mujoco.MjData(model)
    free = [j for j in range(model.njnt) if int(model.jnt_type[j]) == mujoco.mjtJoint.mjJNT_FREE.value]
    base = int(model.jnt_bodyid[free[0]]) if free else int(np.flatnonzero(model.body_parentid == 0)[1])
    keyframe = None
    if model.nkey:
        names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_KEY, k) or "" for k in range(model.nkey)]
        keyframe = next((n for n in names if "stand" in n.lower()), names[0]) or None
        if keyframe:
            mujoco.mj_resetDataKeyframe(model, data, model.key(keyframe).id)
    mujoco.mj_forward(model, data)

    # The lowest drawn point of every body: feet are those within a band of the lowest.
    clouds = {b: geo.body_points(model, data, b) for b in range(1, model.nbody)}
    clouds = {b: p for b, p in clouds.items() if len(p)}
    if not clouds:
        raise ImportProblem("The model draws nothing: it has no visible geoms.")
    floor = min(float(p[:, 2].min()) for p in clouds.values())
    low = [b for b, p in clouds.items() if float(p[:, 2].min()) < floor + FOOT_BAND]
    low_links = {geo.link_of(model, b) for b in low}
    if base in low_links or not free and len(low_links) == 1:
        raise ImportProblem(
            "This looks like a fixed-base robot: its base is its lowest part. "
            "Isla Studio trains legged robots that stand on their own feet."
        )

    if len(low_links) != 2:
        raise ImportProblem(
            f"Found {len(low_links)} feet ({', '.join(_body_name(model, f) for f in low_links)}). "
            "Isla Studio trains robots on two feet; set feet in robot.json for others."
        )
    feet = {}  # Foot link: (sole body, middle of its low parts).
    for link in low_links:
        members = [b for b in low if geo.link_of(model, b) == link]
        sole = min(members, key=lambda b: float(clouds[b][:, 2].min()))
        feet[link] = (sole, np.concatenate([clouds[b] for b in members]).mean(axis=0))
    rotation = data.xmat[base].reshape(3, 3)
    axes = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0)}

    def nearest(vector: np.ndarray) -> str:
        local = rotation.T @ vector
        return max(axes, key=lambda k: float(np.dot(axes[k], local)))

    # Facing. Feet named left and right say which way is left, and so which way is
    # forward. Otherwise: from each ankle, the middle of the sole points to the toes.
    named = {_side(_body_name(model, link)): link for link in feet}
    if set(named) == {"left", "right"}:
        left = nearest(feet[named["left"]][1] - feet[named["right"]][1])
        forward = next(k for k, v in axes.items() if np.allclose(v, np.cross(axes[left], [0, 0, 1])))
        sides = {named["left"]: "left", named["right"]: "right"}
    else:
        pointing = np.zeros(3)
        for link, (_, middle) in feet.items():
            joint = int(model.body_jntadr[link]) if model.body_jntnum[link] else -1
            pointing += middle - (data.xanchor[joint] if joint >= 0 else data.xpos[link])
        pointing[2] = 0
        forward = nearest(pointing) if np.linalg.norm(pointing) > 1e-4 else "+x"
        left = next(k for k, v in axes.items() if np.allclose(v, np.cross([0, 0, 1], axes[forward])))
        across = {link: float(np.dot(middle, rotation @ np.array(axes[left], float))) for link, (_, middle) in feet.items()}
        sides = {link: "left" if across[link] > min(across.values()) else "right" for link in feet}

    def site_named(*words: str) -> str | None:
        for s in range(model.nsite):
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, s) or ""
            if any(w in name.lower() for w in words):
                return name
        return None

    # The torso: a body named so, else the body of a site named so ("torso_imu").
    torso = next(
        (_body_name(model, b) for b in range(1, model.nbody) if any(k in _body_name(model, b).lower() for k in ("torso", "chest", "trunk"))),
        None,
    )
    if torso is None and (site := site_named("torso", "chest", "trunk")) is not None:
        torso = _body_name(model, int(model.site_bodyid[model.site(site).id]))
    return {
        "base": _body_name(model, base),
        "torso": torso,
        "imu_site": site_named("imu"),
        "keyframe": keyframe,
        "frame": {"forward": forward, "left": left},
        "feet": [
            {"side": sides[link], "body": _body_name(model, link), "sole": _body_name(model, sole)}
            for link, (sole, _) in sorted(feet.items(), key=lambda item: sides[item[0]] != "left")
        ],
    }


def _motors(model: mujoco.MjModel) -> tuple[dict, list[dict]]:
    """Actuator types (one per torque limit and gains) and the motors that use them."""
    types: dict[str, dict] = {}
    motors = []
    nodes: dict[str, int] = {}
    for a in range(model.nu):
        if int(model.actuator_trntype[a]) != mujoco.mjtTrn.mjTRN_JOINT.value:
            continue
        joint = int(model.actuator_trnid[a, 0])
        if int(model.jnt_type[joint]) not in (mujoco.mjtJoint.mjJNT_HINGE.value, mujoco.mjtJoint.mjJNT_SLIDE.value):
            continue
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
        gear = abs(float(model.actuator_gear[a, 0])) or 1.0
        servo = int(model.actuator_biastype[a]) == mujoco.mjtBias.mjBIAS_AFFINE.value and model.actuator_gainprm[a, 0] > 0
        if model.actuator_forcelimited[a]:
            peak = float(np.abs(model.actuator_forcerange[a]).max())
        elif not servo and model.actuator_ctrllimited[a]:
            peak = float(np.abs(model.actuator_ctrlrange[a]).max()) * gear
        elif model.jnt_actfrclimited[joint]:
            peak = float(np.abs(model.jnt_actfrcrange[joint]).max())
        else:
            peak = 20.0
        if servo:
            kp, kd = float(model.actuator_gainprm[a, 0]), max(0.0, float(-model.actuator_biasprm[a, 2]))
        else:  # A torque motor: the gains a servo of this torque would hold the robot with.
            kp, kd = max(10.0, 1.2 * peak), max(0.3, 1.2 * peak / 30.0)
        key = f"t{peak:g}_kp{kp:g}".replace(".", "p")
        if key not in types:
            types[key] = {
                "label": f"{peak:g} N·m, kp {kp:g}",
                "peak_torque": round(peak, 3),
                "kp": round(kp, 4),
                "kd": round(kd, 4),
                "protocol": "mit",
                "driver": "",
                "assumed": [] if servo else ["kp", "kd"],
                "note": "Read from the model on import."
                + ("" if servo else " Torque motors in the model: servo gains estimated from the torque limit."),
            }
        group = limb(name)
        bus = BUSES.get(group, "can4")
        nodes[bus] = nodes.get(bus, 0) + 1
        motors.append({"joint": name, "type": key, "bus": bus, "node": nodes[bus], "direction": 1, "zero": 0.0, "group": group})
    if not motors:
        raise ImportProblem("No motor drives a joint. Add actuators to the model (MJCF <position> or <motor>).")
    return types, motors


def import_robot(source: str | Path, robot_id: str | None = None, name: str | None = None, about: str = "") -> RobotProfile:
    """Copy a model into ``robots/<id>/`` and write its detected profile."""
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise ImportProblem(f"No model at {source}")
    if source.suffix.lower() not in (".xml", ".mjcf", ".urdf"):
        raise ImportProblem("Choose an MJCF (.xml) or URDF (.urdf) model.")
    try:
        spec = _load_spec(source)
    except Exception as error:  # MuJoCo says what it cannot read.  # noqa: BLE001
        raise ImportProblem(f"MuJoCo cannot read the model: {error}") from None
    model_name = name or pretty(spec.modelname or source.stem)
    robot_id = slug(robot_id or model_name)
    target = paths.ROBOTS / robot_id
    if target.exists():
        raise ImportProblem(f"The hangar already holds a robot called {robot_id}. Choose another name.")
    paths.ROBOTS.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".import-{robot_id}-", dir=paths.ROBOTS))
    try:
        folder = staging / "source"
        folder.mkdir()
        _copy_assets(spec, source, folder)
        added = _add_motors(spec)
        (folder / "model.xml").write_text(spec.to_xml(), encoding="utf-8", newline="\n")
        try:
            model = mujoco.MjSpec.from_file(str(folder / "model.xml")).compile()
        except Exception as error:  # noqa: BLE001
            raise ImportProblem(f"MuJoCo cannot compile the model: {error}") from None
        detected = _detect(model)
        types, motors = _motors(model)
        profile = {
            "format": FORMAT,
            "version": 1,
            "id": robot_id,
            "name": model_name,
            "about": about or f"Imported from {source.name}." + (f" A motor was added on each of its {added} joints." if added else ""),
            "source": "source/model.xml",
            "imported_from": str(source),
            "frame": detected["frame"],
            "base": detected["base"],
            "torso": detected["torso"],
            "imu_site": detected["imu_site"],
            "mass": {},
            "feet": detected["feet"],
            "actuator_types": types,
            "motors": motors,
            "operating_ranges": {},
            "stand": {"keyframe": detected["keyframe"]} if detected["keyframe"] else {},
            "struts": {"items": []},
            "rods": {"items": []},
            "design": {},
        }
        (staging / "robot.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        RobotProfile(staging, profile).validate()
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return RobotProfile.load(target)
