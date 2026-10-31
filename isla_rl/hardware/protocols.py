"""CAN frame formats the platform speaks to motor drives.

MIT (the Mini Cheetah protocol, also Isla JD-1 with ``can.protocol = mit``):
one 8-byte frame per command carrying position, velocity, stiffness, damping and
feed-forward torque as fixed-point numbers over ranges the drive is configured
with; the drive answers with position, velocity and torque in 6 bytes.

ODrive CANSimple (MKS ODrive S / XDrive, ODrive 0.5.x and 0.6, Isla JD-1 with
``can.protocol = odrive``): the 11-bit identifier is ``node << 5 | command``;
values are little-endian floats in motor turns, before the gearbox.

The Isla JD-1 native protocol (CAN-FD) is not specified yet; until its
PROTOCOL.md exists, run the drives in MIT or ODrive mode.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

# MIT special frames.
MIT_ENTER = bytes([0xFF] * 7 + [0xFC])
MIT_EXIT = bytes([0xFF] * 7 + [0xFD])
MIT_ZERO = bytes([0xFF] * 7 + [0xFE])


@dataclass(frozen=True)
class MitRanges:
    """The ranges an MIT drive maps its fixed-point fields onto (set in the drive)."""

    p_max: float = 12.5
    v_max: float = 65.0
    kp_max: float = 500.0
    kd_max: float = 5.0
    t_max: float = 18.0


def _to_uint(x: float, low: float, high: float, bits: int) -> int:
    x = min(max(x, low), high)
    return int(round((x - low) * ((1 << bits) - 1) / (high - low)))


def _to_float(value: int, low: float, high: float, bits: int) -> float:
    return value * (high - low) / ((1 << bits) - 1) + low


def mit_command(position: float, velocity: float, kp: float, kd: float, torque: float, r: MitRanges) -> bytes:
    """One MIT command frame (motor side units: rad, rad/s, N*m)."""
    p = _to_uint(position, -r.p_max, r.p_max, 16)
    v = _to_uint(velocity, -r.v_max, r.v_max, 12)
    k = _to_uint(kp, 0.0, r.kp_max, 12)
    d = _to_uint(kd, 0.0, r.kd_max, 12)
    t = _to_uint(torque, -r.t_max, r.t_max, 12)
    return bytes(
        [
            p >> 8, p & 0xFF,
            v >> 4, ((v & 0xF) << 4) | (k >> 8),
            k & 0xFF,
            d >> 4, ((d & 0xF) << 4) | (t >> 8),
            t & 0xFF,
        ]
    )


def mit_reply(data: bytes, r: MitRanges) -> tuple[int, float, float, float]:
    """(node, position, velocity, torque) from an MIT reply frame."""
    if len(data) < 6:
        raise ValueError("An MIT reply has at least 6 bytes")
    node = data[0]
    p = (data[1] << 8) | data[2]
    v = (data[3] << 4) | (data[4] >> 4)
    t = ((data[4] & 0xF) << 8) | data[5]
    return (
        node,
        _to_float(p, -r.p_max, r.p_max, 16),
        _to_float(v, -r.v_max, r.v_max, 12),
        _to_float(t, -r.t_max, r.t_max, 12),
    )


def mit_reply_frame(node: int, position: float, velocity: float, torque: float, r: MitRanges) -> bytes:
    """An MIT reply as a drive sends it (used by the practice robot and tests)."""
    p = _to_uint(position, -r.p_max, r.p_max, 16)
    v = _to_uint(velocity, -r.v_max, r.v_max, 12)
    t = _to_uint(torque, -r.t_max, r.t_max, 12)
    return bytes([node & 0xFF, p >> 8, p & 0xFF, v >> 4, ((v & 0xF) << 4) | (t >> 8), t & 0xFF])


# ODrive CANSimple.

class OD:
    HEARTBEAT = 0x01
    ESTOP = 0x02
    SET_AXIS_STATE = 0x07
    ENCODER_ESTIMATES = 0x09
    SET_CONTROLLER_MODE = 0x0B
    SET_INPUT_POS = 0x0C
    SET_LIMITS = 0x0F
    GET_IQ = 0x14
    GET_TEMPERATURE = 0x15
    CLEAR_ERRORS = 0x18
    SET_POS_GAIN = 0x1A
    SET_VEL_GAINS = 0x1B
    GET_TORQUES = 0x1C

    IDLE = 1
    CLOSED_LOOP = 8
    POSITION_CONTROL = 3
    PASSTHROUGH = 1


def odrive_id(node: int, command: int) -> int:
    return ((node & 0x3F) << 5) | (command & 0x1F)


def odrive_split(arbitration_id: int) -> tuple[int, int]:
    return arbitration_id >> 5, arbitration_id & 0x1F


def odrive_set_input_pos(turns: float, velocity_ff: float = 0.0, torque_ff: float = 0.0) -> bytes:
    """Position (turns), velocity feed-forward (turns/s) and torque feed-forward (N*m)."""
    vel = int(max(-32768, min(32767, round(velocity_ff * 1000))))
    tor = int(max(-32768, min(32767, round(torque_ff * 1000))))
    return struct.pack("<fhh", turns, vel, tor)


def odrive_axis_state(state: int) -> bytes:
    return struct.pack("<I", state)


def odrive_controller_mode(control: int = OD.POSITION_CONTROL, input_mode: int = OD.PASSTHROUGH) -> bytes:
    return struct.pack("<II", control, input_mode)


def odrive_gains(kp_joint: float, kd_joint: float, gear_ratio: float) -> tuple[bytes, bytes]:
    """ODrive's cascaded gains for an impedance (N*m/rad, N*m*s/rad) at the joint.

    ODrive: vel_setpoint = pos_gain * pos_error (turns), torque = vel_gain * vel_error
    (turns/s), at the motor. So the stiffness the motor shows is vel_gain * pos_gain
    per turn; through the gearbox the joint sees N^2 times more.
    """
    n2 = max(gear_ratio, 1e-6) ** 2
    kd_motor_turn = kd_joint / n2 * 2 * math.pi  # N*m per (turn/s)
    kp_motor_turn = kp_joint / n2 * 2 * math.pi  # N*m per turn
    vel_gain = max(kd_motor_turn, 1e-6)
    pos_gain = kp_motor_turn / vel_gain
    return struct.pack("<f", pos_gain), struct.pack("<ff", vel_gain, 0.0)


def odrive_estimates(data: bytes) -> tuple[float, float]:
    """(position turns, velocity turns/s) from an encoder-estimates frame."""
    return struct.unpack("<ff", data[:8])


def odrive_heartbeat(data: bytes) -> tuple[int, int]:
    """(axis error bits, axis state) from a heartbeat."""
    error, state = struct.unpack("<IB", data[:5])
    return error, state


def odrive_temperature(data: bytes) -> tuple[float, float]:
    return struct.unpack("<ff", data[:8])


def odrive_torques(data: bytes) -> tuple[float, float]:
    """(torque target, torque estimate) at the motor, N*m."""
    return struct.unpack("<ff", data[:8])
