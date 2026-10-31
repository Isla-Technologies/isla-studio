"""The link to a robot: connecting, arming, the 200 Hz control loop and its safety.

States: ``disconnected`` -> ``connected`` (motors relaxed, readings flowing) ->
``armed`` (motors holding, commands flowing). Any fault (a motor silent or too
hot, a drive error, a joint far from its target, the emergency stop) relaxes
every motor and latches ``fault`` until it is cleared.

While armed, the robot does what its mode says:

- ``hold``: keep the pose it had when armed;
- ``follow``: follow the live simulation's motor targets, no faster than the top speed;
- ``policy``: run a trained policy on the robot's own readings (it needs an IMU).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ..learn.policy import Policy, PolicyRunner
from ..robot.profile import RobotProfile
from .base import DriverError, ImuState, MotorState
from .config import HardwareSettings
from .safety import JointLimits, SafetyFilter

RATE = 200.0
STATUS_RATE = 20.0
STATES = ("disconnected", "connected", "armed", "fault")
MODES = ("hold", "follow", "policy")


class HardwareLink:
    def __init__(self, profile: RobotProfile, settings: HardwareSettings, publish: Callable[[dict], None]) -> None:
        self.profile = profile
        self.settings = settings
        self.publish = publish
        self.driver = None
        self.state = "disconnected"
        self.mode = "hold"
        self.fault: str | None = None
        self.armed_at = 0.0
        self.follow: dict[str, float] = {}
        self.runner: PolicyRunner | None = None
        self.policy_name = ""
        self.rate = 0.0
        self.states: dict[str, MotorState] = {}
        self.imu: ImuState | None = None
        self.scanned: list[str] = []
        self.log: list[str] = []
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_status = 0.0
        self.safety = self._make_filter()

    def _make_filter(self) -> SafetyFilter:
        types = self.profile.actuator_types
        ranges = self.profile.operating_ranges
        joints = {}
        for m in self.profile.motors:
            kind = types[m.type]
            low, high = ranges.get(m.joint, (-3.14, 3.14))
            joints[m.joint] = JointLimits(low, high, float(kind.get("peak_torque", 20)), float(kind.get("kp", 50)), float(kind.get("kd", 2)))
        return SafetyFilter(joints, self.settings.limits)

    def note(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {text}")
        self.log = self.log[-200:]
        self.publish({"type": "hardware.log", "line": f"{stamp}  {text}"})

    # Connecting.

    def connect(self, design: dict | None = None) -> list[str]:
        with self._lock:
            if self.state != "disconnected":
                self.disconnect()
            kind = self.settings.driver
            if kind == "virtual":
                from .virtual import VirtualDriver

                driver = VirtualDriver(self.profile, design, floor=self.settings.virtual_floor)
            elif kind == "can":
                from .can_driver import CanDriver

                driver = CanDriver(self.profile, self.settings)
            elif kind == "remote":
                from .remote import RemoteDriver

                driver = RemoteDriver(self.settings.remote)
            else:
                raise DriverError(f"No driver named {kind!r}")
            driver.connect()
            self.driver = driver
            self.state = "connected"
            self.fault = None
            self.safety = self._make_filter()
            self.scanned = driver.scan()
            missing = [m.joint for m in self.profile.motors if m.joint not in self.scanned]
            self.note(f"Connected to {driver.name}: {len(self.scanned)} of {len(self.profile.motors)} motors answer")
            if missing:
                self.note("Not answering: " + ", ".join(missing))
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="hardware-link", daemon=True)
            self._thread.start()
            return self.scanned

    def disconnect(self) -> None:
        with self._lock:
            self._stop.set()
            driver = self.driver
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if driver is not None:
            try:
                driver.relax()
            finally:
                driver.disconnect()
        with self._lock:
            self.driver = None
            self.state = "disconnected"
            self.states = {}
            self.note("Disconnected")
        self._publish_status(force=True)

    def scan(self) -> list[str]:
        with self._lock:
            if self.driver is None:
                raise DriverError("Connect first")
            self.scanned = self.driver.scan()
            self.note(f"Scan: {len(self.scanned)} motors answer")
            return self.scanned

    # Arming.

    def arm(self) -> None:
        with self._lock:
            if self.state == "fault":
                raise DriverError(f"Clear the stop first: {self.fault}")
            if self.state != "connected" or self.driver is None:
                raise DriverError("Connect first")
            states = self.driver.read()
            problem = self.safety.check(states, armed=False)
            if problem:
                raise DriverError(f"Cannot arm: {problem}")
            self.safety.start({j: s.position for j, s in states.items()})
            self.follow = dict(self.safety.targets)
            self.driver.enable()
            self.armed_at = time.monotonic()
            self.state = "armed"
            self.note(f"Armed ({self.mode}); stiffness ramps up over {self.settings.limits.soft_start:.1f} s")

    def disarm(self) -> None:
        with self._lock:
            if self.driver is not None:
                self.driver.relax()
            if self.state == "armed":
                self.state = "connected"
                self.note("Disarmed: motors relaxed")

    def estop(self, reason: str = "Emergency stop") -> None:
        with self._lock:
            if self.driver is not None:
                try:
                    self.driver.relax()
                except DriverError:
                    pass
            if self.state != "disconnected":
                self.state = "fault"
                self.fault = reason
                self.note(f"STOP: {reason}")
        self._publish_status(force=True)

    def clear(self) -> None:
        with self._lock:
            if self.state == "fault":
                self.state = "connected" if self.driver is not None else "disconnected"
                self.fault = None
                self.note("Stop cleared: motors stay relaxed until armed")

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"No mode {mode!r}")
        with self._lock:
            if mode == "policy" and self.runner is None:
                raise DriverError("Choose a policy to run first")
            if mode == "policy" and self.driver is not None and self.driver.imu() is None:
                raise DriverError("This connection has no IMU: run policies through the robot's computer")
            self.mode = mode
            self.note(f"Mode: {mode}")

    def set_policy(self, policy: Policy | None, name: str = "", design: dict | None = None) -> None:
        with self._lock:
            self.runner = PolicyRunner(policy, design) if policy is not None else None
            self.policy_name = name if policy is not None else ""
            if policy is None and self.mode == "policy":
                self.mode = "hold"

    def set_command(self, forward: float, left: float, turn: float) -> None:
        with self._lock:
            if self.runner is not None:
                self.runner.set_command(forward, left, turn)

    def jump(self) -> None:
        """Start a jump on the robot, when it is armed and running a policy that jumps."""
        with self._lock:
            if self.state != "armed" or self.mode != "policy" or self.runner is None:
                raise DriverError("To jump, arm the robot and run a jump policy on it first (Deploy)")
            if not self.runner.can_jump:
                raise DriverError(f"{self.policy_name} cannot jump: run a jump policy on the robot")
            if not self.runner.jump():
                raise DriverError("Already jumping: wait for it to land")
            self.note("Jump")

    def set_follow(self, targets: dict[str, float]) -> None:
        with self._lock:
            self.follow.update(targets)

    def set_zero(self, joint: str) -> float:
        with self._lock:
            if self.driver is None:
                raise DriverError("Connect first")
            if self.state == "armed":
                raise DriverError("Disarm before setting a zero")
            zero = self.driver.set_zero(joint)
            for m in self.profile.data["motors"]:
                if m["joint"] == joint:
                    m["zero"] = round(float(zero), 6)
            self.profile.save()
            self.note(f"{joint}: zero set")
            return zero

    # The loop.

    def _loop(self) -> None:
        period = 1.0 / RATE
        last = time.perf_counter()
        ticks, window = 0, time.perf_counter()
        while not self._stop.is_set():
            start = time.perf_counter()
            dt = start - last
            last = start
            try:
                self._tick(dt)
            except DriverError as error:
                self.estop(str(error))
            except Exception as error:  # Never let the loop die silently with motors on.  # noqa: BLE001
                self.estop(f"Control loop error: {error}")
            ticks += 1
            if start - window >= 1.0:
                self.rate = ticks / (start - window)
                ticks, window = 0, start
            self._publish_status()
            spare = period - (time.perf_counter() - start)
            if spare > 0:
                time.sleep(spare)

    def _tick(self, dt: float) -> None:
        with self._lock:
            driver = self.driver
            if driver is None:
                return
            states = driver.read()
            self.states = states
            self.imu = driver.imu()
            if self.state != "armed":
                return
            problem = self.safety.check(states, armed=True)
            if problem:
                raise DriverError(problem)
            if self.mode == "follow":
                desired = self.follow
            elif self.mode == "policy" and self.runner is not None and self.imu is not None:
                joints = {j: s.position for j, s in states.items()}
                speeds = {j: s.velocity for j, s in states.items()}
                desired = self.runner.step(dt, self.imu.quaternion, self.imu.gyro, joints, speeds)
            else:
                desired = {}
            commands = self.safety.commands(desired, dt, time.monotonic() - self.armed_at)
            driver.command(commands)

    # Reporting.

    def status(self) -> dict:
        joints = {}
        motors = {m.joint: m for m in self.profile.motors}
        for joint, motor in motors.items():
            s = self.states.get(joint)
            online = s is not None and s.age < self.settings.limits.state_timeout * 3
            joints[joint] = {
                "online": online,
                "q": None if s is None else round(s.position, 5),
                "dq": None if s is None else round(s.velocity, 4),
                "tau": None if s is None else round(s.torque, 3),
                "temp": None if s is None or s.temperature is None else round(s.temperature, 1),
                "fault": None if s is None else s.fault,
                "target": round(self.safety.targets[joint], 5) if joint in self.safety.targets and self.state == "armed" else None,
                "bus": motor.bus,
                "node": motor.node,
            }
        imu = None
        if self.imu is not None:
            imu = {"quat": [round(v, 5) for v in self.imu.quaternion], "gyro": [round(v, 4) for v in self.imu.gyro]}
        return {
            "type": "hardware",
            "state": self.state,
            "mode": self.mode,
            "fault": self.fault,
            "driver": self.settings.driver,
            "driver_name": getattr(self.driver, "name", ""),
            "rate": round(self.rate, 1),
            "armed_for": round(time.monotonic() - self.armed_at, 1) if self.state == "armed" else 0,
            "policy": self.policy_name,
            "can_jump": bool(self.mode == "policy" and self.runner is not None and self.runner.can_jump),
            "joints": joints,
            "imu": imu,
            "limits": self.settings.limits.__dict__,
        }

    def _publish_status(self, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_status < 1.0 / STATUS_RATE:
            return
        self._last_status = now
        try:
            self.publish(self.status())
        except Exception:  # noqa: BLE001
            pass
