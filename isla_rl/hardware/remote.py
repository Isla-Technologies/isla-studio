"""The robot's motors through its own computer: ``python -m isla_rl.runtime`` there.

The runtime owns the CAN buses and the IMU on the robot and streams every
reading over a WebSocket; this driver keeps the latest and sends commands
without waiting for answers, so Wi-Fi latency never holds up the control loop.
If this laptop falls silent, the runtime's watchdog relaxes the motors.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Mapping

from .base import DriverError, ImuState, MotorCommand, MotorState

PORT = 47210


class RemoteDriver:
    name = "Robot computer"

    def __init__(self, address: str) -> None:
        host, _, port = address.partition(":")
        self.url = f"ws://{host}:{int(port or PORT)}"
        self._ws = None
        self._states: dict[str, MotorState] = {}
        self._imu: ImuState | None = None
        self._stamp = 0.0
        self._replies: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
        from websockets.sync.client import connect

        try:
            self._ws = connect(self.url, open_timeout=3, max_size=2**22)
        except Exception as error:  # noqa: BLE001
            raise DriverError(f"Could not reach the robot at {self.url}: {error}") from None
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive, name="remote-robot", daemon=True)
        self._thread.start()
        self.name = f"Robot computer ({self.url[5:]})"

    def disconnect(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._ws = None

    def _receive(self) -> None:
        while not self._stop.is_set() and self._ws is not None:
            try:
                text = self._ws.recv(timeout=0.5)
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "states":
                states = {
                    j: MotorState(s[0], s[1], s[2], s[3], s[4], s[5])
                    for j, s in message.get("motors", {}).items()
                }
                imu = message.get("imu")
                with self._lock:
                    self._states = states
                    self._imu = ImuState(tuple(imu["quat"]), tuple(imu["gyro"]), 0.0) if imu else None
                    self._stamp = time.monotonic()
            elif "id" in message:
                with self._lock:
                    self._replies[message["id"]] = message

    def _send(self, message: dict) -> None:
        if self._ws is None:
            raise DriverError("Not connected to the robot")
        with self._send_lock:
            try:
                self._ws.send(json.dumps(message, separators=(",", ":")))
            except Exception as error:  # noqa: BLE001
                raise DriverError(f"The robot connection dropped: {error}") from None

    def _ask(self, op: str, timeout: float = 2.0, **fields) -> dict:
        ask = next(self._ids)
        self._send({"op": op, "id": ask, **fields})
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            with self._lock:
                reply = self._replies.pop(ask, None)
            if reply is not None:
                if reply.get("error"):
                    raise DriverError(reply["error"])
                return reply
            time.sleep(0.005)
        raise DriverError(f"The robot did not answer {op}")

    def scan(self) -> list[str]:
        return list(self._ask("scan").get("joints", []))

    def read(self) -> dict[str, MotorState]:
        with self._lock:
            age = time.monotonic() - self._stamp if self._stamp else 9.9
            return {j: MotorState(s.position, s.velocity, s.torque, s.temperature, s.fault, s.age + age) for j, s in self._states.items()}

    def imu(self) -> ImuState | None:
        with self._lock:
            return self._imu

    def enable(self) -> None:
        self._ask("enable")

    def command(self, commands: Mapping[str, MotorCommand]) -> None:
        self._send(
            {
                "op": "command",
                "commands": {
                    j: [c.position, c.velocity, c.kp, c.kd, c.torque, c.torque_limit] for j, c in commands.items()
                },
            }
        )

    def relax(self) -> None:
        if self._ws is not None:
            try:
                self._ask("relax", timeout=1.0)
            except DriverError:
                pass

    def set_zero(self, joint: str) -> float:
        return float(self._ask("set_zero", joint=joint)["zero"])
