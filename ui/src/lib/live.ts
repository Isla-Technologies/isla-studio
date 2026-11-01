// The live connection: one WebSocket for frames, hardware status, training reports
// and quick commands. It reconnects on its own.

import {
  activeRobot,
  activeRun,
  connected,
  gpuInstalling,
  hardware,
  hardwareLog,
  live,
  runLive,
  runs,
  say,
  simState,
  status,
  tests,
  updateRun,
  type PlatformStatus,
} from './store';
import type { CodesignReport, Frame, HardwareStatus, ProgressMessage, SwarmMessage } from './types';

let socket: WebSocket | null = null;
let retry = 0;
const HISTORY = 4000;

export function connectLive(): void {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.onopen = () => {
    connected.value = true;
    retry = 0;
  };
  socket.onclose = () => {
    connected.value = false;
    socket = null;
    retry = Math.min(retry + 1, 6);
    window.setTimeout(connectLive, 400 * retry);
  };
  socket.onmessage = (event) => {
    let message: { type: string; [key: string]: unknown };
    try {
      message = JSON.parse(event.data as string);
    } catch {
      return;
    }
    handle(message);
  };
}

let lastSimState = 0;

function handle(message: { type: string; [key: string]: unknown }): void {
  switch (message.type) {
    case 'frame': {
      const frame = message as unknown as Frame;
      live.frame = frame;
      live.frameAt = performance.now();
      const now = performance.now();
      if (now - lastSimState > 200) {
        lastSimState = now;
        simState.value = {
          mode: frame.mode,
          gantry: frame.gantry,
          standsAlone: frame.stands_alone,
          steers: frame.steers,
          canJump: frame.can_jump,
          jumping: frame.jumping,
          playing: frame.playing,
          policy: frame.policy,
          tilt: frame.tilt,
          t: frame.t,
          message: frame.message,
        };
      }
      break;
    }
    case 'hello':
      if (message.hardware) hardware.value = message.hardware as HardwareStatus;
      if (message.robot) activeRobot.value = String(message.robot);
      break;
    case 'robot':
      live.frame = null; // Frames of the robot before do not fit this one.
      activeRobot.value = String(message.robot);
      break;
    case 'hardware': {
      const next = message as unknown as HardwareStatus;
      const before = hardware.value?.state;
      hardware.value = next;
      if (next.state === 'fault' && before && before !== 'fault') say(`Robot stopped: ${next.fault ?? 'emergency stop'}. Every motor is relaxed.`, 'error');
      break;
    }
    case 'hardware.log':
      hardwareLog.value = [...hardwareLog.value.slice(-199), String(message.line)];
      break;
    case 'train': {
      const run = String(message.run);
      const inner = message.message as { type: string; [key: string]: unknown };
      trainMessage(run, inner);
      break;
    }
    case 'train.state':
      updateRun(String(message.run), (r) => ({ ...r, state: String(message.state) }));
      break;
    case 'gpu.install':
      if (message.done) {
        const gpu = message.gpu as PlatformStatus['gpu'] | undefined;
        gpuInstalling.value = false;
        if (gpu && status.value) status.value = { ...status.value, gpu };
        if (message.code !== 0) say('Installing GPU support failed: see runs/gpu-install.log', 'error');
        else if (gpu?.ready) say(`NVIDIA GPU support installed: new runs train on the ${gpu.name}.`);
        else say(`NVIDIA GPU support installed, but the GPU cannot be used yet: ${gpu?.problem ?? 'see runs/gpu-install.log'}`, 'error');
      }
      break;
    case 'design':
      break;
    case 'evaluation': {
      const run = String(message.run);
      const state = message.state as 'running' | 'done' | 'error';
      tests.value = { ...tests.value, [run]: { state, message: message.message ? String(message.message) : undefined } };
      if (state === 'done') say(`${run} is tested: the results are in Deploy.`);
      if (state === 'error') say(`Testing ${run} failed: ${String(message.message)}`, 'error');
      break;
    }
    case 'error':
      say(String(message.message), 'error');
      break;
  }
}

