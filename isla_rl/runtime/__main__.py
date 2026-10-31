"""The robot's own program: serve its motors and IMU to the platform over the network.

Run on the robot's computer (Linux on a Jetson or a Raspberry Pi; any OS works):

    python -m isla_rl.runtime --robot luma_1            # real CAN buses (settings/<robot>-hardware.json)
    python -m isla_rl.runtime --robot luma_1 --practice # a practice robot, to rehearse

then on the laptop choose **Robot computer** on the Connect page with its address.
The runtime streams motor and IMU readings 200 times a second and applies the
commands it receives. If the laptop falls silent for ``--watchdog`` seconds while
the motors are enabled, it relaxes them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from ..hardware.base import DriverError, MotorCommand
from ..hardware.config import HardwareSettings
from ..hardware.remote import PORT
from ..robot.profile import RobotProfile


async def serve(driver, port: int, watchdog: float) -> None:
    from websockets.asyncio.server import serve as ws_serve

    clients: set = set()
    last_heard = {"t": time.monotonic()}
    enabled = {"on": False}

    async def stream() -> None:
        while True:
            await asyncio.sleep(0.005)
            if not clients:
                continue
            try:
                states = driver.read()
                imu = driver.imu()
            except DriverError:
                continue
            message = json.dumps(
                {
                    "type": "states",
                    "motors": {j: [s.position, s.velocity, s.torque, s.temperature, s.fault, s.age] for j, s in states.items()},
                    "imu": None if imu is None else {"quat": list(imu.quaternion), "gyro": list(imu.gyro)},
                },
                separators=(",", ":"),
            )
            for ws in list(clients):
                try:
                    await ws.send(message)
                except Exception:  # noqa: BLE001
                    clients.discard(ws)
            if enabled["on"] and time.monotonic() - last_heard["t"] > watchdog:
                driver.relax()
                enabled["on"] = False
                print("Watchdog: the laptop fell silent, motors relaxed", flush=True)

    async def handle(ws) -> None:
        clients.add(ws)
        print(f"Platform connected from {ws.remote_address}", flush=True)
        try:
            async for text in ws:
                last_heard["t"] = time.monotonic()
                message = json.loads(text)
                op, ask = message.get("op"), message.get("id")
                reply: dict = {"id": ask} if ask is not None else {}
                try:
                    if op == "command":
                        driver.command({j: MotorCommand(*v) for j, v in message["commands"].items()})
                    elif op == "scan":
                        reply["joints"] = driver.scan()
                    elif op == "enable":
                        driver.enable()
                        enabled["on"] = True
                    elif op == "relax":
                        driver.relax()
                        enabled["on"] = False
                    elif op == "set_zero":
                        reply["zero"] = driver.set_zero(message["joint"])
                except DriverError as error:
                    reply["error"] = str(error)
                if ask is not None:
                    await ws.send(json.dumps(reply))
        finally:
            clients.discard(ws)
            driver.relax()
            enabled["on"] = False
            print("Platform disconnected: motors relaxed", flush=True)

    async with ws_serve(handle, "0.0.0.0", port, max_size=2**22):
        print(f"Isla runtime listening on port {port}", flush=True)
        await stream()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m isla_rl.runtime")
    parser.add_argument("--robot", default="luma_1")
    parser.add_argument("--practice", action="store_true", help="Serve a practice robot instead of real CAN buses")
    parser.add_argument("--floor", action="store_true", help="Practice robot on the floor instead of a gantry")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--watchdog", type=float, default=0.3)
    args = parser.parse_args(argv)
    profile = RobotProfile.load(args.robot)
    if args.practice:
        from ..hardware.virtual import VirtualDriver

        driver = VirtualDriver(profile, floor=args.floor)
    else:
        from ..hardware.can_driver import CanDriver

        driver = CanDriver(profile, HardwareSettings.load(profile.id, profile))
    driver.connect()
    try:
        asyncio.run(serve(driver, args.port, args.watchdog))
    except KeyboardInterrupt:
        pass
    finally:
        driver.relax()
        driver.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
