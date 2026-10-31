"""The robot's motor drives on CAN buses, through python-can.

python-can reaches the usual adapters: ``slcan`` (CANable and other serial
adapters, any OS), ``gs_usb`` (candleLight), ``pcan`` (PEAK), ``kvaser``, and
``socketcan`` on Linux (the robot's own computer). Each bus gets a thread that
reads every frame and keeps each motor's latest state.

Units at the joint: a motor's position (joint side, after its gearbox and in
radians) is ``direction * joint + zero``; ODrive drives report motor turns, which
the gear ratio converts. Zeroing is done here, not written into the drives.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..robot.profile import RobotProfile
from . import protocols as P
from .base import DriverError, ImuState, MotorCommand, MotorState
from .config import HardwareSettings, ProtocolConfig

FRESH = 0.1  # s: a reading this old still counts as current.


@dataclass
class _Motor:
    joint: str
    bus: str
    node: int
    direction: int
    zero: float
    protocol: ProtocolConfig
    ranges: P.MitRanges
    position: float = 0.0  # Joint side, before direction and zero.
    velocity: float = 0.0
    torque: float = 0.0
    temperature: float | None = None
    fault: str | None = None
    updated: float = 0.0
    gains: tuple[float, float] = (-1.0, -1.0)
    extra: dict = field(default_factory=dict)

    def to_joint(self, motor_position: float) -> float:
        return self.direction * (motor_position - self.zero)

    def to_motor(self, joint_position: float) -> float:
        return self.direction * joint_position + self.zero


def can_available() -> str | None:
    try:
        import can  # noqa: F401
    except ImportError:
        return "python-can is not installed"
    return None


class CanDriver:
    name = "CAN bus"

    def __init__(self, profile: RobotProfile, settings: HardwareSettings) -> None:
        self.profile = profile
        self.settings = settings
        self.motors: dict[str, _Motor] = {}
        for m in profile.motors:
            proto = settings.protocols.get(m.type, ProtocolConfig())
            ranges = P.MitRanges(proto.p_max, proto.v_max, proto.kp_max, proto.kd_max, proto.t_max)
            self.motors[m.joint] = _Motor(m.joint, m.bus, m.node, m.direction, m.zero, proto, ranges)
        self.buses: dict = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.connected = False

    # Connection.

    def connect(self) -> None:
        problem = can_available()
        if problem:
            raise DriverError(problem)
        import can

        wanted = {m.bus for m in self.motors.values()}
        configs = {b.name: b for b in self.settings.buses}
        missing = [b for b in sorted(wanted) if b not in configs or not configs[b].channel and configs[b].interface != "virtual"]
        if missing:
            raise DriverError(f"Choose an adapter channel for {', '.join(missing)} first")
        try:
            for name in sorted(wanted):
                c = configs[name]
                kwargs = {"interface": c.interface, "channel": c.channel or name}
                if c.interface not in ("virtual", "socketcan"):
                    kwargs["bitrate"] = c.bitrate
                self.buses[name] = can.Bus(**kwargs)
        except Exception as error:  # Each adapter library raises its own errors.  # noqa: BLE001
            self.disconnect()
            raise DriverError(f"Could not open the CAN adapter: {error}") from None
        self._stop.clear()
        for name, bus in self.buses.items():
            thread = threading.Thread(target=self._receive, args=(name, bus), name=f"can-{name}", daemon=True)
            thread.start()
            self._threads.append(thread)
        self.connected = True

    def disconnect(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1)
        self._threads.clear()
        for bus in self.buses.values():
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.buses.clear()
        self.connected = False

    # Receiving.

    def _receive(self, name: str, bus) -> None:
        by_node = {m.node: m for m in self.motors.values() if m.bus == name}
        while not self._stop.is_set():
            try:
                message = bus.recv(timeout=0.05)
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
                continue
            if message is None or message.is_remote_frame:
                continue
            now = time.monotonic()
            data = bytes(message.data)
            # MIT replies carry the node in their first byte.
            if len(data) >= 6 and data[0] in by_node and by_node[data[0]].protocol.protocol == "mit" and message.arbitration_id <= 0x7F:
                motor = by_node[data[0]]
                _, p, v, t = P.mit_reply(data, motor.ranges)
                with self._lock:
                    motor.position, motor.velocity, motor.torque, motor.updated = p, v, t, now
                continue
            node, cmd = P.odrive_split(message.arbitration_id)
            motor = by_node.get(node)
            if motor is None or motor.protocol.protocol != "odrive":
                continue
            ratio = max(motor.protocol.gear_ratio, 1e-6)
            with self._lock:
                if cmd == P.OD.ENCODER_ESTIMATES and len(data) >= 8:
                    turns, speed = P.odrive_estimates(data)
                    motor.position = turns * 2 * math.pi / ratio
                    motor.velocity = speed * 2 * math.pi / ratio
                    motor.updated = now
                elif cmd == P.OD.HEARTBEAT and len(data) >= 5:
                    error, state = P.odrive_heartbeat(data)
                    motor.fault = f"error 0x{error:08X}" if error else None
                    motor.extra["state"] = state
                    if not motor.updated:
                        motor.updated = now
                elif cmd == P.OD.GET_TEMPERATURE and len(data) >= 8:
                    _, motor_temp = P.odrive_temperature(data)
                    motor.temperature = motor_temp
                elif cmd == P.OD.GET_TORQUES and len(data) >= 8:
                    _, estimate = P.odrive_torques(data)
                    motor.torque = estimate * ratio

    def _send(self, motor: _Motor, arbitration_id: int, data: bytes = b"", remote: bool = False) -> None:
        import can

        bus = self.buses.get(motor.bus)
        if bus is None:
            raise DriverError(f"{motor.bus} is not open")
        message = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=False, is_remote_frame=remote, dlc=8 if remote else len(data))
        try:
            bus.send(message, timeout=0.01)
        except Exception as error:  # noqa: BLE001
            raise DriverError(f"{motor.bus}: could not send ({error})") from None

    # Driver interface.

    def _require(self) -> None:
        if not self.connected:
            raise DriverError("Not connected to the CAN buses")

    def scan(self) -> list[str]:
        self._require()
        for motor in self.motors.values():
            if motor.protocol.protocol == "mit":
                self._send(motor, motor.node, P.mit_command(0, 0, 0, 0, 0, motor.ranges))
            else:
                self._send(motor, P.odrive_id(motor.node, P.OD.ENCODER_ESTIMATES), remote=True)
        time.sleep(0.25)
        now = time.monotonic()
        with self._lock:
            return [j for j, m in self.motors.items() if now - m.updated < 0.5]

    def read(self) -> dict[str, MotorState]:
        self._require()
        now = time.monotonic()
        out = {}
        with self._lock:
            for joint, m in self.motors.items():
                if not m.updated:
                    continue
                out[joint] = MotorState(
                    m.to_joint(m.position),
                    m.direction * m.velocity,
                    m.direction * m.torque,
                    m.temperature,
                    m.fault,
                    now - m.updated,
                )
        return out

    def imu(self) -> ImuState | None:
        return None  # The IMU sits on the robot's computer: use the remote driver for it.

    def enable(self) -> None:
        self._require()
        for motor in self.motors.values():
            if motor.protocol.protocol == "mit":
                self._send(motor, motor.node, P.MIT_ENTER)
            else:
                self._send(motor, P.odrive_id(motor.node, P.OD.CLEAR_ERRORS))
                self._send(motor, P.odrive_id(motor.node, P.OD.SET_CONTROLLER_MODE), P.odrive_controller_mode())
                self._send(motor, P.odrive_id(motor.node, P.OD.SET_AXIS_STATE), P.odrive_axis_state(P.OD.CLOSED_LOOP))

    def command(self, commands: Mapping[str, MotorCommand]) -> None:
        self._require()
        for joint, cmd in commands.items():
            motor = self.motors.get(joint)
            if motor is None:
                continue
            target = motor.to_motor(cmd.position)
            velocity = motor.direction * cmd.velocity
            torque = motor.direction * cmd.torque
            if motor.protocol.protocol == "mit":
                limit = cmd.torque_limit or motor.ranges.t_max
                torque = max(-limit, min(limit, torque))
                self._send(motor, motor.node, P.mit_command(target, velocity, cmd.kp, cmd.kd, torque, motor.ranges))
            else:
                ratio = max(motor.protocol.gear_ratio, 1e-6)
                if abs(motor.gains[0] - cmd.kp) > 1e-3 or abs(motor.gains[1] - cmd.kd) > 1e-3:
                    pos_gain, vel_gains = P.odrive_gains(cmd.kp, cmd.kd, ratio)
                    self._send(motor, P.odrive_id(motor.node, P.OD.SET_POS_GAIN), pos_gain)
                    self._send(motor, P.odrive_id(motor.node, P.OD.SET_VEL_GAINS), vel_gains)
                    motor.gains = (cmd.kp, cmd.kd)
                turns = target * ratio / (2 * math.pi)
                self._send(
                    motor,
                    P.odrive_id(motor.node, P.OD.SET_INPUT_POS),
                    P.odrive_set_input_pos(turns, velocity * ratio / (2 * math.pi), torque / ratio),
                )
                self._send(motor, P.odrive_id(motor.node, P.OD.ENCODER_ESTIMATES), remote=True)

    def relax(self) -> None:
        if not self.connected:
            return
        for motor in self.motors.values():
            try:
                if motor.protocol.protocol == "mit":
                    self._send(motor, motor.node, P.MIT_EXIT)
                else:
                    self._send(motor, P.odrive_id(motor.node, P.OD.SET_AXIS_STATE), P.odrive_axis_state(P.OD.IDLE))
            except DriverError:
                continue

    def set_zero(self, joint: str) -> float:
        """The zero offset that makes the joint read zero where it stands now."""
        motor = self.motors[joint]
        with self._lock:
            motor.zero = motor.position
        return motor.zero