export function trainMessage(run: string, inner: { type: string; [key: string]: unknown }): void {
  switch (inner.type) {
    case 'progress': {
      const progress = inner as unknown as ProgressMessage;
      updateRun(run, (r) => {
        const last = r.history[r.history.length - 1];
        const history = last && last.iteration >= progress.iteration ? r.history : [...r.history.slice(-HISTORY), progress];
        return { ...r, progress, history, state: 'running' };
      });
      break;
    }
    case 'codesign': {
      const report = inner as unknown as CodesignReport;
      updateRun(run, (r) => ({
        ...r,
        codesign: report,
        codesignHistory: r.codesignHistory.some((h) => h.generation === report.generation) ? r.codesignHistory : [...r.codesignHistory, report],
      }));
      break;
    }
    case 'swarm':
      updateRun(run, (r) => ({ ...r, swarm: inner as unknown as SwarmMessage }));
      break;
    case 'started':
      updateRun(run, (r) => ({ ...r, started: inner as unknown as RunLiveStarted, state: 'running' }));
      break;
    case 'finished':
    case 'stopped':
      updateRun(run, (r) => ({ ...r, state: inner.type }));
      break;
    case 'error':
      updateRun(run, (r) => ({ ...r, state: 'error', error: String(inner.message) }));
      say(`Training ${run} stopped with an error: ${String(inner.message)}`, 'error');
      break;
  }
}

type RunLiveStarted = { total: number; robots: number; rewards: Record<string, string>; design_keys: string[]; codesign: boolean };

export function send(message: Record<string, unknown>): void {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  else say('Not connected to the Isla Studio server: reconnecting. Try again in a moment.', 'error');
}

/** Speeds the keys and commands ask a walking policy for, in m/s and rad/s. */
export const STEER = { forward: 0.4, back: -0.25, side: 0.2, turn: 0.6, run: 1.5 };

/** Why the steering keys cannot drive the robot now, or null when they can. */
export function steerBlocked(): string | null {
  const sim = simState.value;
  if (sim.mode !== 'policy') return 'To steer, watch a walking policy first: Train, pick a walk or walk-and-run run, Watch policy.';
  if (!sim.steers) return `${sim.policy} does not take steering: watch a walk or walk-and-run policy to drive it with W A S D.`;
  return null;
}

/** Why the robot cannot jump now, or null when it can. */
export function jumpBlocked(): string | null {
  const sim = simState.value;
  if (sim.mode !== 'policy') return 'To jump, watch a jump policy first: Train, pick a jump run, Watch policy.';
  if (!sim.canJump) return `${sim.policy} never learned to jump: watch a jump policy to jump.`;
  if (sim.jumping) return 'Already jumping: wait for it to land.';
  return null;
}

export function jump(): void {
  const why = jumpBlocked();
  if (why) say(why);
  else send({ type: 'sim.jump' });
}

/** Why the practice robots cannot be shown, or null when the selected run has them. */
export function swarmBlocked(): string | null {
  const name = activeRun.value;
  if (!name) return 'Start or pick a training run in Train to watch its practice robots.';
  if (runLive.value[name]?.swarm) return null;
  const running = runLive.value[name]?.state === 'running' || runs.value.some((r) => r.name === name && r.running);
  return running ? 'The practice robots appear after the first round of practice.' : `Practice robots show while a run trains: continue ${name} or start a run in Train.`;
}

/** The emergency stop. It always goes out; a robot that is not there, or already stopped, is said. */
export function estop(): void {
  send({ type: 'hw.estop' });
  const state = hardware.value?.state ?? 'disconnected';
  if (state === 'disconnected') say('No robot is connected, so there is nothing to stop. Space pauses the simulation.');
  else if (state === 'fault') say('Already stopped: every motor is relaxed.');
}
