"""Practice robots and training on the processor."""

from __future__ import annotations

import math

import numpy as np
import pytest

from isla_rl.learn import evaluate as evaluation
from isla_rl.learn.env import PracticeRobots, envelope
from isla_rl.learn.ppo import PPO, PPOSettings, Rollout
from isla_rl.learn.skills import TrainingSetup
from isla_rl.learn.trainer import create_run, train
from isla_rl.robot import importer
from isla_rl.robot.profile import RobotProfile
from isla_rl.sim.session import LiveSession

from .models import biped_urdf


@pytest.fixture
def small_biped(hangar, tmp_path):
    """A quick-to-simulate robot for runs that only need to show training works end to end."""
    return importer.import_robot(biped_urdf(tmp_path), robot_id="biped")


@pytest.mark.parametrize("skill", ["stand", "walk", "move", "jump"])
def test_luma_practice_robots_step(skill):
    robots = PracticeRobots(RobotProfile.load("luma_1"), TrainingSetup(robot="luma_1", skill=skill, robots=8), "cpu", threads=2)
    try:
        obs, critic = robots.reset()
        assert obs.shape == (8, robots.obs_size) and critic.shape == (8, robots.critic_size)
        for _ in range(5):
            out = robots.step(np.zeros((8, len(robots.joints))))
            assert all(np.isfinite(np.asarray(part, dtype=float)).all() for part in out[:3])
    finally:
        robots.close()


def test_a_short_run_learns_and_saves_a_policy(small_biped):
    run = create_run("biped-smoke", TrainingSetup(robot=small_biped.id, skill="stand", robots=16, steps=16 * 24 * 2), small_biped)
    messages: list[dict] = []
    train(run, report=messages.append, device="cpu", swarm=False)
    assert not [m for m in messages if m["type"] == "error"]
    assert any(m["type"] == "finished" for m in messages)
    policy = run.policy()
    assert policy is not None and policy.robot == "biped"


def test_gantry_holds_a_robot_that_cannot_stand():
    session = LiveSession(RobotProfile.load("luma_1"))
    assert not session.stands_alone and session.gantry  # Passive ankles: it needs a policy or the gantry.
    for _ in range(1000):
        session._step()
    upright = session.frame()["tilt"]
    session.set_gantry(False)
    for _ in range(1500):
        session._step()
    assert session.frame()["tilt"] > upright + 15.0  # Let go, it tips over.
    session.reset()
    assert session.gantry is False and math.isfinite(session.frame()["tilt"])


def _untrained(skill: str):
    """A Luma-1 policy for ``skill`` with its first, random weights."""
    robots = PracticeRobots(RobotProfile.load("luma_1"), TrainingSetup(robot="luma_1", skill=skill, robots=2), "cpu", threads=1)
    try:
        robots.reset()
        return robots.policy(PPO(robots.obs_size, robots.critic_size, len(robots.joints), seed=0), skill)
    finally:
        robots.close()


def test_motors_brake_past_their_no_load_speed():
    peak, corner, no_load = np.array([66.0]), np.array([4.9]), np.array([5.8])
    low, high = envelope(peak, corner, no_load, np.array([3.0]), 1.0)
    assert (low[0], high[0]) == (-66.0, 66.0)  # Below the corner: full torque either way.
    low, high = envelope(peak, corner, no_load, np.array([5.35]), 1.0)
    assert high[0] == pytest.approx(33.0) and low[0] == -66.0  # Half way to no-load: half the drive.
    low, high = envelope(peak, corner, no_load, np.array([6.25]), 1.0)
    assert high[0] == pytest.approx(-33.0) and low[0] == -66.0  # Past no-load it brakes, whatever it is asked.
    low, high = envelope(peak, corner, no_load, np.array([-9.0]), 1.0)
    assert (low[0], high[0]) == pytest.approx((66.0, 66.0))  # Far past, turning the other way: full braking.


def test_exploration_noise_has_a_ceiling():
    """With rewards that say nothing, the entropy bonus alone must not blow the action noise up."""
    ppo = PPO(4, 4, 2, PPOSettings(hidden=(8,), learning_rate=1e-2, init_std=0.95, max_std=1.0), seed=0)
    rollout = Rollout(4, 16, 4, 4, 2)
    rng = np.random.default_rng(0)
    for step in range(4):
        rollout.obs[step] = rollout.critic_obs[step] = rng.normal(size=(16, 4))
        rollout.actions[step], rollout.log_probs[step], rollout.means[step] = ppo.act(rollout.obs[step])
    rollout.step = 4
    for _ in range(3):
        ppo.update(rollout)
    assert float(ppo.std.max()) <= 1.0 + 1e-6


def test_evaluating_a_jump_policy(monkeypatch):
    for name, value in {"SETTLE": 0.3, "STAND_TIME": 0.3, "JUMPS": 1, "JUMP_SHOVES": (0.3,), "JUMP_DIRECTIONS": 1}.items():
        monkeypatch.setattr(evaluation, name, value)
    report = evaluation.evaluate(_untrained("jump"), RobotProfile.load("luma_1"))
    assert len(report["jumping"]["jumps"]) == 1 and len(report["shocks"]) == 4
    phases = report["phases"]
    assert set(phases) <= {"standing", "jumping", "shoves"} and all(p["trials"] >= p["falls"] for p in phases.values())
    assert {m["type"] for m in report["motors"]} == {"macro", "micro"}
    shock = report["shocks"][0]
    assert shock["stroke_mm"] == pytest.approx(24.0) and shock["preload_force"] > 0
    assert "Shock absorbers, standing" in evaluation.describe(report)


def test_watching_a_jump_policy():
    profile = RobotProfile.load("luma_1")
    policy = _untrained("jump")
    session = LiveSession(profile)
    with pytest.raises(ValueError, match="watch a jump policy"):
        session.jump()  # Nothing that jumps is running yet.
    session.set_policy(policy, "jump")
    session.set_mode("policy")
    frame = session.frame()
    assert frame["can_jump"] and not frame["steers"] and not frame["jumping"]
    session.jump()
    with pytest.raises(ValueError, match="Already jumping"):
        session.jump()
    for _ in range(100):
        session._step()
    assert session.frame()["jumping"]
    for _ in range(500):  # The jump clock runs out after the policy's jump time, 1 s.
        session._step()
    assert not session.frame()["jumping"] and math.isfinite(session.frame()["tilt"])
