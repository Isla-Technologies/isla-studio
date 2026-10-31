"""The web app's API: status, the hangar, switching robots, importing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from isla_rl.learn.skills import TrainingSetup
from isla_rl.learn.trainer import create_run
from isla_rl.robot import importer
from isla_rl.robot.profile import RobotProfile
from isla_rl.server.app import create_app

from .models import biped_urdf, mjcf_biped


@pytest.fixture
def client():
    with TestClient(create_app("luma_1")) as c:
        yield c


@pytest.fixture
def two_robots(hangar, tmp_path):
    """A hangar of two small robots, so switching between them is quick."""
    importer.import_robot(biped_urdf(tmp_path), robot_id="biped_a")
    importer.import_robot(mjcf_biped(tmp_path / "b"), robot_id="biped_b")
    with TestClient(create_app("biped_a")) as c:
        yield c


def test_status(client):
    status = client.get("/api/status").json()
    assert status["robot"] == "luma_1" and status["mujoco"].startswith("3.")


def test_hangar_cards(client):
    cards = client.get("/api/robots").json()
    assert cards[0]["id"] == "luma_1" and cards[0]["active"] and not cards[0]["imported"]
    for c in cards:
        assert c["blueprint"]["parts"] and len(c["blueprint"]["motors"]) == c["motors"]


def test_any_robot_can_be_looked_at(two_robots):
    meta = two_robots.get("/api/robots/biped_b/meta").json()
    assert meta["nq"] == 13 and meta["robot"]["id"] == "biped_b"
    assert len(two_robots.get("/api/robots/biped_b/meshes.bin").content) > 12
    analysis = two_robots.post("/api/robots/biped_b/analysis", json={"design": {}}).json()
    assert "unaided" in analysis["standing"]


def test_switching_robots(two_robots):
    assert two_robots.post("/api/robots/biped_b/activate").json()["robot"] == "biped_b"
    assert two_robots.get("/api/status").json()["robot"] == "biped_b"
    cards = {c["id"]: c for c in two_robots.get("/api/robots").json()}
    assert cards["biped_b"]["active"] and not cards["biped_a"]["active"]
    assert two_robots.post("/api/robots/biped_a/activate").status_code == 200
    assert two_robots.get("/api/robots/nobody/meta").status_code == 404


def test_removing_a_robot(two_robots, hangar):
    answer = two_robots.delete("/api/robots/biped_a")
    assert answer.status_code == 400 and "loaded" in answer.json()["detail"]  # The loaded one stays.
    assert two_robots.delete("/api/robots/biped_b").status_code == 200
    assert not (hangar / "biped_b").exists()


def test_import_problems_are_said_plainly(client, tmp_path):
    answer = client.post("/api/robots/import", json={"path": ""})
    assert answer.status_code == 400 and "path" in answer.json()["detail"]
    answer = client.post("/api/robots/import", json={"path": str(tmp_path / "missing.xml")})
    assert answer.status_code == 400 and "No model" in answer.json()["detail"]
    answer = client.put("/api/robots/upload/abcdefgh12/robot/setup.exe", content=b"x")
    assert answer.status_code == 400


def test_a_policy_is_tested_only_once_it_exists(two_robots):
    assert two_robots.post("/api/runs/nobody/evaluate").status_code == 404
    create_run("untrained", TrainingSetup(robot="biped_a", skill="stand", robots=4, steps=96), RobotProfile.load("biped_a"))
    answer = two_robots.post("/api/runs/untrained/evaluate")
    assert answer.status_code == 400 and "no policy yet" in answer.json()["detail"]
    assert two_robots.get("/api/runs/untrained/evaluation").json() == {"testing": False, "report": None}
    listed = {r["name"]: r for r in two_robots.get("/api/runs").json()}
    assert listed["untrained"]["evaluated"] is False and listed["untrained"]["evaluating"] is False
