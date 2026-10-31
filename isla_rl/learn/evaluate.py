"""Try a trained policy in the studio's live physics and measure what building the robot needs.

    python -m isla_rl.learn evaluate runs/luma1-walk-run

The physics is the live session's own: 2 ms steps, every motor held to its torque-speed
envelope, and the shock absorbers as built (spring, damper, preload and stroke). It is a
different simulator setup from training's, so a policy that passes here has not just
learned the training simulator's quirks.

- Walking policies are asked for a ladder of forward speeds, then backward, sideways,
  turning and a curve, then shoved from eight directions at growing strength, then sent
  roaming on random commands for a minute.
- Jumping policies stand, are shoved, then jump ten times.

Every trial records each shock absorber's stroke, shaft speed and force, and each motor's
torque and speed, so springs, dampers and motors can be chosen to fit. Loads are kept by
kind of trial (walking and running, standing, jumping, being shoved), and only from trials
the robot came through standing: a fall slams the ankles in ways no part is sized for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import mujoco
import numpy as np

from ..robot.profile import RobotProfile
from ..sim.session import TIMESTEP, LiveSession
from .policy import Policy

FALL_TILT = 50.0  # degrees from upright: fallen, as in training.
FALL_RATIO = 0.6  # Centre of mass below this share of its standing height: fallen.
CONTACT = 5.0  # N on a foot that counts as touching, as in training.
SETTLE = 2.0  # s to stand before a trial starts.
LADDER = (0.0, 0.3, 0.6, 0.9, 1.2, 1.5)  # m/s forward, from standing still up to what the policy learned.
SHOVES = (0.3, 0.5, 0.7, 0.9)  # m/s a shove adds to the whole robot's speed.
SHOVE_TIME = 0.1  # s the shove pushes for.
DIRECTIONS = 8
STAND_TIME = 10.0  # s a policy that does not walk is left standing.
JUMPS = 10
JUMP_SHOVES = (0.3, 0.5)  # A jumping policy is shoved gently, from four sides.
JUMP_DIRECTIONS = 4
ROAM = 60.0  # s of random commands.
AT_STOP = 0.0005  # m from a stroke end that counts as on the stop.
SATURATED = 0.98  # Share of the torque available that counts as at the limit.
PHASES = {"moving": "Walking and running", "standing": "Standing", "jumping": "Jumping", "shoves": "Shoved"}


@dataclass
class Shock:
    """One shock absorber's extremes over everything tried."""

    name: str
    position: str
    tendon: int
    stiffness: float
    damping: float
    free_length: float
    low: float
    high: float
    shortest: float = math.inf
    longest: float = -math.inf
    fastest: float = 0.0  # m/s, either way.
    spring_peak: float = 0.0  # N
    force_peak: float = 0.0  # N, spring and damper together.
    stop_force_peak: float = 0.0  # N from the end stops.
    on_stop: int = 0  # Steps at either end of the stroke.
    steps: int = 0

    def fresh(self) -> Shock:
        return replace(self, shortest=math.inf, longest=-math.inf, fastest=0.0, spring_peak=0.0, force_peak=0.0, stop_force_peak=0.0, on_stop=0, steps=0)

    def merge(self, other: Shock) -> None:
        self.shortest = min(self.shortest, other.shortest)
        self.longest = max(self.longest, other.longest)
        self.fastest = max(self.fastest, other.fastest)
        self.spring_peak = max(self.spring_peak, other.spring_peak)
        self.force_peak = max(self.force_peak, other.force_peak)
        self.stop_force_peak = max(self.stop_force_peak, other.stop_force_peak)
        self.on_stop += other.on_stop
        self.steps += other.steps

    def summary(self) -> dict:
        return {
            "name": self.name,
            "position": self.position,
            "stroke_mm": round((self.high - self.low) * 1000, 2),
            "used_mm": round((self.longest - self.shortest) * 1000, 2),
            "closest_to_bottom_mm": round((self.shortest - self.low) * 1000, 2),
            "closest_to_top_mm": round((self.high - self.longest) * 1000, 2),
            "on_stop_share": round(self.on_stop / max(1, self.steps), 5),
            "shaft_speed_peak": round(self.fastest, 3),
            "spring_force_peak": round(self.spring_peak, 1),
            "force_peak": round(self.force_peak, 1),
            "stop_force_peak": round(self.stop_force_peak, 1),
            "preload_force": round(self.stiffness * (self.free_length - self.high), 1),
        }


