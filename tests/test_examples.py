"""The example runs that come with Studio: offered once, never over a run of your own, and usable."""

from __future__ import annotations

import json
import shutil

import pytest

from isla_rl import examples, paths
from isla_rl.learn.trainer import Run

SHIPPED = sorted(p.name for p in (paths.ROOT / "examples" / "runs").iterdir() if p.is_dir())


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    """An examples folder and an empty runs folder of the test's own."""
    monkeypatch.setattr(paths, "EXAMPLES", tmp_path / "examples")
    monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    return tmp_path


def _example(shelf, name: str, trained: float = 1_790_000_000.0) -> None:
    folder = shelf / "examples" / "runs" / name
    folder.mkdir(parents=True)
    (folder / "setup.json").write_text("{}", encoding="utf-8")
    (folder / "status.json").write_text(json.dumps({"state": "finished", "updated": trained}), encoding="utf-8")


def test_an_example_is_offered_once(shelf):
    _example(shelf, "demo-walk")
    assert examples.install() == ["demo-walk"]
    copied = paths.RUNS / "demo-walk"
    assert (copied / "setup.json").exists()
    assert (copied / "setup.json").stat().st_mtime == pytest.approx(1_790_000_000.0)  # Dated when it was trained.
    shutil.rmtree(copied)  # Deleted: it stays deleted.
    assert examples.install() == []
    assert not copied.exists()


def test_a_run_of_your_own_is_never_replaced(shelf):
    _example(shelf, "mine")
    own = paths.RUNS / "mine"
    own.mkdir()
    (own / "setup.json").write_text('{"own": true}', encoding="utf-8")
    assert examples.install() == []
    assert (own / "setup.json").read_text(encoding="utf-8") == '{"own": true}'


def test_no_examples_folder_is_fine(shelf):
    assert examples.install() == []


@pytest.fixture
def bundled_robots(tmp_path, monkeypatch):
    source = tmp_path / "bundle" / "robots"
    robot = source / "luma_1"
    robot.mkdir(parents=True)
    (robot / "robot.json").write_text('{"bundled": true}', encoding="utf-8")
    (robot / "source").mkdir()
    (robot / "source" / "model.xml").write_text("<mujoco/>", encoding="utf-8")
    monkeypatch.setattr(paths, "BUNDLED_ROBOTS", source)
    monkeypatch.setattr(paths, "ROBOTS", tmp_path / "documents" / "robots")
    return source


def test_bundled_robots_are_copied_once_and_deletions_are_kept(bundled_robots):
    assert examples.install_robots() == ["luma_1"]
    robot = paths.ROBOTS / "luma_1"
    assert (robot / "source" / "model.xml").read_text(encoding="utf-8") == "<mujoco/>"
    assert (robot / "robot.json").read_bytes() == (bundled_robots / "luma_1" / "robot.json").read_bytes()
    shutil.rmtree(robot)
    assert examples.install_robots() == []
    assert not robot.exists()
    assert not (bundled_robots / examples.ROBOTS_RECORD).exists()  # The installation stays read-only.


def test_a_bundled_robot_never_replaces_a_users_robot(bundled_robots):
    robot = paths.ROBOTS / "luma_1"
    robot.mkdir(parents=True)
    own = b'{"own": true}'
    (robot / "robot.json").write_bytes(own)
    assert examples.install_robots() == []
    assert (robot / "robot.json").read_bytes() == own
    shutil.rmtree(robot)
    assert examples.install_robots() == []  # A name already in use was offered too.
    assert not robot.exists()


def test_a_checkout_does_not_copy_robots_over_itself(bundled_robots, monkeypatch):
    monkeypatch.setattr(paths, "ROBOTS", bundled_robots)
    assert examples.install_robots() == []
    assert not (bundled_robots / examples.ROBOTS_RECORD).exists()


def test_an_interrupted_robot_copy_is_cleaned_up_and_retried(bundled_robots, monkeypatch):
    def interrupted(source, target, **kwargs):
        (target / "partial").write_bytes(b"incomplete")
        raise OSError("copy interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copytree", interrupted)
        assert examples.install_robots() == []
    assert not (paths.ROBOTS / "luma_1").exists()
    assert examples.install_robots() == ["luma_1"]
    assert (paths.ROBOTS / "luma_1" / "robot.json").is_file()


@pytest.mark.parametrize("name", SHIPPED)
def test_the_shipped_examples_work(name):
    run = Run(paths.ROOT / "examples" / "runs" / name)
    policy = run.policy()
    assert policy is not None and policy.name == name and policy.robot == "luma_1"
    assert run.status()["state"] == "finished" and "pid" not in run.status()
    profile = run.profile()
    assert profile.folder == paths.ROBOTS / "luma_1"  # The hangar's copy, not a path from the computer it trained on.
    assert run.setup.robot == "luma_1"


def test_luma_comes_with_a_tested_walking_example():
    assert {"luma1-walk", "luma1-leg-design"} <= set(SHIPPED)
    evaluation = json.loads((paths.ROOT / "examples" / "runs" / "luma1-walk" / "evaluation.json").read_text(encoding="utf-8"))
    assert evaluation["policy"] == "luma1-walk" and evaluation["walking"]["roam"]["falls"] == 0
