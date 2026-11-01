// The full-bleed stage: the live robot, or a training run's practice robots.

import { effect, useSignalEffect } from '@preact/signals';
import { useEffect, useRef } from 'preact/hooks';
import { jump, send, swarmBlocked } from '../lib/live';
import { activeRun, design, live, runLive, selection, simState, viewMode } from '../lib/store';
import type { RobotAssets } from '../lib/robot';
import { theme } from '../lib/theme';
import type { SwarmMessage } from '../lib/types';
import { MainView, type Pose } from '../three/viewport';
import { Icon } from './icons';
import { Action, Choice } from './ui';

interface Clip {
  data: Float32Array;
  frames: number;
  robots: number;
  nq: number;
  dt: number;
  designs: Record<string, number>[];
}

function decodeSwarm(message: SwarmMessage, keys: string[]): Clip {
  const bytes = Uint8Array.from(atob(message.qpos), (c) => c.charCodeAt(0));
  const data = new Float32Array(bytes.buffer);
  const [frames, robots, nq] = message.shape;
  const designs = (message.designs ?? []).map((vector) => Object.fromEntries(keys.map((k, i) => [k, vector[i]])));
  return { data, frames, robots, nq, dt: message.dt, designs };
}

export function Viewport({ assets }: { assets: RobotAssets }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<MainView | null>(null);
  const meta = assets.meta;
  const keys = Object.keys(meta.robot.design);

  useEffect(() => {
    if (!canvas.current) return;
    const view = new MainView(canvas.current, assets);
    viewRef.current = view;
    const unfollow = effect(() => view.setTheme(theme.value)); // Light or dark, the stage follows.
    view.onPick = (body) => {
      const link = meta.bodies[body].link;
      const joint = meta.joints.find((j) => j.body === link && (j.motor || j.passive));
      selection.value = joint ? { kind: 'joint', id: joint.id } : { kind: 'body', id: body };
    };
    view.onPush = (force) => send({ type: 'sim.push', force, seconds: 0.2 });
    let clip: Clip | null = null;
    let clipSource: SwarmMessage | undefined;
    let clipStart = performance.now();
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      let poses: Pose[];
      if (viewMode.value === 'swarm') {
        const run = activeRun.value ? runLive.value[activeRun.value] : undefined;
        if (run?.swarm && run.swarm !== clipSource) {
          clipSource = run.swarm;
          clip = decodeSwarm(run.swarm, keys);
          clipStart = performance.now();
        }
        if (clip) {
          const c = clip;
          const index = Math.floor((performance.now() - clipStart) / 1000 / c.dt) % c.frames;
          poses = Array.from({ length: c.robots }, (_, r) => ({
            qpos: c.data.subarray((index * c.robots + r) * c.nq, (index * c.robots + r + 1) * c.nq),
            design: c.designs[r] ?? design.value,
          }));
        } else {
          poses = [{ qpos: live.frame?.qpos ?? meta.stand, design: design.value }];
        }
        view.setCom(null);
        view.setCommand(null, 0);
      } else {
        const frame = live.frame;
        poses = [{ qpos: frame?.qpos ?? meta.stand, design: design.value }];
        view.setCom(frame?.com ?? null);
        view.setCommand(frame && frame.mode === 'policy' ? frame.command : null, view.heading());
      }
      view.setPoses(poses);
      view.setFollow(viewMode.value === 'live');
      view.frame();
    };
    raf = requestAnimationFrame(loop);
    return () => {
      unfollow();
      cancelAnimationFrame(raf);
      view.dispose();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets]);

  // The selected link glows red in the main view too.
  useSignalEffect(() => {
    const sel = selection.value;
    const view = viewRef.current;
    if (!view) return;
    if (!sel) {
      view.setHighlight([]);
      return;
    }
    const link = sel.kind === 'joint' ? meta.joints[sel.id].body : meta.bodies[sel.id].link;
    const members = meta.links.find((l) => l.body === link)?.members ?? [link];
    view.setHighlight(members);
  });

  useSignalEffect(() => {
    viewMode.value;
    window.setTimeout(() => viewRef.current?.frameRobot(), 50);
  });

  const state = simState.value;
  const run = activeRun.value ? runLive.value[activeRun.value] : undefined;
  return (
    <div class="stage">
      <canvas ref={canvas} class="stage-canvas" aria-label="Live view of the robot. Drag to orbit, scroll to zoom, double-click to frame it, shift-drag on the robot to push it." />
      <div class="stage-tools" role="toolbar" aria-label="Simulation">
        <button class="btn ghost small" type="button" onClick={() => send({ type: 'sim.play', playing: !state.playing })} aria-label={state.playing ? 'Pause the simulation' : 'Run the simulation'}>
          <Icon name={state.playing ? 'pause' : 'play'} />
          {state.playing ? 'Pause' : 'Run'}
        </button>
        <button class="btn ghost small" type="button" onClick={() => send({ type: 'sim.reset' })}>
          <Icon name="reset" />
          Reset
        </button>
        <button
          class="btn ghost small"
          type="button"
          aria-pressed={state.gantry}
          onClick={() => send({ type: 'sim.gantry', on: !state.gantry })}
          title={
            state.gantry
              ? 'Let the robot out of the gantry'
              : state.standsAlone
                ? 'Hang the robot in a gantry'
                : 'This robot needs a policy to balance: hang it in a gantry until one runs'
          }
        >
          <Icon name="gantry" />
          Gantry
        </button>
        {state.mode === 'policy' && viewMode.value === 'live' ? (
          <Action
            class="btn ghost small"
            pressed={state.jumping}
            onClick={jump}
            blocked={state.canJump ? state.jumping && 'Already jumping: wait for it to land.' : `${state.policy} never learned to jump: watch a jump policy to jump.`}
            title="Jump (J)"
          >
            <Icon name="jump" />
            {state.jumping ? 'Jumping' : 'Jump'}
          </Action>
        ) : null}
        <Choice
          label="What the stage shows"
          value={viewMode.value}
          options={[
            { value: 'live', label: 'Live robot' },
            { value: 'swarm', label: 'Practice robots', blocked: viewMode.value !== 'swarm' && swarmBlocked() },
          ]}
          onChange={(v) => (viewMode.value = v)}
        />
      </div>
      <p class="stage-readout num" aria-live="off">
        {viewMode.value === 'swarm'
          ? run?.swarm
            ? `${run.swarm.shape[1]} practice robots of run ${activeRun.value}`
            : 'Start or pick a training run to watch its practice robots'
          : `${modeLabel(state.mode, state.policy)}${state.gantry ? ' in the gantry' : ''} · tilt ${state.tilt.toFixed(1)}° · ${state.t.toFixed(1)} s`}
      </p>
      {state.message ? <p class="stage-message" role="status">{state.message}</p> : null}
    </div>
  );
}

function modeLabel(mode: string, policy: string): string {
  switch (mode) {
    case 'policy':
      return `Policy ${policy}`;
    case 'pose':
      return 'Holding a pose';
    case 'limp':
      return 'Motors off';
    case 'mirror':
      return 'Showing the real robot';
    default:
      return 'Standing';
  }
}