@dataclass
class Motor:
    """One motor's extremes over everything tried."""

    joint: str
    type: str
    actuator: int
    dof: int
    rating: float  # N*m the profile allows.
    no_load: float | None  # rad/s, when the profile gives it.
    torque_peak: float = 0.0
    torque_squares: float = 0.0
    speed_peak: float = 0.0
    saturated: int = 0
    steps: int = 0

    def fresh(self) -> Motor:
        return replace(self, torque_peak=0.0, torque_squares=0.0, speed_peak=0.0, saturated=0, steps=0)

    def merge(self, other: Motor) -> None:
        self.torque_peak = max(self.torque_peak, other.torque_peak)
        self.torque_squares += other.torque_squares
        self.speed_peak = max(self.speed_peak, other.speed_peak)
        self.saturated += other.saturated
        self.steps += other.steps

    def summary(self) -> dict:
        return {
            "joint": self.joint,
            "type": self.type,
            "rating": self.rating,
            "torque_peak": round(self.torque_peak, 2),
            "torque_rms": round(math.sqrt(self.torque_squares / max(1, self.steps)), 2),
            "speed_peak": round(self.speed_peak, 2),
            "no_load_speed": self.no_load,
            "saturated_share": round(self.saturated / max(1, self.steps), 4),
        }


@dataclass
class Tally:
    """The extremes of every trial of one kind the robot came through, and how many it fell in."""

    shocks: list[Shock]
    motors: list[Motor]
    trials: int = 0
    falls: int = 0

    def summary(self, phase: str) -> dict:
        return {
            "label": PHASES[phase],
            "trials": self.trials,
            "falls": self.falls,
            "shocks": [s.summary() for s in self.shocks] if any(s.steps for s in self.shocks) else [],
            "motors": [m.summary() for m in self.motors] if any(m.steps for m in self.motors) else [],
        }


@dataclass
class Window:
    """Where the robot was at the start of a measured stretch, and how steady it has been since."""

    t: float
    com: np.ndarray
    yaw: float
    forward: np.ndarray
    left: np.ndarray
    steps: int = 0
    lean: np.ndarray = field(default_factory=lambda: np.zeros(2))  # Sums of pitch and roll, degrees.
    lean_squares: np.ndarray = field(default_factory=lambda: np.zeros(2))
    rate_squares: float = 0.0  # Pitch and roll rates, (deg/s)^2.
    bounce_squares: float = 0.0  # Vertical speed of the centre of mass, (m/s)^2.


