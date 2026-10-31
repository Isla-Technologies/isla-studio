"""The CAN protocols and the safety filter between a policy and real motors."""

from __future__ import annotations

import pytest

from isla_rl.hardware import protocols as P
from isla_rl.hardware.base import MotorState
from isla_rl.hardware.config import HardwareSettings, SafetyLimits
from isla_rl.hardware.safety import JointLimits, SafetyFilter
from isla_rl.robot.profile import RobotProfile


def test_mit_round_trip():
    ranges = P.MitRanges(t_max=30.0)
    frame = P.mit_reply_frame(7, 1.234, -3.5, 12.0, ranges)
    node, position, velocity, torque = P.mit_reply(frame, ranges)
    assert node == 7
    assert position == pytest.approx(1.234, abs=2 * ranges.p_max / 65535)
    assert velocity == pytest.approx(-3.5, abs=2 * ranges.v_max / 4095)
    assert torque == pytest.approx(12.0, abs=2 * ranges.t_max / 4095)
    assert len(P.mit_command(0.5, 0.0, 45.0, 1.0, 0.0, ranges)) == 8


def test_mit_values_are_clamped_to_the_drive_ranges():
    ranges = P.MitRanges(t_max=10.0)
    _, _, _, torque = P.mit_reply(P.mit_reply_frame(1, 0.0, 0.0, 99.0, ranges), ranges)
    assert torque == pytest.approx(10.0)


def test_odrive_ids():
    arbitration = P.odrive_id(5, P.OD.SET_INPUT_POS)
    assert P.odrive_split(arbitration) == (5, P.OD.SET_INPUT_POS)


def test_luma_drive_ranges_cover_its_torque():
    """The default MIT torque range must reach each actuator's limit (23.39 N·m micro)."""
    profile = RobotProfile.load("luma_1")
    settings = HardwareSettings.defaults(profile.id, profile)
    for key, kind in profile.actuator_types.items():
        assert settings.protocols[key].t_max >= kind["peak_torque"]
        assert settings.protocols[key].gear_ratio == kind["gear_ratio"]


def make_filter(**changes) -> SafetyFilter:
    limits = SafetyLimits(**{"max_speed": 1.0, "soft_start": 2.0, "stiffness_fraction": 0.5, "torque_fraction": 0.5, **changes})
    return SafetyFilter({"knee": JointLimits(low=-1.0, high=1.0, peak_torque=66.0, kp=160.0, kd=5.0)}, limits)


def test_targets_stay_inside_the_range_and_move_slowly():
    f = make_filter()
    f.start({"knee": 0.0})
    command = f.commands({"knee": 5.0}, dt=0.1, since_armed=10.0)["knee"]
    assert command.position == pytest.approx(0.1)  # 1 rad/s for 0.1 s.
    for _ in range(100):
        command = f.commands({"knee": 5.0}, dt=0.1, since_armed=10.0)["knee"]
    assert command.position == pytest.approx(1.0 - f.limits.limit_margin)
    assert command.torque_limit == pytest.approx(33.0)


def test_soft_start_ramps_stiffness():
    f = make_filter()
    f.start({"knee": 0.0})
    assert f.commands({}, dt=0.01, since_armed=0.0)["knee"].kp == pytest.approx(0.0)
    assert f.commands({}, dt=0.01, since_armed=1.0)["knee"].kp == pytest.approx(160.0 * 0.5 * 0.5)
    assert f.commands({}, dt=0.01, since_armed=5.0)["knee"].kp == pytest.approx(80.0)


@pytest.mark.parametrize(
    "state, words",
    [
        (None, "not answering"),
        (MotorState(0.0, age=0.5), "no data"),
        (MotorState(0.0, fault="overcurrent"), "overcurrent"),
        (MotorState(0.0, temperature=95.0), "over the"),
        (MotorState(0.9), "from its target"),
    ],
)
def test_reasons_to_stop(state, words):
    f = make_filter(max_tracking_error=0.5)
    f.start({"knee": 0.0})
    states = {} if state is None else {"knee": state}
    assert words in (f.check(states, armed=True) or "")


def test_healthy_robot_keeps_going():
    f = make_filter()
    f.start({"knee": 0.0})
    assert f.check({"knee": MotorState(0.01, temperature=40.0)}, armed=True) is None
