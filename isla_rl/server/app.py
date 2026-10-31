"""The platform's web server: the web app, its API, and one WebSocket of live data.

REST carries what is asked for (robots, designs, runs, settings); the WebSocket
carries what changes on its own: simulation frames 30 times a second, the
hardware link's status, training reports, and commands that must be quick
(steering, pushes, the emergency stop). Everything that runs on a thread
(simulation, hardware loop, training watcher) publishes through :class:`Hub`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from typing import Any

import mujoco
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, paths
from ..hardware.base import DriverError
from ..hardware.can_driver import can_available
from ..hardware.config import HardwareSettings
from ..hardware.link import HardwareLink
from ..learn import device as devices
from ..learn.skills import TrainingSetup, catalogue
from ..learn.trainer import POLICY, Run, delete_run, list_runs
from ..robot import analysis, bundle, importer
from ..robot.build import Built, build, measure
from ..robot.card import card
from ..robot.profile import RobotProfile, list_robots
from ..sim.session import LiveSession
from .training import TrainingManager


class Hub:
    """Fan-out of messages from any thread to every connected browser."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def publish(self, message: dict) -> None:
        loop = self.loop
        if loop is None or not self.clients:
            return
        text = json.dumps(message, separators=(",", ":"), allow_nan=False, default=_plain)
        loop.call_soon_threadsafe(self._deliver, text, message.get("type") == "frame")

    def _deliver(self, text: str, droppable: bool) -> None:
        for queue in list(self.clients):
            if queue.full():
                if droppable:
                    continue
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(text)


def _plain(value: Any):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    raise TypeError(f"{type(value).__name__} is not JSON")


def _clean(value):
    """NaN and infinity become null: browsers' JSON has neither."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "tolist"):
        return _clean(value.tolist())
    return value


class Platform:
    """Everything the server holds: the robot, its simulation, its hardware, training."""

    def __init__(self, robot: str | None = None) -> None:
        paths.ensure_dirs()
        self.hub = Hub()
        self.training = TrainingManager(self.hub.publish)
        robots = list_robots()
        if not robots:
            raise RuntimeError(f"No robots in {paths.ROBOTS}")
        wanted = robot or _remembered() or paths.DEFAULT_ROBOT
        self.profile = next((r for r in robots if r.id == wanted), robots[0])
        self._display: dict[str, tuple[str, Built]] = {}  # Robots looked at in the hangar.
        self.session = LiveSession(self.profile)
        self.session.listeners.append(self.hub.publish)
        self.settings = HardwareSettings.load(self.profile.id, self.profile)
        self.link = HardwareLink(self.profile, self.settings, self.hub.publish)
        self._built_train = None
        self._gpu: dict | None = None
        self._lock = threading.Lock()
        self.session.listeners.append(self._follow)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.hub.loop = loop
        self.session.start()
        self.training.start_watching()
        threading.Thread(target=self._warm_cards, name="robot-cards", daemon=True).start()

    def _warm_cards(self) -> None:
        """Work out every robot's card ahead of the hangar asking for it."""
        for profile in list_robots():
            with contextlib.suppress(Exception):
                card(profile)

    def built_display(self, profile: RobotProfile) -> Built:
        """The display model of any robot: the live one's, or a cached build for the hangar."""
        if profile.id == self.profile.id:
            return self.session.built
        key = json.dumps(profile.data, sort_keys=True)
        cached = self._display.get(profile.id)
        if cached is None or cached[0] != key:
            if len(self._display) >= 4:
                self._display.pop(next(iter(self._display)))
            cached = self._display[profile.id] = (key, build(profile, None, "display"))
        return cached[1]

    def close(self) -> None:
        self.session.stop()
        self.training.close()
        if self.link.state != "disconnected":
            self.link.disconnect()

    def _follow(self, frame: dict) -> None:
        """While the robot follows the simulation, the simulation's targets go to it."""
        if self.link.state == "armed" and self.link.mode == "follow" and frame.get("source") == "sim":
            targets = {j: m[3] for j, m in zip(self.session.actuator_joints, frame["motors"], strict=True)}
            self.link.set_follow(targets)
        if self.session.mode == "mirror" and self.link.states:
            self.session.mirror(
                {j: s.position for j, s in self.link.states.items()},
                list(self.link.imu.quaternion) if self.link.imu is not None else None,
            )

    def built_train(self):
        with self._lock:
            if self._built_train is None:
                self._built_train = build(self.profile, None, purpose="train")
            return self._built_train

    def switch_robot(self, robot: str) -> None:
        """Load another robot from the hangar: its simulation, hardware settings and link."""
        profile = RobotProfile.load(robot)
        if self.link.state != "disconnected":
            self.link.disconnect()
        self.session.set_policy(None)  # A policy drives the robot it learned on.
        self.profile = profile
        self._built_train = None
        self._display.pop(profile.id, None)
        self.session.profile = profile
        self.session.load(None)
        self.session.set_mode("stand")
        self.settings = HardwareSettings.load(profile.id, profile)
        self.link = HardwareLink(profile, self.settings, self.hub.publish)
        _remember(profile.id)
        self.hub.publish({"type": "robot", "robot": profile.id})

    def gpu(self) -> dict:
        if self._gpu is None:
            problem = devices.gpu_problem()
            name = devices.gpu_name() if problem is None else None
            self._gpu = {"ready": problem is None, "problem": problem, "name": name, "installed": devices.gpu_installed()}
        return self._gpu