@dataclass
class Bench:
    """A live session stepped as fast as it goes, with a policy in charge, recording as it runs."""

    profile: RobotProfile
    policy: Policy
    session: LiveSession = field(init=False)
    shocks: list[Shock] = field(init=False)  # This trial's extremes so far.
    motors: list[Motor] = field(init=False)
    kept: dict[str, Tally] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.session = s = LiveSession(self.profile, dict(self.policy.design) or None)
        s.set_policy(self.policy, self.policy.name)
        s.set_mode("policy")  # Out of the gantry: the policy balances it.
        model = self.model = s.model
        self.data = s.data
        self.mass = float(np.sum(model.body_mass))
        self.weight = self.mass * float(np.linalg.norm(model.opt.gravity))
        self.to_robot = np.asarray(self.policy.to_robot, float)
        self.touch = [int(model.sensor_adr[model.sensor(f.touch).id]) for f in s.info.feet]
        self.soles = [model.site(f.site).id for f in s.info.feet]
        self.shocks = [
            Shock(info.name, info.position, tid, info.stiffness, info.damping, info.free_length, info.length_min, info.length_max)
            for info, tid in s.struts
        ]
        types = self.profile.actuator_types
        motor_type = {m.joint: m.type for m in s.info.motors}
        self.motors = []
        for a, joint in enumerate(s.actuator_joints):
            if joint not in motor_type:
                continue
            kind = motor_type[joint]
            self.motors.append(
                Motor(joint, kind, a, int(s.actuator_dofs[a]), float(types[kind].get("peak_torque", 0.0)), types[kind].get("no_load_speed"))
            )
        self.limit_tendon = int(mujoco.mjtConstraint.mjCNSTR_LIMIT_TENDON)
        free = next(j for j in range(model.njnt) if int(model.jnt_type[j]) == mujoco.mjtJoint.mjJNT_FREE.value)
        self.free_dof = int(model.jnt_dofadr[free])
        self.window: Window | None = None
        self.last_height = 0.0
        self.reset()

    # Driving.

    def begin(self) -> None:
        """Start a new trial's extremes."""
        self.shocks = [s.fresh() for s in self.shocks]
        self.motors = [m.fresh() for m in self.motors]

    def keep(self, phase: str, survived: bool) -> None:
        """End a trial of the kind ``phase``: its extremes count only if the robot came through standing."""
        tally = self.kept.setdefault(phase, Tally([s.fresh() for s in self.shocks], [m.fresh() for m in self.motors]))
        tally.trials += 1
        if survived:
            for mine, theirs in zip(tally.shocks, self.shocks, strict=True):
                mine.merge(theirs)
            for mine, theirs in zip(tally.motors, self.motors, strict=True):
                mine.merge(theirs)
        else:
            tally.falls += 1
        self.begin()

    def overall(self) -> Tally:
        """Every kept trial together."""
        total = Tally([s.fresh() for s in self.shocks], [m.fresh() for m in self.motors])
        for tally in self.kept.values():
            total.trials += tally.trials
            total.falls += tally.falls
            for mine, theirs in zip(total.shocks, tally.shocks, strict=True):
                mine.merge(theirs)
            for mine, theirs in zip(total.motors, tally.motors, strict=True):
                mine.merge(theirs)
        return total

    def reset(self, command: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        self.begin()
        self.session.command = np.asarray(command, float)
        self.session.reset()
        self.session.message = ""
        self.yaw = self._heading_angle()
        self.max_tilt = 0.0
        self.run(SETTLE)
        self.stand_com = float(self.data.subtree_com[self.session.base][2])

    def command(self, forward: float, left: float = 0.0, turn: float = 0.0) -> None:
        self.session.set_command(forward, left, turn)

    def shove(self, direction: float, speed: float) -> None:
        """Push the torso so the whole robot gains ``speed`` m/s, ``direction`` radians left of its heading."""
        heading = self._heading_angle()
        force = self.mass * speed / SHOVE_TIME
        angle = heading + direction
        self.session.push_robot((force * math.cos(angle), force * math.sin(angle), 0.0), SHOVE_TIME)

    def run(self, seconds: float) -> bool:
        """Step for ``seconds``; False as soon as the robot falls (or the physics gives way)."""
        for _ in range(max(1, round(seconds / TIMESTEP))):
            self.session._step()
            if self.session.message:  # The session reset itself: the physics went unstable.
                return False
            self.yaw += self._wrap(self._heading_angle() - self.yaw)
            self._record()
            if self.window is not None:
                self._steadiness(self.window)
            if self.fallen():
                return False
        return True

    # Reading.

    def tilt(self) -> float:
        up = self.data.xmat[self.session.base].reshape(3, 3) @ self.to_robot[2]
        return math.degrees(math.acos(max(-1.0, min(1.0, float(up[2])))))

    def fallen(self) -> bool:
        tilt = self.tilt()
        self.max_tilt = max(self.max_tilt, tilt)
        com = float(self.data.subtree_com[self.session.base][2])
        return tilt > FALL_TILT or (hasattr(self, "stand_com") and com < FALL_RATIO * self.stand_com)

    def airborne(self) -> bool:
        return all(self.data.sensordata[adr] < CONTACT for adr in self.touch)

    def foot_load(self) -> float:
        """Force on the feet, in body weights."""
        return float(sum(self.data.sensordata[adr] for adr in self.touch)) / self.weight

    def sole_height(self) -> float:
        """The lower sole's height above the ground."""
        return float(min(self.data.site_xpos[s][2] for s in self.soles))

    def start(self) -> Window:
        forward, left = self._axes()
        self.window = Window(float(self.data.time), self.data.subtree_com[self.session.base].copy(), self.yaw, forward, left)
        self.last_height = float(self.window.com[2])
        return self.window

    def since(self, w: Window) -> dict:
        """Since ``w``: average speeds in the robot's own frame (forward, left, turning), and how
        steady the torso was: sway (RMS pitch and roll about their mean, degrees), wobble (RMS
        pitch and roll rate, deg/s) and bounce (RMS vertical speed of the centre of mass, m/s)."""
        dt = max(1e-9, float(self.data.time) - w.t)
        moved = self.data.subtree_com[self.session.base][:2] - w.com[:2]
        forward, left = self._axes()
        fwd = w.forward + forward
        lft = w.left + left
        fwd /= max(1e-9, np.linalg.norm(fwd))
        lft /= max(1e-9, np.linalg.norm(lft))
        n = max(1, w.steps)
        spread = np.maximum(w.lean_squares / n - (w.lean / n) ** 2, 0.0)
        return {
            "forward": float(moved @ fwd) / dt,
            "left": float(moved @ lft) / dt,
            "turn": (self.yaw - w.yaw) / dt,
            "sway": float(np.sqrt(spread.sum())),
            "wobble": math.sqrt(w.rate_squares / n),
            "bounce": math.sqrt(w.bounce_squares / n),
        }

    def _steadiness(self, w: Window) -> None:
        rotation = self.data.xmat[self.session.base].reshape(3, 3)
        down = self.to_robot @ (rotation.T @ np.array([0.0, 0.0, -1.0]))  # Gravity, in the robot's frame.
        lean = np.degrees([math.atan2(down[0], -down[2]), math.atan2(down[1], -down[2])])
        rate = np.degrees(self.to_robot @ self.data.qvel[self.free_dof + 3 : self.free_dof + 6])[:2]
        height = float(self.data.subtree_com[self.session.base][2])
        rise = (height - self.last_height) / TIMESTEP
        self.last_height = height
        w.steps += 1
        w.lean += lean
        w.lean_squares += lean * lean
        w.rate_squares += float(rate @ rate)
        w.bounce_squares += rise * rise

    def _axes(self) -> tuple[np.ndarray, np.ndarray]:
        rotation = self.data.xmat[self.session.base].reshape(3, 3)
        forward = (rotation @ self.to_robot[0])[:2]
        left = (rotation @ self.to_robot[1])[:2]
        return forward / max(1e-9, np.linalg.norm(forward)), left / max(1e-9, np.linalg.norm(left))

    def _heading_angle(self) -> float:
        forward, _ = self._axes()
        return math.atan2(forward[1], forward[0])

    @staticmethod
    def _wrap(angle: float) -> float:
        return (angle + math.pi) % (2 * math.pi) - math.pi

    def _record(self) -> None:
        data = self.data
        stop_force: dict[int, float] = {}
        if self.shocks:
            for i in range(data.nefc):
                if data.efc_type[i] == self.limit_tendon:
                    tendon = int(data.efc_id[i])
                    stop_force[tendon] = stop_force.get(tendon, 0.0) + abs(float(data.efc_force[i]))
        for shock in self.shocks:
            length = float(data.ten_length[shock.tendon])
            speed = float(data.ten_velocity[shock.tendon])
            spring = shock.stiffness * (shock.free_length - length)
            force = spring - shock.damping * speed  # Pushing the mounts apart; the damper resists motion.
            shock.shortest = min(shock.shortest, length)
            shock.longest = max(shock.longest, length)
            shock.fastest = max(shock.fastest, abs(speed))
            shock.spring_peak = max(shock.spring_peak, spring)
            shock.force_peak = max(shock.force_peak, force)
            shock.stop_force_peak = max(shock.stop_force_peak, stop_force.get(shock.tendon, 0.0))
            shock.on_stop += int(length <= shock.low + AT_STOP or length >= shock.high - AT_STOP)
            shock.steps += 1
        forcerange = self.model.actuator_forcerange
        for motor in self.motors:
            torque = float(data.actuator_force[motor.actuator])
            available = max(abs(float(forcerange[motor.actuator, 0])), abs(float(forcerange[motor.actuator, 1])), 1e-9)
            motor.torque_peak = max(motor.torque_peak, abs(torque))
            motor.torque_squares += torque * torque
            motor.speed_peak = max(motor.speed_peak, abs(float(data.qvel[motor.dof])))
            motor.saturated += int(abs(torque) >= SATURATED * available)
            motor.steps += 1


def _walking(bench: Bench, rng: np.random.Generator) -> dict:
    commands = bench.policy.commands
    top = commands.forward[1]
    out: dict = {"commands": commands.to_dict()}

    # A ladder of forward speeds in one go: 2 s to reach each, 2.5 s measured.
    ladder = []
    bench.reset()
    for asked in sorted({*(v for v in LADDER if v < top - 1e-6), top}):
        bench.command(asked)
        bench.max_tilt = 0.0  # Each speed's own.
        standing = bench.run(2.0)
        window = bench.start()
        standing = standing and bench.run(2.5)
        got = bench.since(window)
        ladder.append({"asked": asked, **{k: round(v, 3) for k, v in got.items()}, "max_tilt": round(bench.max_tilt, 1), "fell": not standing})
        bench.keep("moving", standing)
        if not standing:
            break
    out["forward"] = ladder

    # Backward, sideways, turning and a curve, each from standing.
    moves = {
        "backward": (commands.forward[0], 0.0, 0.0),
        "left": (0.0, commands.sideways, 0.0),
        "right": (0.0, -commands.sideways, 0.0),
        "turn_left": (0.0, 0.0, commands.turning),
        "turn_right": (0.0, 0.0, -commands.turning),
        "curve": (min(0.6, top), 0.0, min(0.6, commands.turning)),
    }
    for name, asked in moves.items():
        bench.reset()
        bench.command(*asked)
        standing = bench.run(2.0)
        window = bench.start()
        standing = standing and bench.run(3.0)
        got = bench.since(window)
        out[name] = {"asked": list(asked), **{k: round(v, 3) for k, v in got.items()}, "fell": not standing}
        bench.keep("moving", standing)

    out["shoves"] = {
        "standing": _shoves(bench, (0.0, 0.0, 0.0)),
        "walking": _shoves(bench, (min(0.5, top), 0.0, 0.0)),
    }

    # Roaming: a new random command every 3 s for a minute.
    bench.reset()
    falls, errors = 0, []
    for _ in range(int(ROAM / 3.0)):
        asked = (
            float(rng.uniform(commands.forward[0], top)),
            float(rng.uniform(-commands.sideways, commands.sideways)) * float(rng.random() < 0.5),
            float(rng.uniform(-commands.turning, commands.turning)) * float(rng.random() < 0.5),
        )
        bench.command(*asked)
        standing = bench.run(1.5)
        window = bench.start()
        standing = standing and bench.run(1.5)
        got = bench.since(window)
        errors.append(math.hypot(got["forward"] - asked[0], got["left"] - asked[1]))
        bench.keep("moving", standing)
        if not standing:
            falls += 1
            bench.reset()
    out["roam"] = {"seconds": ROAM, "falls": falls, "speed_error": round(float(np.mean(errors)), 3)}
    return out


def _shoves(bench: Bench, command: tuple[float, float, float], strengths: tuple[float, ...] | None = None, directions: int | None = None) -> dict:
    """Shove from every side at growing strength; how hard a shove it takes from each."""
    strengths = SHOVES if strengths is None else strengths
    directions = DIRECTIONS if directions is None else directions
    held: list[list[bool]] = []
    for speed in strengths:
        row = []
        for k in range(directions):
            bench.reset(command)
            bench.run(0.5)
            bench.shove(2 * math.pi * k / directions, speed)
            held_it = bench.run(3.0)
            bench.keep("shoves", held_it)
            row.append(held_it)
        held.append(row)
    every = [s for s, row in zip(strengths, held, strict=True) if all(row)]
    some = [s for s, row in zip(strengths, held, strict=True) if any(row)]
    return {
        "speeds": list(strengths),
        "directions": directions,
        "held": held,
        "every_side_up_to": max(every) if every else 0.0,
        "some_sides_up_to": max(some) if some else 0.0,
    }


def _jumping(bench: Bench) -> dict:
    out: dict = {}
    bench.reset()
    out["stands"] = bench.run(STAND_TIME)
    bench.keep("standing", out["stands"])
    out["shoves"] = _shoves(bench, (0.0, 0.0, 0.0), JUMP_SHOVES, JUMP_DIRECTIONS)

    jumps = []
    bench.reset()
    rest_sole = bench.sole_height()
    for _ in range(JUMPS):
        bench.run(1.0)
        bench.keep("standing", True)  # The rest before a jump.
        rest_com = float(bench.data.subtree_com[bench.session.base][2])
        bench.session.jump()
        bench.max_tilt = 0.0
        top_com, top_sole, landing = rest_com, rest_sole, 0.0
        flight = longest = 0  # Steps in the air: this flight, the longest.
        touchdown = None  # Step the feet came down after the longest flight.
        standing = True
        for step in range(round(2.5 / TIMESTEP)):
            standing = bench.run(TIMESTEP)
            top_com = max(top_com, float(bench.data.subtree_com[bench.session.base][2]))
            if bench.airborne():
                flight += 1
                top_sole = max(top_sole, bench.sole_height())
            elif flight:
                if flight > longest:
                    longest, touchdown = flight, step
                flight = 0
            if touchdown is not None and step - touchdown < round(0.3 / TIMESTEP):
                landing = max(landing, bench.foot_load())  # The hardest hit in the 0.3 s after landing.
            if not standing:
                break
        longest = max(longest, flight)
        bench.keep("jumping", standing)
        jumps.append(
            {
                "rise_cm": round((top_com - rest_com) * 100, 2),
                "clearance_cm": round((top_sole - rest_sole) * 100, 2),
                "airborne_ms": round(longest * TIMESTEP * 1000),
                "landing_load": round(landing, 2),
                "max_tilt": round(bench.max_tilt, 1),
                "landed": standing,
            }
        )
        if not standing:
            bench.reset()
            rest_sole = bench.sole_height()
    out["jumps"] = jumps
    landed = [j for j in jumps if j["landed"]]
    out["landed"] = f"{len(landed)}/{len(jumps)}"
    for key in ("rise_cm", "clearance_cm", "airborne_ms"):
        values = [j[key] for j in jumps]
        out[f"{key}_median"] = float(np.median(values))
        out[f"{key}_best"] = float(np.max(values))
    return out


def evaluate(policy: Policy, profile: RobotProfile, seed: int = 0) -> dict:
    """Try ``policy`` on ``profile``; everything measured, as plain numbers."""
    bench = Bench(profile, policy)
    rng = np.random.default_rng(seed)
    report: dict = {
        "policy": policy.name,
        "robot": policy.robot,
        "skill": policy.skill,
        "design": dict(policy.design),
        "mass": round(bench.mass, 3),
        "timestep": TIMESTEP,
    }
    if policy.commands is not None:
        report["walking"] = _walking(bench, rng)
    elif bench.session.runner is not None and bench.session.runner.can_jump:
        report["jumping"] = _jumping(bench)
    else:
        bench.reset()
        stands = bench.run(STAND_TIME)
        bench.keep("standing", stands)
        report["standing"] = {"stands": stands, "shoves": _shoves(bench, (0.0, 0.0, 0.0))}
    report["phases"] = {phase: bench.kept[phase].summary(phase) for phase in PHASES if phase in bench.kept}
    overall = bench.overall()
    report["shocks"] = [s.summary() for s in overall.shocks]
    report["motors"] = [m.summary() for m in overall.motors]
    return report


def describe(report: dict) -> str:
    """The report in plain words, for a terminal."""
    lines = [f"{report['policy']} ({report['skill']}) on {report['robot']}, {report['mass']} kg"]
    walking = report.get("walking")
    if walking:
        lines.append("Forward speed, asked -> reached:")
        for row in walking["forward"]:
            lines.append(
                f"  {row['asked']:.1f} -> {row['forward']:.2f} m/s (drift {row['left']:+.2f} m/s, sway {row['sway']:.1f} deg, wobble {row['wobble']:.0f} deg/s, "
                f"bounce {row['bounce']:.3f} m/s, tilt up to {row['max_tilt']:.0f} deg){' FELL' if row['fell'] else ''}"
            )
        for name in ("backward", "left", "right", "turn_left", "turn_right", "curve"):
            row = walking[name]
            lines.append(
                f"  {name:<10} asked {row['asked']} -> forward {row['forward']:+.2f}, left {row['left']:+.2f} m/s, turning {row['turn']:+.2f} rad/s{' FELL' if row['fell'] else ''}"
            )
        for name, shoves in walking["shoves"].items():
            lines.append(f"Shoves while {name}: recovers from every side up to {shoves['every_side_up_to']} m/s, some sides up to {shoves['some_sides_up_to']} m/s")
        roam = walking["roam"]
        lines.append(f"Roaming {roam['seconds']:.0f} s on random commands: {roam['falls']} falls, speed error {roam['speed_error']} m/s")
    jumping = report.get("jumping")
    if jumping:
        lines.append(f"Stands {STAND_TIME:.0f} s: {jumping['stands']}; shoves standing: every side up to {jumping['shoves']['every_side_up_to']} m/s")
        lines.append(
            f"Jumps: landed {jumping['landed']}; centre of mass rises {jumping['rise_cm_median']} cm (best {jumping['rise_cm_best']}), "
            f"feet clear {jumping['clearance_cm_median']} cm (best {jumping['clearance_cm_best']}), "
            f"in the air {jumping['airborne_ms_median']:.0f} ms (best {jumping['airborne_ms_best']:.0f})"
        )
    standing = report.get("standing")
    if standing:
        lines.append(f"Stands {STAND_TIME:.0f} s: {standing['stands']}; shoves: every side up to {standing['shoves']['every_side_up_to']} m/s")
    for phase in report.get("phases", {}).values():
        lines.append(f"Shock absorbers, {phase['label'].lower()} ({phase['trials'] - phase['falls']} of {phase['trials']} trials kept; falls left out):")
        for s in phase["shocks"]:
            lines.append(
                f"  {s['name']:<14} used {s['used_mm']:5.1f} of {s['stroke_mm']:.0f} mm (closest {s['closest_to_bottom_mm']:.1f} mm to bottom, {s['closest_to_top_mm']:.1f} to top, "
                f"on a stop {100 * s['on_stop_share']:.2f}% of the time), shaft up to {s['shaft_speed_peak']:.2f} m/s, "
                f"spring up to {s['spring_force_peak']:.0f} N, with damper {s['force_peak']:.0f} N, stops up to {s['stop_force_peak']:.0f} N"
            )
    lines.append("Motors, every kept trial:")
    for m in report["motors"]:
        lines.append(
            f"  {m['joint']:<22} {m['type']:<8} peak {m['torque_peak']:6.1f} of {m['rating']:.1f} N*m, rms {m['torque_rms']:5.1f}, "
            f"at the limit {100 * m['saturated_share']:.1f}%, speed up to {m['speed_peak']:.1f}"
            + (f" of {m['no_load_speed']:.1f} rad/s" if m["no_load_speed"] else " rad/s")
        )
    return "\n".join(lines)
