// Application state. Signals for what the interface shows; plain mutable
// holders for what changes 30 times a second (frames), which the 3D views read
// directly without re-rendering components.

import { computed, signal } from '@preact/signals';
import type {
  CodesignReport,
  Frame,
  HardwareStatus,
  Meta,
  ProgressMessage,
  RobotCard,
  RunSummary,
  SwarmMessage,
} from './types';

export type Section = 'hangar' | 'train' | 'design' | 'connect' | 'deploy' | 'robot';

export interface PlatformStatus {
  version: string;
  os: string;
  machine: string;
  python: string;
  mujoco: string;
  cpus: number;
  gpu: { ready: boolean; problem: string | null; name: string | null; installed: boolean };
  robot: string;
  can: string | null;
}

export interface RunLive {
  progress?: ProgressMessage;
  history: ProgressMessage[];
  codesign?: CodesignReport;
  codesignHistory: CodesignReport[];
  swarm?: SwarmMessage;
  state?: string;
  started?: { total: number; robots: number; rewards: Record<string, string>; design_keys: string[]; codesign: boolean };
  error?: string;
}

export const status = signal<PlatformStatus | null>(null);
export const meta = signal<Meta | null>(null);
export const loading = signal<string>('Connecting to Isla Studio');
export const connected = signal(false);
export const section = signal<Section>('hangar');
/** The robot loaded in Isla Studio (the server's), as last announced. */
export const activeRobot = signal<string | null>(null);
/** The hangar's robots, and the bay in view: a robot's id or the import bay. */
export const robots = signal<RobotCard[] | null>(null);
export const hangarPick = signal<string | null>(null);
export const design = signal<Record<string, number>>({});
export const hardware = signal<HardwareStatus | null>(null);
export const hardwareLog = signal<string[]>([]);
export const runs = signal<RunSummary[]>([]);
/** Whether the list of runs has come from the server yet: until then, no runs means "not known". */
export const runsLoaded = signal(false);
export const activeRun = signal<string | null>(null);
export const runLive = signal<Record<string, RunLive>>({});
export const selection = signal<{ kind: 'joint' | 'body'; id: number } | null>(null);
export const notice = signal<{ text: string; tone: 'info' | 'error' } | null>(null);
export const viewMode = signal<'live' | 'swarm'>('live');
export const anatomySource = signal<'sim' | 'robot'>('sim');
export const simState = signal<{
  mode: string;
  gantry: boolean;
  standsAlone: boolean;
  steers: boolean;
  canJump: boolean;
  jumping: boolean;
  playing: boolean;
  policy: string;
  tilt: number;
  t: number;
  message: string;
}>({
  mode: 'stand',
  gantry: false,
  standsAlone: true,
  steers: false,
  canJump: false,
  jumping: false,
  playing: true,
  policy: '',
  tilt: 0,
  t: 0,
  message: '',
});

/** The latest frame, kept out of signals: the 3D views read it every animation frame. */
export const live: { frame: Frame | null; frameAt: number } = { frame: null, frameAt: 0 };

export const motorJoints = computed(() => meta.value?.joints.filter((j) => j.motor) ?? []);

/** Training runs of the loaded robot (under its id or a name it had before). */
export const robotRuns = computed(() => {
  const robot = meta.value?.robot;
  if (!robot) return [];
  const names = new Set([robot.id, ...(robot.aliases ?? [])]);
  return runs.value.filter((r) => names.has(r.robot));
});

/** Policies being tried in full physics, by run, as the live connection reports them. */
export const tests = signal<Record<string, { state: 'running' | 'done' | 'error'; message?: string }>>({});

export function say(text: string, tone: 'info' | 'error' = 'info'): void {
  // The server's messages come without a full stop; every notice reads as a sentence.
  const sentence = /[.!?…]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`;
  notice.value = { text: sentence, tone };
  const shown = notice.value;
  // Long enough to read: about 60 ms a character, never under 3.5 s.
  window.setTimeout(() => {
    if (notice.value === shown) notice.value = null;
  }, Math.max(tone === 'error' ? 7000 : 3500, sentence.length * 60));
}

export function updateRun(name: string, change: (run: RunLive) => RunLive): void {
  const current = runLive.value[name] ?? { history: [], codesignHistory: [] };
  runLive.value = { ...runLive.value, [name]: change(current) };
}
