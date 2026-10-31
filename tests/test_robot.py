"""Luma-1 as the platform builds it: mass, actuators, shock absorbers, rods, standing."""

from __future__ import annotations

import math

import mujoco
import numpy as np
import pytest

from isla_rl.robot import analysis
from isla_rl.robot.build import STAND_KEY, build, measure
from isla_rl.robot.profile import RobotProfile


@pytest.fixture(scope="module")
def luma():
    return RobotProfile.load("luma_1")


def test_old_name_still_loads(luma):
    assert RobotProfile.load("isla_v1").id == luma.id == "luma_1"
    assert luma.name == "Luma-1"


def test_actuators_follow_the_datasheet(luma):
    macro, micro = luma.actuator_types["macro"], luma.actuator_types["micro"]
    assert macro["gear_ratio"] == 36
    assert micro["gear_ratio"] == pytest.approx(7.385**2, abs=1e-3)
    # Continuous torque is the limit: torque constant at the output times continuous current.
    assert macro["peak_torque"] == pytest.approx(macro["torque_constant"] * macro["continuous_current"])
    assert micro["peak_torque"] == pytest.approx(micro["torque_constant"] * micro["continuous_current"], abs=0.01)
    assert (macro["peak_torque"], micro["peak_torque"]) == (66.0, 23.39)


def test_adopted_design_is_the_co_design_result(luma):
    design = luma.default_design()
    assert (design["thigh_rod"], design["shin_rod"]) == (42.0, 46.0)
    assert build(luma, purpose="train").info.height == pytest.approx(1.034, abs=0.005)


def test_display_model(luma):
    cad_design = {key: param.default for key, param in luma.design.items()}
    built = build(luma, cad_design, purpose="display")
    info = built.info
    assert info.total_mass == pytest.approx(27.0, abs=1e-3)  # 27 kg as built, with the CAD's rods.
    assert info.virtual_mass == pytest.approx(27.0 - info.cad_mass, abs=1e-3)
    assert len(info.motors) == 19 and len(info.struts) == 4 and len(info.rods) == 4
    # Longer carbon rods weigh more: the adopted legs add their tube, the virtual mass stays.
    adopted = build(luma, purpose="display").info
    assert adopted.virtual_mass == pytest.approx(info.virtual_mass)
    assert 0.02 < adopted.total_mass - info.total_mass < 0.1
    assert 0.85 < info.height < 1.05
    for motor in info.motors:
        assert built.model.actuator_forcerange[built.model.actuator(motor.actuator).id][1] == motor.peak_torque


def test_feet_stand_on_the_floor(luma):
    built = build(luma, purpose="train")
    model, data = built.model, mujoco.MjData(built.model)
    mujoco.mj_resetDataKeyframe(model, data, model.key(STAND_KEY).id)
    mujoco.mj_kinematics(model, data)
    for foot in built.info.feet:
        g = model.geom(foot.geom).id
        rotation = data.geom_xmat[g].reshape(3, 3)
        bottom = data.geom_xpos[g][2] - np.abs(rotation[2]) @ model.geom_size[g]
        assert abs(bottom) < 1e-3


def test_longer_rods_make_a_taller_robot(luma):
    short = build(luma, {"thigh_rod": 0.0, "shin_rod": 0.0}, purpose="train").info
    tall = build(luma, {"thigh_rod": 40.0, "shin_rod": 40.0}, purpose="train").info
    assert tall.hip_height - short.hip_height == pytest.approx(0.08, abs=0.01)


def test_shock_absorbers_follow_the_design(luma):
    built = build(luma, {"front_k": 20.0, "back_c": 300.0}, purpose="train")
    model = built.model
    for strut in built.info.struts:
        tendon = model.tendon(strut.tendon).id
        if strut.position == "front":
            assert model.tendon_stiffness[tendon] == pytest.approx(20_000.0)
        else:
            assert model.tendon_damping[tendon] == pytest.approx(300.0)
        low, high = model.tendon_range[tendon]
        assert high - low == pytest.approx(luma.strut_stroke)


def test_standing_unaided_is_settled_by_simulation(luma):
    """The ankles' stiffness ratio is not enough: soft springs tip over, stiff ones stand."""
    train = build(luma, purpose="train")
    cad = measure(luma)
    cad_design = {key: param.default for key, param in luma.design.items()}  # Rods as in the CAD.
    soft = analysis.analyse(luma, train, cad, luma.complete_design({**cad_design, "front_k": 12, "back_k": 12}))
    stiff = analysis.analyse(luma, train, cad, luma.complete_design({**cad_design, "front_k": 40, "back_k": 40}))
    assert soft["ankle"]["passive_ratio"] > 1 and not soft["standing"]["unaided"]
    assert stiff["standing"]["unaided"] and stiff["standing"]["drift_deg"] < 5
    assert math.isfinite(soft["size"]["height"])
