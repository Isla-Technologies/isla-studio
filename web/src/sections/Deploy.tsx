// Deploy: trained policies, tried in full physics, then run on the robot.

import { useEffect, useState } from 'preact/hooks';
import { api } from '../lib/api';
import { send } from '../lib/live';
import { compact, designValue, fixed } from '../lib/format';
import { activeRun, design, hardware, meta, robotRuns, runsLoaded, say, viewMode } from '../lib/store';
import { Icon } from '../components/icons';
import { Tested } from '../components/Tested';
import { Action, Empty, Status } from '../components/ui';
import { refreshRuns, watchHint } from './Train';

interface PolicyInfo {
  design: Record<string, number>;
  skill: string;
}

export function Deploy() {
  const m = meta.value!;
  const list = robotRuns.value.filter((r) => r.has_policy);
  const hw = hardware.value;
  // The policy picked here, else the newest: the list may arrive after the page opens.
  const [picked, setPicked] = useState<string | null>(null);
  const chosen = list.some((r) => r.name === picked) ? picked : (list[0]?.name ?? null);
  const [info, setInfo] = useState<PolicyInfo | null>(null);
  useEffect(() => {
    refreshRuns();
  }, []);
  useEffect(() => {
    setInfo(null);
    if (!chosen) return;
    api.get<PolicyInfo>(`/api/runs/${encodeURIComponent(chosen)}/policy.json`).then(setInfo).catch(() => setInfo(null));
  }, [chosen]);

  const built = m.robot.design_values;
  const mismatch = info
    ? Object.entries(info.design ?? {}).filter(([k, v]) => Math.abs((built[k] ?? v) - v) > (m.robot.design[k]?.step || 1e-6))
    : [];

  async function watch(): Promise<void> {
    if (!chosen) return;
    try {
      const result = await api.post<{ design: Record<string, number> }>('/api/sim/policy', { run: chosen });
      if (result.design) design.value = { ...design.value, ...result.design };
      viewMode.value = 'live';
      say(watchHint(chosen, info?.skill ?? list.find((r) => r.name === chosen)?.skill ?? ''));
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }

  async function runOnRobot(): Promise<void> {
    if (!chosen) return;
    try {
      await api.post('/api/hardware/mode', { mode: 'policy', run: chosen });
      say(`The robot is running ${chosen}. Esc stops it.`);
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }

  const armFirst =
    hw?.state === 'armed'
      ? null
      : hw?.state === 'fault'
        ? 'The robot is stopped: clear the stop in Connect, then arm it.'
        : hw?.state === 'connected'
          ? 'Arm the robot first, in Connect.'
          : 'Connect and arm the robot first, in Connect.';
  const running = hw?.state === 'armed' && hw.mode === 'policy' && hw.policy === chosen;

  if (!list.length) {
    return (
      <div class="section">
        <header class="section-head">
          <h1 class="t-headline">Deploy</h1>
        </header>
        {runsLoaded.value ? (
          <Empty title="No trained policies yet">Start a run in Train; its policy appears here as soon as it saves, about every 30 seconds.</Empty>
        ) : (
          <p class="t-meta" role="status">Loading the trained policies.</p>
        )}
      </div>
    );
  }

  return (
    <div class="section">
      <header class="section-head">
        <h1 class="t-headline">Deploy</h1>
      </header>
      <ul class="run-list" aria-label="Policies">
        {list.map((r) => (
          <li key={r.name}>
            <button type="button" class="run-row" aria-current={chosen === r.name} onClick={() => { setPicked(r.name); activeRun.value = r.name; }}>
              <span class="run-name">{r.name}</span>
              <span class="run-meta">
                <span>{r.skill}</span>
                <span class="num">{compact(r.steps)} moves</span>
                <span class="num">score {fixed(r.score, 2)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {chosen ? (
        <section class="block">
          <h2 class="block-title">{chosen}</h2>
          {info?.design && Object.keys(info.design).length ? (
            <p class="t-meta">
              Trained for: {Object.entries(info.design).map(([k, v]) => `${m.robot.design[k]?.label ?? k} ${designValue(v, m.robot.design[k]?.unit ?? '', m.robot.design[k]?.kind ?? '')}`).join(', ')}.
            </p>
          ) : null}
          {mismatch.length ? (
            <p class="verdict warn">
              <Icon name="alert" />
              This policy learned on a different build than the robot's saved design ({mismatch.map(([k]) => m.robot.design[k]?.label ?? k).join(', ')}). Build the robot to its design, or save that design, before running it.
            </p>
          ) : null}
          <div class="actions">
            <button class="btn" type="button" onClick={watch}>
              <Icon name="eye" /> Watch in simulation
            </button>
            <Action onClick={runOnRobot} blocked={armFirst} title="The robot runs the policy from its own readings">
              <Icon name="rocket" /> Run on robot
            </Action>
            {info?.skill === 'jump' ? (
              <Action
                class="btn ghost"
                onClick={() => send({ type: 'hw.jump' })}
                blocked={armFirst ?? (!running && 'Press Run on robot first: the robot jumps while it runs this policy.')}
              >
                <Icon name="jump" /> Jump
              </Action>
            ) : null}
            <a class="btn ghost" href={`/api/runs/${encodeURIComponent(chosen)}/policy.json`} download>
              <Icon name="download" /> Policy file
            </a>
          </div>
          <p class="t-meta">
            Robot: <Status tone={hw?.state === 'armed' ? 'busy' : hw?.state === 'connected' ? 'ok' : 'off'}>{hw?.state === 'armed' ? 'armed' : hw?.state === 'connected' ? 'connected, relaxed' : 'not connected'}</Status>
          </p>
        </section>
      ) : null}
      {chosen ? <Tested key={chosen} run={chosen} /> : null}
      <section class="block">
        <h2 class="block-title"><Icon name="cpu" /> On the robot's computer</h2>
        <p class="t-meta">For walking without a cable: the robot's computer (Jetson or Raspberry Pi, Linux) owns the CAN buses and the IMU. Copy this project there, then:</p>
        <pre class="code">python -m isla_rl.runtime --robot {m.robot.id}</pre>
        <p class="t-meta">Then choose "The robot's computer" in Connect with its address. Add <code>--practice</code> to rehearse with a simulated robot.</p>
      </section>
      <section class="block">
        <h2 class="block-title"><Icon name="shield" /> Before the first run on hardware</h2>
        <ol class="checklist">
          <li>Robot in a gantry, feet clear of the floor.</li>
          <li>Every motor answers, directions and zeros checked in Connect.</li>
          <li>Safety limits raised for policies: stiffness and torque shares near 1, top speed at least 3 rad/s.</li>
          <li>The policy's design matches the robot as built.</li>
          <li>A hardware emergency stop within reach; Esc stops it from here.</li>
        </ol>
      </section>
    </div>
  );
}
