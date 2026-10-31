"""Small robot models written on the fly, so tests need no files from elsewhere."""

from __future__ import annotations

from pathlib import Path


def _box_link(name: str, mass: float, size: tuple[float, float, float], xyz: tuple[float, float, float]) -> str:
    sx, sy, sz = size
    x, y, z = xyz
    inertia = mass / 12.0
    return f"""
  <link name="{name}">
    <inertial><origin xyz="{x} {y} {z}"/><mass value="{mass}"/>
      <inertia ixx="{inertia * (sy * sy + sz * sz):.6f}" iyy="{inertia * (sx * sx + sz * sz):.6f}" izz="{inertia * (sx * sx + sy * sy):.6f}" ixy="0" ixz="0" iyz="0"/></inertial>
    <visual><origin xyz="{x} {y} {z}"/><geometry><box size="{sx} {sy} {sz}"/></geometry></visual>
    <collision><origin xyz="{x} {y} {z}"/><geometry><box size="{sx} {sy} {sz}"/></geometry></collision>
  </link>"""


def _joint(name: str, parent: str, child: str, xyz: tuple[float, float, float], effort: float) -> str:
    x, y, z = xyz
    return f"""
  <joint name="{name}" type="revolute">
    <parent link="{parent}"/><child link="{child}"/><origin xyz="{x} {y} {z}"/><axis xyz="0 1 0"/>
    <limit lower="-1.5" upper="1.5" effort="{effort}" velocity="10"/>
  </joint>"""


def biped_urdf(folder: Path, named_sides: bool = True) -> Path:
    """A 10 kg biped facing +x: pelvis, and on each side a hip, knee and ankle with a long foot."""
    parts = [_box_link("pelvis", 5.0, (0.2, 0.3, 0.1), (0, 0, 0))]
    for side, y in (("left", 0.1), ("right", -0.1)):
        tag = side if named_sides else ("a" if side == "left" else "b")
        parts += [
            _joint(f"{tag}_hip_pitch", "pelvis", f"{tag}_thigh", (0, y, -0.05), 40),
            _box_link(f"{tag}_thigh", 1.5, (0.06, 0.06, 0.3), (0, 0, -0.15)),
            _joint(f"{tag}_knee", f"{tag}_thigh", f"{tag}_shin", (0, 0, -0.3), 40),
            _box_link(f"{tag}_shin", 1.0, (0.05, 0.05, 0.3), (0, 0, -0.15)),
            _joint(f"{tag}_ankle", f"{tag}_shin", f"{tag}_foot", (0, 0, -0.3), 20),
            _box_link(f"{tag}_foot", 0.5, (0.2, 0.08, 0.03), (0.045, 0, -0.015)),
        ]
    path = folder / "biped.urdf"
    path.write_text('<?xml version="1.0"?>\n<robot name="test_biped">' + "".join(parts) + "\n</robot>\n", encoding="utf-8")
    return path


def arm_urdf(folder: Path) -> Path:
    """A fixed-base arm: its base is its lowest part."""
    parts = [
        _box_link("base_link", 3.0, (0.2, 0.2, 0.1), (0, 0, 0.05)),
        _joint("shoulder", "base_link", "upper_arm", (0, 0, 0.1), 30),
        _box_link("upper_arm", 1.0, (0.05, 0.05, 0.4), (0, 0, 0.2)),
        _joint("elbow", "upper_arm", "forearm", (0, 0, 0.4), 20),
        _box_link("forearm", 0.6, (0.04, 0.04, 0.3), (0, 0, 0.15)),
    ]
    path = folder / "arm.urdf"
    path.write_text('<?xml version="1.0"?>\n<robot name="test_arm">' + "".join(parts) + "\n</robot>\n", encoding="utf-8")
    return path


def stl_box(path: Path, sx: float, sy: float, sz: float) -> None:
    """A binary STL box centred on the origin."""
    import struct

    corners = [(x * sx / 2, y * sy / 2, z * sz / 2) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    data = bytearray(80) + struct.pack("<I", len(faces))
    for face in faces:
        data += struct.pack("<3f", 0, 0, 0)
        for i in face:
            data += struct.pack("<3f", *corners[i])
        data += struct.pack("<H", 0)
    path.write_bytes(bytes(data))


def ros_package(folder: Path) -> Path:
    """A ROS-style package: urdf/biped.urdf whose feet are package:// meshes in meshes/."""
    package = folder / "my_robot_description"
    (package / "urdf").mkdir(parents=True)
    (package / "meshes").mkdir()
    stl_box(package / "meshes" / "foot.stl", 0.2, 0.08, 0.03)
    urdf = biped_urdf(package / "urdf")
    box = '<geometry><box size="0.2 0.08 0.03"/></geometry></visual>'
    mesh = '<geometry><mesh filename="package://my_robot_description/meshes/foot.stl"/></geometry></visual>'
    urdf.write_text(urdf.read_text(encoding="utf-8").replace(box, mesh), encoding="utf-8")
    return urdf


def mjcf_biped(folder: Path) -> Path:
    """An MJCF biped facing -y (its left is +x), with mesh feet, servo actuators and a standing keyframe."""
    (folder / "meshes").mkdir(parents=True, exist_ok=True)
    stl_box(folder / "meshes" / "foot.stl", 0.08, 0.2, 0.03)
    legs, actuators = [], []
    for side, x in (("left", 0.1), ("right", -0.1)):
        legs.append(f"""
      <body name="{side}_thigh" pos="{x} 0 -0.05">
        <joint name="{side}_hip_pitch" axis="1 0 0" range="-1.5 1.5"/>
        <geom type="box" size="0.03 0.03 0.15" pos="0 0 -0.15" mass="1.5" contype="0" conaffinity="0" group="2"/>
        <body name="{side}_shin" pos="0 0 -0.3">
          <joint name="{side}_knee" axis="1 0 0" range="-1.5 1.5"/>
          <geom type="box" size="0.025 0.025 0.15" pos="0 0 -0.15" mass="1.0" contype="0" conaffinity="0" group="2"/>
          <body name="{side}_foot" pos="0 0 -0.3">
            <joint name="{side}_ankle" axis="1 0 0" range="-1 1"/>
            <geom type="mesh" mesh="foot" pos="0 -0.045 -0.015" mass="0.5" contype="0" conaffinity="0" group="2"/>
          </body>
        </body>
      </body>""")
        for joint, kp in (("hip_pitch", 60), ("knee", 80), ("ankle", 30)):
            actuators.append(f'<position name="{side}_{joint}_servo" joint="{side}_{joint}" kp="{kp}" kv="{kp / 10:g}" forcerange="-40 40"/>')
    path = folder / "robot.xml"
    path.write_text(f"""<mujoco model="test_mjcf_biped">
  <compiler angle="radian" meshdir="meshes"/>
  <asset><mesh name="foot" file="foot.stl"/></asset>
  <worldbody>
    <geom name="ground" type="plane" size="5 5 0.1"/>
    <body name="pelvis" pos="0 0 0.68">
      <freejoint name="root"/>
      <geom type="box" size="0.15 0.1 0.05" mass="5" contype="0" conaffinity="0" group="2"/>
      <site name="torso_imu" pos="0 0 0.02"/>{"".join(legs)}
    </body>
  </worldbody>
  <actuator>
    {chr(10).join("    " + a for a in actuators).strip()}
  </actuator>
  <keyframe><key name="home_stand" qpos="0 0 0.68 1 0 0 0 0 0 0 0 0 0"/></keyframe>
</mujoco>
""", encoding="utf-8")
    return path
