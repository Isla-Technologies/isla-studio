"""Smoke-test the packaged Windows runtime without a system Python or browser."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "desktop" / "payload"


def main(payload: Path) -> None:
    python = payload / "python" / "python.exe"
    with tempfile.TemporaryDirectory(prefix="isla-desktop-") as folder:
        data = Path(folder)
        env = os.environ.copy()
        env.update(PYTHONPATH=str(payload / "studio"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_TARGET=str(data / "python-packages"))
        packages = Path(env["PIP_TARGET"])
        packages.mkdir()
        (packages / "desktop-check.pth").write_text('import os; os.environ["ISLA_PTH_CHECK"] = "yes"\n', encoding="utf-8")
        subprocess.run([str(python), "-c", 'import os; assert os.environ.get("ISLA_PTH_CHECK") == "yes"'], cwd=data, env=env, check=True)
        for key, name in (("ISLA_ROBOTS", "robots"), ("ISLA_RUNS", "runs"), ("ISLA_POLICIES", "policies"), ("ISLA_SETTINGS", "settings"), ("ISLA_CACHE", "cache")):
            env[key] = str(data / name)
        subprocess.run([str(python), "-m", "pip", "check"], cwd=data, env=env, check=True)
        for attempt in range(2):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            log = data / f"server-{attempt}.log"
            with log.open("wb") as output:
                process = subprocess.Popen(
                    [str(python), "-u", "-m", "isla_rl.server", "--port", str(port), "--no-browser", "--stop-with-stdin"],
                    cwd=data, env=env, stdin=subprocess.PIPE, stdout=output, stderr=output,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                try:
                    base = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError(log.read_text())
                        try:
                            with urllib.request.urlopen(base + "/api/status", timeout=2) as response:
                                status = json.load(response)
                            break
                        except OSError:
                            time.sleep(0.2)
                    else:
                        raise TimeoutError(log.read_text())
                    for endpoint in ("/", "/api/robots/luma_1/meta", "/api/robots/luma_1/meshes.bin", "/api/runs"):
                        with urllib.request.urlopen(base + endpoint, timeout=20) as response:
                            assert response.status == 200 and response.read(), endpoint
                    assert (data / "robots/luma_1/robot.json").is_file()
                    assert (data / "runs/luma1-walk/policy.json").is_file()
                    websocket = (
                        "import asyncio,json,websockets\n"
                        "async def check():\n"
                        f"    async with websockets.connect('ws://127.0.0.1:{port}/ws') as socket:\n"
                        "        for _ in range(100):\n"
                        "            message=json.loads(await asyncio.wait_for(socket.recv(),10))\n"
                        "            if message.get('type')=='frame':\n"
                        "                assert message.get('qpos')\n"
                        "                return\n"
                        "        raise RuntimeError('No live simulation frames')\n"
                        "asyncio.run(check())\n"
                    )
                    subprocess.run([str(python), "-c", websocket], cwd=data, env=env, check=True, timeout=30)
                    print(f"Packaged startup {attempt + 1}, robot assets and example policy: OK ({status.get('robot', '')})", flush=True)
                finally:
                    process.stdin.close()
                    try:
                        code = process.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError("Packaged backend did not stop on stdin EOF") from None
                    if code:
                        raise RuntimeError(log.read_text())
                print("Clean EOF shutdown: OK", flush=True)
        # Run the same interpreter/CLI that TrainingManager launches. Keep this tiny.
        setup = (
            "from isla_rl.learn.skills import TrainingSetup; "
            "from isla_rl.learn.trainer import create_run; "
            "from isla_rl.robot.profile import RobotProfile; "
            "create_run('desktop-smoke', TrainingSetup(robot='luma_1', skill='stand', robots=2, steps=48), RobotProfile.load('luma_1'))"
        )
        subprocess.run([str(python), "-c", setup], cwd=data, env=env, check=True)
        subprocess.run(
            [str(python), "-m", "isla_rl.learn", "train", str(data / "runs/desktop-smoke"), "--device", "cpu", "--iterations", "1", "--threads", "1", "--no-swarm"],
            cwd=data, env=env, check=True, timeout=120,
        )
        assert (data / "runs/desktop-smoke/policy.json").is_file()
        print("Packaged CPU training subprocess: OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, default=PAYLOAD, help="Prepared or installed payload folder")
    main(parser.parse_args().payload.resolve())