def create_app(robot: str | None = None) -> FastAPI:
    state = Platform(robot)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        state.start(asyncio.get_running_loop())
        yield
        state.close()

    app = FastAPI(title="Isla Studio", version=__version__, lifespan=lifespan)
    app.state.platform = state

    # Status.

    @app.get("/api/status")
    def status():
        return _clean(
            {
                "version": __version__,
                "os": platform.system(),
                "os_release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "mujoco": mujoco.__version__,
                "cpus": os.cpu_count(),
                "gpu": state.gpu(),
                "robot": state.profile.id,
                "can": can_available(),
            }
        )

    # Robots.

    @app.get("/api/robots")
    def robots():
        """The hangar: every robot's card, with its training runs and policies."""
        trained: dict[str, list[int]] = {}
        for run in list_runs():
            try:
                robot = run.setup.robot
            except (OSError, ValueError, KeyError):
                continue
            counts = trained.setdefault(robot, [0, 0])
            counts[0] += 1
            counts[1] += int(run.path(POLICY).exists())
        cards = []
        # The team's own robots first, then imported ones in the order they arrived.
        ordered = sorted(list_robots(), key=lambda r: ("imported_from" in r.data, (r.folder / "robot.json").stat().st_ctime))
        for profile in ordered:
            try:
                entry = card(profile)
            except Exception as error:  # A robot whose model no longer builds still shows.  # noqa: BLE001
                entry = {"id": profile.id, "name": profile.name, "about": profile.about, "problem": str(error)}
            names = [profile.id, *profile.data.get("aliases", [])]
            entry = {
                **entry,
                "active": profile.id == state.profile.id,
                "runs": sum(trained.get(n, [0, 0])[0] for n in names),
                "policies": sum(trained.get(n, [0, 0])[1] for n in names),
            }
            cards.append(entry)
        return _clean(cards)

    @app.post("/api/robots/{robot}/activate")
    async def activate(robot: str):
        profile = _profile(state, robot)
        try:
            await asyncio.to_thread(state.switch_robot, profile.id)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(400, f"{profile.name} could not be loaded: {error}") from None
        return {"ok": True, "robot": state.profile.id}

    @app.post("/api/robots/import")
    async def import_path(request: Request):
        """Import a robot from a model file on this computer."""
        body = await request.json()
        source = str(body.get("path", "")).strip().strip('"')
        if not source:
            raise HTTPException(400, "Enter the path of the robot's model file (.xml or .urdf).")
        return await _import(source, body)

    @app.put("/api/robots/upload/{token}/{name:path}")
    async def upload_file(token: str, name: str, request: Request):
        """One file of a robot's folder, sent from the browser before importing it."""
        folder = _upload_folder(token)
        target = (folder / name).resolve()
        if folder not in target.parents:
            raise HTTPException(400, f"{name} is outside the robot's folder")
        if target.suffix.lower() not in UPLOAD_TYPES:
            raise HTTPException(400, f"{name}: only model, mesh and texture files are imported")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await request.body())
        return {"ok": True}

    @app.post("/api/robots/upload/{token}/import")
    async def import_upload(token: str, request: Request):
        """Import the robot whose folder was just sent, then forget the sent files."""
        body = await request.json()
        folder = _upload_folder(token)
        source = (folder / str(body.get("model", ""))).resolve()
        if folder not in source.parents:
            raise HTTPException(400, "Choose the model file inside the robot's folder")
        try:
            return await _import(str(source), body)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    async def _import(source: str, body: dict):
        try:
            profile = await asyncio.to_thread(importer.import_robot, source, body.get("id") or None, body.get("name") or None)
            entry = await asyncio.to_thread(card, profile)
        except importer.ImportProblem as error:
            raise HTTPException(400, str(error)) from None
        except (ValueError, KeyError, FileNotFoundError) as error:
            raise HTTPException(400, f"The robot was copied in but does not build yet: {error}. Correct robots/{importer.slug(body.get('id') or body.get('name') or '')}/robot.json or remove it.") from None
        return _clean({**entry, "active": False, "runs": 0, "policies": 0})

    @app.delete("/api/robots/{robot}")
    def remove_robot(robot: str):
        """Take a robot out of the hangar (its folder is deleted)."""
        profile = _profile(state, robot)
        if profile.id == state.profile.id:
            raise HTTPException(400, f"{profile.name} is loaded. Load another robot before removing it.")
        if profile.folder.parent.resolve() != paths.ROBOTS.resolve():
            raise HTTPException(400, f"{profile.name} lives outside the hangar's folder")
        names = {profile.id, *profile.data.get("aliases", [])}
        used = []
        for run in list_runs():
            with contextlib.suppress(OSError, ValueError, KeyError):
                if run.setup.robot in names:
                    used.append(run.name)
        if used:
            raise HTTPException(400, f"Training runs use {profile.name} ({', '.join(used[:3])}). Delete those runs first.")
        shutil.rmtree(profile.folder)
        shutil.rmtree(paths.CACHE / profile.id, ignore_errors=True)
        state._display.pop(profile.id, None)
        return {"ok": True}

    @app.get("/api/robots/{robot}/meta")
    def robot_meta(robot: str):
        profile = _profile(state, robot)
        built = state.built_display(profile)
        document = bundle.meta(profile, built)
        _, index = bundle.mesh_blob(profile, built)
        document["meshes"] = index
        document["design_values"] = profile.default_design()
        return _clean(document)

    @app.get("/api/robots/{robot}/meshes.bin")
    def robot_meshes(robot: str):
        profile = _profile(state, robot)
        built = state.built_display(profile)
        blob, _ = bundle.mesh_blob(profile, built)
        return Response(blob, media_type="application/octet-stream", headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/robots/{robot}/analysis")
    async def robot_analysis(robot: str, request: Request):
        profile = _profile(state, robot)
        body = await request.json()
        design = profile.complete_design(body.get("design") or {})
        built = state.built_train() if profile.id == state.profile.id else build(profile, None, "train")
        return _clean(await asyncio.to_thread(analysis.analyse, profile, built, measure(profile), design))

    @app.post("/api/robots/{robot}/design")
    async def robot_design(robot: str, request: Request):
        """Make a design the robot's own: saved in its profile, the live simulation rebuilt."""
        profile = _profile(state, robot)
        body = await request.json()
        design = profile.complete_design(body.get("design") or {})
        profile.data["chosen_design"] = {k: round(float(v), 4) for k, v in design.items()}
        profile.save()
        if profile.id == state.profile.id:
            await asyncio.to_thread(state.session.set_design, design)
        state.hub.publish({"type": "design", "robot": profile.id, "design": design})
        return {"ok": True, "design": design}

    @app.put("/api/robots/{robot}/motors")
    async def robot_motors(robot: str, request: Request):
        """Change the motor map: bus, node, direction and zero of each joint's motor."""
        profile = _profile(state, robot)
        body = await request.json()
        by_joint = {m["joint"]: m for m in profile.data["motors"]}
        for change in body.get("motors", []):
            motor = by_joint.get(change.get("joint"))
            if motor is None:
                continue
            for key in ("bus", "node", "direction", "zero"):
                if key in change:
                    motor[key] = change[key] if key == "bus" else (int(change[key]) if key in ("node", "direction") else float(change[key]))
        try:
            profile.save()
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {"ok": True, "motors": [m.to_dict() for m in profile.motors]}

    # Training.

    @app.get("/api/skills")
    def skills():
        return catalogue()

    @app.get("/api/runs")
    def runs():
        return _clean(state.training.runs())

    @app.get("/api/runs/{name}")
    def run_detail(name: str):
        run = _run(name)
        latest = state.training.snapshot(name)
        return _clean(
            {
                "summary": {**run.summary(), "running": state.training.running(name)},
                "setup": run.setup.to_dict(),
                "progress": run.progress(),
                "codesign": run.codesign(),
                "latest": latest,
            }
        )

    @app.post("/api/runs")
    async def start_run(request: Request):
        body = await request.json()
        name = str(body.get("name") or "run").strip()
        try:
            setup = TrainingSetup.from_dict({**body.get("setup", {}), "robot": state.profile.id})
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        if (paths.RUNS / name).exists():
            raise HTTPException(409, f"A run named {name} already exists")
        run = state.training.start(name, setup, state.profile, str(body.get("device", "auto")))
        return {"ok": True, "run": run.name}

    @app.post("/api/runs/{name}/stop")
    def stop_run(name: str):
        _run(name)
        state.training.stop(name)
        return {"ok": True}

    @app.post("/api/runs/{name}/evaluate")
    def evaluate_run(name: str):
        _run(name)
        try:
            state.training.evaluate(name)
        except (FileNotFoundError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from None
        return {"ok": True}

    @app.get("/api/runs/{name}/evaluation")
    def run_evaluation(name: str):
        path = _run(name).path("evaluation.json")
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return {"testing": state.training.evaluating(name), "report": report}

    @app.post("/api/runs/{name}/resume")
    async def resume_run(name: str, request: Request):
        body = await request.json() if await request.body() else {}
        try:
            state.training.resume(name, str(body.get("device", "auto")))
        except (FileNotFoundError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from None
        return {"ok": True}

    @app.delete("/api/runs/{name}")
    def remove_run(name: str):
        run = _run(name)
        if state.training.running(name):
            raise HTTPException(400, "Stop the run before deleting it")
        delete_run(run)
        return {"ok": True}

    @app.get("/api/runs/{name}/policy.json")
    def run_policy(name: str):
        run = _run(name)
        path = run.path("policy.json")
        if not path.exists():
            raise HTTPException(404, "This run has no policy yet")
        return FileResponse(path, media_type="application/json", filename=f"{name}-policy.json")

    # The live simulation.

    @app.post("/api/sim/policy")
    async def sim_policy(request: Request):
        body = await request.json()
        name = body.get("run")
        if not name:
            state.session.set_policy(None)
            return {"ok": True}
        run = _run(name)
        policy = run.policy()
        if policy is None:
            raise HTTPException(404, "This run has no policy yet")
        if policy.robot and policy.robot != state.profile.id:
            raise HTTPException(400, f"That policy is for {policy.robot}")
        if policy.design and body.get("use_design", True):
            await asyncio.to_thread(state.session.set_design, state.profile.complete_design(policy.design))
        state.session.set_policy(policy, name)
        state.session.reset()
        state.session.set_mode("policy")
        return {"ok": True, "design": policy.design, "skill": policy.skill}

    # Hardware.

    @app.get("/api/hardware")
    def hardware():
        return _clean({**state.link.status(), "settings": state.settings.to_dict(), "log": state.link.log[-60:]})

    @app.put("/api/hardware/settings")
    async def hardware_settings(request: Request):
        if state.link.state == "armed":
            raise HTTPException(400, "Disarm before changing settings")
        body = await request.json()
        try:
            current = state.settings.to_dict()
            merged = {**current, **body, "robot": state.profile.id}
            if "limits" in body:
                merged["limits"] = {**current["limits"], **body["limits"]}
            settings = HardwareSettings.from_dict(merged)
        except (ValueError, TypeError) as error:
            raise HTTPException(400, str(error)) from None
        settings.save()
        state.settings = settings
        if state.link.state == "disconnected":
            state.link = HardwareLink(state.profile, settings, state.hub.publish)
        else:
            state.link.settings = settings
            state.link.safety.limits = settings.limits
        return {"ok": True, "settings": settings.to_dict()}

    @app.post("/api/hardware/{action}")
    async def hardware_action(action: str, request: Request):
        body = await request.json() if await request.body() else {}
        link = state.link
        try:
            if action == "connect":
                joints = await asyncio.to_thread(link.connect, state.profile.default_design())
                return {"ok": True, "joints": joints}
            if action == "disconnect":
                await asyncio.to_thread(link.disconnect)
            elif action == "scan":
                return {"ok": True, "joints": await asyncio.to_thread(link.scan)}
            elif action == "arm":
                link.arm()
            elif action == "disarm":
                link.disarm()
            elif action == "estop":
                link.estop(str(body.get("reason") or "Emergency stop pressed"))
            elif action == "clear":
                link.clear()
            elif action == "mode":
                mode = str(body.get("mode"))
                if mode == "policy":
                    name = body.get("run") or state.session.policy_name
                    policy = _run(name).policy() if name else None
                    link.set_policy(policy, name or "", state.profile.default_design())
                link.set_mode(mode)
            elif action == "zero":
                zero = link.set_zero(str(body["joint"]))
                return {"ok": True, "zero": zero}
            elif action == "inject":
                driver = link.driver
                if driver is None or not hasattr(driver, "inject"):
                    raise DriverError("Faults can only be rehearsed on the practice robot")
                driver.inject(str(body["joint"]), body.get("fault"))
            else:
                raise HTTPException(404, f"No hardware action {action}")
        except DriverError as error:
            raise HTTPException(400, str(error)) from None
        return {"ok": True}

    @app.get("/api/hardware/ports")
    def hardware_ports():
        """Serial ports, for slcan adapters."""
        try:
            from serial.tools import list_ports

            ports = [{"device": p.device, "description": p.description} for p in list_ports.comports()]
        except ImportError:
            ports = []
        return {"ports": ports, "interfaces": ["slcan", "gs_usb", "pcan", "kvaser", "socketcan", "virtual"]}

    # GPU support.

    @app.post("/api/gpu/install")
    def gpu_install():
        requirements = paths.ROOT / "requirements-gpu.txt"
        log = paths.RUNS / "gpu-install.log"
        log.parent.mkdir(parents=True, exist_ok=True)

        def work():
            with log.open("w", encoding="utf-8") as out:
                process = subprocess.Popen(
                    [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(paths.ROOT),
                )
                assert process.stdout is not None
                for line in process.stdout:
                    out.write(line)
                    state.hub.publish({"type": "gpu.install", "line": line.rstrip()})
                code = process.wait()
            state._gpu = None
            state.hub.publish({"type": "gpu.install", "done": True, "code": code, "gpu": state.gpu()})

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # The live connection.

    @app.websocket("/ws")
    async def socket(ws: WebSocket):
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=240)
        state.hub.clients.add(queue)
        await ws.send_text(json.dumps(_clean({"type": "hello", "robot": state.profile.id, "hardware": state.link.status()})))
        for run in state.training.runs():
            if run.get("running"):
                for kind, message in state.training.snapshot(run["name"]).items():
                    if kind in ("progress", "codesign", "swarm", "started"):
                        await ws.send_text(json.dumps(_clean({"type": "train", "run": run["name"], "message": message})))

        async def sender():
            while True:
                text = await queue.get()
                await ws.send_text(text)

        task = asyncio.create_task(sender())
        try:
            while True:
                message = json.loads(await ws.receive_text())
                try:
                    _command(state, message)
                except (ValueError, DriverError, KeyError) as error:
                    await ws.send_text(json.dumps({"type": "error", "message": str(error)}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            task.cancel()
            state.hub.clients.discard(queue)

    # The web app itself.

    dist = paths.WEB_DIST
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def web(path: str):
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/")
        def missing():
            return JSONResponse({"message": "The web app is not built: run `npm run build` in web/."}, status_code=503)

    return app


def _command(state: Platform, message: dict) -> None:
    """Quick commands over the WebSocket."""
    kind = message.get("type")
    session = state.session
    if kind == "sim.play":
        session.playing = bool(message.get("playing", True))
    elif kind == "sim.reset":
        session.reset()
    elif kind == "sim.mode":
        session.set_mode(str(message["mode"]))
    elif kind == "sim.gantry":
        session.set_gantry(bool(message.get("on", True)))
    elif kind == "sim.jump":
        session.jump()
    elif kind == "hw.jump":
        state.link.jump()
    elif kind == "sim.targets":
        session.set_targets({str(k): float(v) for k, v in message.get("targets", {}).items()})
    elif kind == "sim.command":
        forward, left, turn = (float(v) for v in message.get("command", [0, 0, 0]))
        session.set_command(forward, left, turn)
        state.link.set_command(forward, left, turn)
    elif kind == "sim.push":
        force = message.get("force", [0, 0, 0])
        session.push_robot((float(force[0]), float(force[1]), float(force[2])), float(message.get("seconds", 0.2)))
    elif kind == "sim.rate":
        session.rate = min(max(float(message.get("rate", 1.0)), 0.05), 4.0)
    elif kind == "hw.estop":
        state.link.estop("Emergency stop pressed")
    else:
        raise ValueError(f"Unknown command {kind!r}")


UPLOAD_TYPES = {".xml", ".urdf", ".mjcf", *importer.MESH_EXTENSIONS, ".png"}


def _upload_folder(token: str):
    if not re.fullmatch(r"[a-z0-9]{8,40}", token):
        raise HTTPException(400, "Bad upload name")
    return (paths.CACHE / "uploads" / token).resolve()


def _remembered() -> str | None:
    """The robot loaded when Isla Studio last ran."""
    path = paths.SETTINGS / "studio.json"
    with contextlib.suppress(OSError, ValueError, AttributeError):
        return json.loads(path.read_text(encoding="utf-8")).get("robot")
    return None


def _remember(robot: str) -> None:
    path = paths.SETTINGS / "studio.json"
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"robot": robot}) + "\n", encoding="utf-8")


def _profile(state: Platform, robot: str) -> RobotProfile:
    if robot == state.profile.id:
        return state.profile
    try:
        return RobotProfile.load(robot)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(404, str(error)) from None


def _run(name: str) -> Run:
    folder = paths.RUNS / name
    if not (folder / "setup.json").exists() or ".." in name:
        raise HTTPException(404, f"No run named {name}")
    return Run(folder)

