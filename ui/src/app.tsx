// Isla Studio: the robot fills the screen, every tool floats over it.

import { useSignalEffect } from '@preact/signals';
import { useEffect, useState } from 'preact/hooks';
import { api, ApiError } from './lib/api';
import { connectLive, estop, jump, send, STEER, steerBlocked } from './lib/live';
import { loadRobot, type RobotAssets } from './lib/robot';
import {
  activeRobot,
  activeRun,
  connected,
  design,
  hangarPick,
  hardware,
  loading,
  meta,
  notice,
  robots,
  say,
  section,
  selection,
  simState,
  status,
  viewMode,
  type PlatformStatus,
  type Section,
} from './lib/store';
import { Anatomy } from './components/Anatomy';
import { ChartsPanel } from './components/ChartsPanel';
import { CommandBar } from './components/CommandBar';
import { Icon } from './components/icons';
import { IconButton, Segments } from './components/ui';
import { setTheme, theme } from './lib/theme';
import { Viewport } from './components/Viewport';
import { Connect } from './sections/Connect';
import { Deploy } from './sections/Deploy';
import { Design } from './sections/Design';
import { HangarSheet, HangarStage, Lineup } from './sections/Hangar';
import { Robot } from './sections/Robot';
import { Train } from './sections/Train';

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'hangar', label: 'Hangar' },
  { key: 'train', label: 'Train' },
  { key: 'design', label: 'Design' },
  { key: 'connect', label: 'Connect' },
  { key: 'deploy', label: 'Deploy' },
  { key: 'robot', label: 'Robot' },
];

export function App() {
  const [assets, setAssets] = useState<RobotAssets | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [panel, setPanel] = useState(true);
  const [switching, setSwitching] = useState<string | null>(null);

  useEffect(() => {
    connectLive();
    (async () => {
      try {
        status.value = await api.get<PlatformStatus>('/api/status');
        activeRobot.value = status.value.robot;
        const loaded = await loadRobot(status.value.robot, (step) => (loading.value = step));
        meta.value = loaded.meta;
        design.value = { ...loaded.meta.design_values };
        setAssets(loaded);
      } catch (error) {
        setFailure(error instanceof ApiError ? error.message : String(error));
      }
    })();
  }, []);

  // The address keeps the section, and the hangar's bay: #train, #hangar/luma_1.
  useEffect(() => {
    const read = () => {
      const [key, pick] = location.hash.slice(1).split('/');
      if (SECTIONS.some((s) => s.key === key)) section.value = key as Section;
      if (key === 'hangar' && pick) hangarPick.value = decodeURIComponent(pick);
    };
    read();
    window.addEventListener('hashchange', read);
    return () => window.removeEventListener('hashchange', read);
  }, []);
  useSignalEffect(() => {
    const pick = hangarPick.value;
    const hash = section.value === 'hangar' && pick ? `#hangar/${encodeURIComponent(pick)}` : `#${section.value}`;
    if (location.hash !== hash) history.replaceState(null, '', hash);
  });

  // Follow the server's robot: when the hangar loads another, the workspace is rebuilt around it.
  const wanted = activeRobot.value;
  useEffect(() => {
    if (!wanted || !assets || assets.meta.robot.id === wanted) return;
    let current = true;
    setSwitching(wanted);
    loadRobot(wanted)
      .then((loaded) => {
        if (!current) return;
        selection.value = null;
        activeRun.value = null;
        viewMode.value = 'live';
        meta.value = loaded.meta;
        design.value = { ...loaded.meta.design_values };
        if (status.value) status.value = { ...status.value, robot: wanted };
        setAssets(loaded);
        setSwitching(null);
      })
      .catch((error: Error) => {
        if (!current) return;
        setSwitching(null);
        say(`The robot could not be loaded: ${error.message}`, 'error');
      });
    return () => {
      current = false;
    };
  }, [wanted, assets]);

  // Keys work anywhere except while typing in a field. Esc is the emergency stop.
  useEffect(() => {
    const held = new Set<string>();
    // W walks, Shift with W runs: the policy keeps to the speeds it learned.
    const steer = () => {
      const f = (held.has('w') ? (held.has('shift') ? STEER.run : STEER.forward) : 0) + (held.has('s') ? STEER.back : 0);
      const l = (held.has('a') ? STEER.side : 0) - (held.has('d') ? STEER.side : 0);
      const t = (held.has('q') ? STEER.turn : 0) - (held.has('e') ? STEER.turn : 0);
      send({ type: 'sim.command', command: [f, l, t] });
    };
    const typing = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null;
      return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
    };
    const down = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        const hw = hardware.value;
        if (hw && (hw.state === 'armed' || hw.state === 'connected')) send({ type: 'hw.estop' });
        return;
      }
      if (typing(event) || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === 'shift') {
        if (!held.has('shift')) {
          held.add('shift');
          if (held.has('w')) steer();
        }
      } else if (key === ' ') {
        event.preventDefault();
        send({ type: 'sim.play', playing: !simState.value.playing });
      } else if (key === 'r') {
        send({ type: 'sim.reset' });
      } else if (['1', '2', '3', '4', '5', '6'].includes(key)) {
        section.value = SECTIONS[Number(key) - 1].key;
      } else if ('wasdqe'.includes(key) && key.length === 1) {
        if (held.has(key) || event.repeat) return;
        const why = steerBlocked();
        if (why) {
          say(why);
          return;
        }
        held.add(key);
        steer();
      } else if (key === 'j' && !event.repeat) {
        jump();
      }
    };
    const up = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if (held.delete(key) && (key !== 'shift' || held.has('w'))) steer();
    };
    // Keys let go in another window would leave the robot walking.
    const blur = () => {
      const walking = [...held].some((k) => k !== 'shift');
      held.clear();
      if (walking) steer();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
    };
  }, []);

  if (failure) {
    return (
      <div class="boot">
        <div class="wordmark">ISLA TECHNOLOGIES</div>
        <p class="t-title">Isla Studio could not start</p>
        <p class="t-meta">{failure}</p>
        <button class="btn" type="button" onClick={() => location.reload()}>Try again</button>
      </div>
    );
  }
  if (!assets) {
    return (
      <div class="boot" aria-busy="true">
        <div class="wordmark">ISLA TECHNOLOGIES</div>
        <p class="boot-app">ISLA STUDIO</p>
        <div class="boot-bar"><Segments count={12} done={loading.value === 'Preparing meshes' ? 8 : 3} current /></div>
        <p class="t-meta" role="status">{loading.value}</p>
      </div>
    );
  }

  const current = section.value;
  const hangar = current === 'hangar';
  return (
    <div class="app">
      <TopBar />
      <main class={`workspace${panel ? ' with-panel' : ''}${hangar ? ' in-hangar' : ''}`} key={assets.meta.robot.id}>
        {hangar ? <HangarStage panel={panel} /> : <Viewport assets={assets} />}
        {panel ? (
          <aside class="side hud-panel" aria-label={`${current} panel`}>
            <button class="side-fold btn ghost icon" type="button" onClick={() => setPanel(false)} aria-label="Fold the panel away" title="Fold the panel away">
              <Icon name="fold" />
            </button>
            {hangar ? <HangarSheet /> : null}
            {current === 'train' ? <Train /> : null}
            {current === 'design' ? <Design /> : null}
            {current === 'connect' ? <Connect /> : null}
            {current === 'deploy' ? <Deploy /> : null}
            {current === 'robot' ? <Robot /> : null}
          </aside>
        ) : (
          <button class="side-unfold btn ghost" type="button" onClick={() => setPanel(true)}>
            <Icon name="chevron" />
            {SECTIONS.find((s) => s.key === current)?.label}
          </button>
        )}
        {hangar ? (
          <Lineup />
        ) : (
          <div class="right-col">
            <Anatomy assets={assets} />
            {current === 'train' ? <ChartsPanel /> : null}
          </div>
        )}
        <CommandBar />
        {switching ? (
          <div class="switching" role="status">
            <Segments count={12} done={4} current />
            <span>Loading {robots.value?.find((c) => c.id === switching)?.name ?? switching} for training</span>
          </div>
        ) : null}
        {notice.value ? (
          <div class={`notice ${notice.value.tone}`} role={notice.value.tone === 'error' ? 'alert' : 'status'}>
            <Icon name={notice.value.tone === 'error' ? 'alert' : 'check'} />
            <span>{notice.value.text}</span>
          </div>
        ) : null}
      </main>
    </div>
  );
}

