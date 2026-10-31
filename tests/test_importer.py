"""Bringing robots into the hangar: MJCF with meshes, URDF without actuators, and refusals."""

from __future__ import annotations

import pytest

from isla_rl.robot import importer
from isla_rl.robot.build import build
from isla_rl.robot.card import card
from isla_rl.robot.profile import RobotProfile

from .models import arm_urdf, biped_urdf, mjcf_biped, ros_package


def test_urdf_biped(hangar, tmp_path):
    profile = importer.import_robot(biped_urdf(tmp_path), name="Test Biped")
    data = profile.data
    assert profile.id == "test_biped" and (hangar / "test_biped" / "robot.json").exists()
    assert data["frame"] == {"forward": "+x", "left": "+y"}
    assert {f["side"]: f["body"] for f in data["feet"]} == {"left": "left_foot", "right": "right_foot"}
    assert len(data["motors"]) == 6
    assert {t["peak_torque"] for t in data["actuator_types"].values()} == {40.0, 20.0}  # The URDF's effort limits.
    assert {m["group"] for m in data["motors"]} == {"left_leg", "right_leg"}
    built = build(profile)
    assert built.model.nq == 7 + 6
    assert built.info.total_mass == pytest.approx(11.0)
    assert built.info.height == pytest.approx(0.73, abs=0.01)


def test_ros_package_meshes_are_found_and_copied(hangar, tmp_path):
    source = ros_package(tmp_path)
    profile = importer.import_robot(source, robot_id="ros_biped")
    assert [p.name for p in (hangar / "ros_biped" / "source" / "meshes").iterdir()] == ["foot.stl"]
    assert not [p for p in source.parent.iterdir() if p.name != "biped.urdf"]  # Nothing written beside the source.
    assert build(profile).info.total_mass == pytest.approx(11.0)


def test_feet_without_side_names_face_their_toes(hangar, tmp_path):
    profile = importer.import_robot(biped_urdf(tmp_path, named_sides=False), robot_id="unnamed")
    assert profile.data["frame"]["forward"] == "+x"
    assert {f["side"]: f["body"] for f in profile.data["feet"]} == {"left": "a_foot", "right": "b_foot"}


def test_fixed_base_robots_are_refused(hangar, tmp_path):
    with pytest.raises(importer.ImportProblem, match="fixed-base"):
        importer.import_robot(arm_urdf(tmp_path))
    assert not any(hangar.iterdir())  # Nothing half-imported is left behind.


def test_mjcf_with_meshes_keyframe_and_servos(hangar, tmp_path):
    profile = importer.import_robot(mjcf_biped(tmp_path), robot_id="mjcf_biped")
    data = profile.data
    assert data["frame"] == {"forward": "-y", "left": "+x"}
    assert data["stand"] == {"keyframe": "home_stand"}
    assert data["imu_site"] == "torso_imu" and data["torso"] == "pelvis"
    assert len(data["motors"]) == 6
    assert {(t["peak_torque"], t["kp"]) for t in data["actuator_types"].values()} == {(40.0, 60.0), (40.0, 80.0), (40.0, 30.0)}
    assert [p.name for p in (hangar / "mjcf_biped" / "source" / "meshes").glob("*.stl")] == ["foot.stl"]
    assert build(profile).info.total_mass == pytest.approx(11.0)


def test_names_are_unique(hangar, tmp_path):
    importer.import_robot(biped_urdf(tmp_path), robot_id="twin")
    with pytest.raises(importer.ImportProblem, match="already holds"):
        importer.import_robot(biped_urdf(tmp_path), robot_id="twin")


def test_missing_and_wrong_files(hangar, tmp_path):
    with pytest.raises(importer.ImportProblem, match="No model"):
        importer.import_robot(tmp_path / "nothing.xml")
    text = tmp_path / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    with pytest.raises(importer.ImportProblem, match="MJCF"):
        importer.import_robot(text)


def test_card_blueprint():
    entry = card(RobotProfile.load("luma_1"))
    assert entry["motors"] == len(entry["blueprint"]["motors"]) == 19
    assert len(entry["blueprint"]["parts"]) > 50
    _, low_z, _, high_z = entry["blueprint"]["bounds"]
    assert high_z == pytest.approx(entry["height"], abs=0.02) and low_z == pytest.approx(0.0, abs=0.01)
    assert not entry["stands_alone"]  # Passive ankles: it needs a policy to balance.


@pytest.mark.parametrize("name, expected", [("kinetic_mk1", "Kinetic MK1"), ("my-robot", "My Robot"), ("humanoid", "Humanoid")])
def test_pretty_names(name, expected):
    assert importer.pretty(name) == expected
