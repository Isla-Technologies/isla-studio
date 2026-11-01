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