/** Light or dark: names the mode it switches to, and shows that mode's icon. */
function ThemeToggle() {
  const next = theme.value === 'dark' ? 'light' : 'dark';
  return <IconButton icon={next === 'light' ? 'sun' : 'moon'} label={`Switch to ${next} mode`} onClick={() => setTheme(next)} />;
}

function TopBar() {
  const m = meta.value!;
  const hw = hardware.value;
  const hwState = hw?.state ?? 'disconnected';
  const gpu = status.value?.gpu;
  return (
    <header class="topbar">
      <div class="brand">
        <span class="wordmark">ISLA TECHNOLOGIES</span>
        <span class="app-name">Isla Studio</span>
      </div>
      <span class="robot-readout num" title={`${m.robot.name} is loaded for training`}>
        {m.robot.name}
        <span class="details"> · {m.info.total_mass.toFixed(1)} kg · {m.joints.filter((j) => j.motor).length} motors</span>
      </span>
      <nav class="tabs" role="tablist" aria-label="Sections">
        {SECTIONS.map((s, i) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={section.value === s.key}
            class={section.value === s.key ? 'on' : ''}
            onClick={() => (section.value = s.key)}
            title={`${s.label} (${i + 1})`}
          >
            {s.label}
          </button>
        ))}
      </nav>
      <div class="links">
        <span class={`status ${connected.value ? 'ok' : 'fault'}`} title={connected.value ? 'Connected to the Isla Studio server' : 'Reconnecting to the server'}>
          {connected.value ? 'Sim' : 'Offline'}
        </span>
        <span class={`status ${hwState === 'armed' ? 'busy' : hwState === 'connected' ? 'ok' : hwState === 'fault' ? 'fault' : 'off'}`}>
          Robot {hwState === 'armed' ? 'armed' : hwState === 'connected' ? 'relaxed' : hwState === 'fault' ? 'stopped' : 'off'}
        </span>
        <span class={`status ${gpu?.ready ? 'ok' : 'off'}`} title={gpu?.ready ? gpu.name ?? '' : gpu?.problem ?? 'Processor'}>
          {gpu?.ready ? 'GPU' : 'CPU'}
        </span>
      </div>
      <ThemeToggle />
      <button class="estop" type="button" onClick={estop} title="Emergency stop: relaxes every motor of the connected robot (Esc)">
        <Icon name="stop" size={18} />
        E-stop
      </button>
    </header>
  );
}
