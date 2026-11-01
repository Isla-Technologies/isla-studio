// Train: start a run from a preset (or every option), follow it, watch it, use what it found.

import { useEffect, useState } from 'preact/hooks';
import { api, ApiError } from '../lib/api';
import { compact, designValue, duration, fixed, percent } from '../lib/format';
import { activeRun, design, gpuInstalling, meta, robotRuns, runLive, runs, runsLoaded, say, status, updateRun, viewMode } from '../lib/store';
import type { CodesignReport, ProgressMessage, RunSummary } from '../lib/types';
import { Icon } from '../components/icons';
import { DesignTracks, RewardBars } from '../components/charts';
import { Action, Readout, Segments, Status } from '../components/ui';

interface Catalogue {
  skills: { key: string; title: string; summary: string; detail: string; steps: number; episode: number; jumps: boolean; commands: unknown }[];
  rewards: { key: string; label: string; help: string }[];
  robustness: Record<string, number | number[]>;
  codesign: Record<string, unknown>;
  commands: { forward: number[]; sideways: number; turning: number };
}

let catalogue: Catalogue | null = null;
export async function loadCatalogue(): Promise<Catalogue> {
  if (!catalogue) catalogue = await api.get<Catalogue>('/api/skills');
  return catalogue;
}

export async function refreshRuns(): Promise<void> {
  try {
    runs.value = await api.get<RunSummary[]>('/api/runs');
    runsLoaded.value = true;
    const own = robotRuns.value;
    if (!activeRun.value && own.length) {
      const running = own.find((r) => r.running);
      activeRun.value = (running ?? own[0]).name;
    }
  } catch (error) {
    if (error instanceof ApiError) say(error.message, 'error');
  }
}

/** Fill a run's history from its files when it is first opened. */
export async function loadRun(name: string): Promise<void> {
  const detail = await api.get<{ progress: Record<string, number | string>[]; codesign: (CodesignReport & { candidates: unknown })[]; latest: Record<string, unknown>; summary: RunSummary }>(`/api/runs/${encodeURIComponent(name)}`);
  const history = detail.progress.map((row) => ({ type: 'progress', ...row, total: detail.summary.total, rewards: {} }) as unknown as ProgressMessage);
  const keys = Object.keys(meta.value?.robot.design ?? {});
  const codesignHistory = detail.codesign.map((entry) => {
    const candidates = (entry.candidates as unknown as number[][]).map((row) => ({
      design: row.slice(0, keys.length),
      score: row[keys.length] as number | null,
      seconds: row[keys.length + 1] as number,
      height: 0,
    }));
    return { ...entry, type: 'codesign', keys, candidates } as CodesignReport;
  });
  updateRun(name, (r) => {
    const latest = detail.latest as { progress?: ProgressMessage; codesign?: CodesignReport; swarm?: never };
    return {
      ...r,
      history: history.length >= r.history.length ? history : r.history,
      codesignHistory: codesignHistory.length >= r.codesignHistory.length ? codesignHistory : r.codesignHistory,
      progress: r.progress ?? latest.progress ?? history[history.length - 1],
      codesign: r.codesign ?? latest.codesign ?? codesignHistory[codesignHistory.length - 1],
      swarm: r.swarm ?? latest.swarm,
      state: detail.summary.running ? 'running' : detail.summary.status,
    };
  });
}

type Preset = 'move' | 'jump' | 'stand' | 'codesign';

/** What each preset trains, and how many robots for how many million moves (GPU, processor). */
const PRESETS: Record<Preset, { skill: string; search: boolean; gpu: [number, number]; cpu: [number, number] }> = {
  move: { skill: 'move', search: false, gpu: [8192, 400], cpu: [512, 60] },
  jump: { skill: 'jump', search: false, gpu: [4096, 200], cpu: [512, 40] },
  stand: { skill: 'stand', search: false, gpu: [4096, 30], cpu: [512, 15] },
  codesign: { skill: 'walk', search: true, gpu: [8192, 150], cpu: [1024, 60] },
};

/** Skills steered by speed commands, as opposed to standing or jumping in place. */
function steers(cat: Catalogue | null, skill: string): boolean {
  const known = cat?.skills.find((s) => s.key === skill);
  return known ? known.commands !== null : skill === 'walk' || skill === 'move';
}

/** What to try while watching a policy, by the skill it learned. */
export function watchHint(name: string, skill: string): string {
  if (skill === 'jump') return `Watching ${name} in full physics. Press J or Jump to jump; shift-drag pushes the robot.`;
  if (skill === 'walk' || skill === 'move') {
    return `Watching ${name} in full physics. Steer with W A S D, turn with Q and E${skill === 'move' ? ', hold Shift to run' : ''}.`;
  }
  return `Watching ${name} in full physics. Push the robot with shift-drag.`;
}

export function Train() {
  const [cat, setCat] = useState<Catalogue | null>(catalogue);
  const [composing, setComposing] = useState(false);
  const [preset, setPreset] = useState<Preset>('move');
  useEffect(() => {
    loadCatalogue().then(setCat).catch(() => undefined);
    refreshRuns();
    const id = window.setInterval(refreshRuns, 5000);
    // The command bar opens the composer on a preset.
    const compose = (event: Event) => {
      const asked = (event as CustomEvent<string>).detail;
      setPreset(asked in PRESETS ? (asked as Preset) : 'move');
      setComposing(true);
    };
    window.addEventListener('isla:compose', compose);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('isla:compose', compose);
    };
  }, []);
  useEffect(() => {
    if (activeRun.value) loadRun(activeRun.value).catch(() => undefined);
  }, [activeRun.value]);

  const list = robotRuns.value;
  const selected = list.find((r) => r.name === activeRun.value) ?? null;
  return (
    <div class="section">
      <header class="section-head">
        <h1 class="t-headline">Train</h1>
        <button class="btn small" type="button" onClick={() => setComposing(!composing)} aria-expanded={composing}>
          <Icon name={composing ? 'close' : 'flask'} />
          {composing ? 'Close' : 'New run'}
        </button>
      </header>
      {composing || !list.length ? <Composer key={preset} initial={preset} cat={cat} onStarted={() => setComposing(false)} /> : null}
      {list.length ? (
        <>
          <RunList list={list} />
          {selected ? <RunDetail run={selected} cat={cat} /> : null}
        </>
      ) : null}
    </div>
  );
}

function RunList({ list }: { list: RunSummary[] }) {
  return (
    <ul class="run-list" aria-label="Training runs">
      {list.slice(0, 8).map((r) => {
        const share = r.total ? Math.min(1, r.steps / r.total) : 0;
        return (
          <li key={r.name}>
            <button type="button" class="run-row" aria-current={activeRun.value === r.name} onClick={() => (activeRun.value = r.name)}>
              <span class="run-name">{r.name}</span>
              <span class="run-meta">
                <Status tone={r.running ? 'busy' : r.status === 'finished' ? 'ok' : 'off'}>{r.running ? 'Training' : r.status === 'finished' ? 'Finished' : 'Stopped'}</Status>
                <span class="num">{compact(r.steps)}</span>
              </span>
              <Segments count={20} done={Math.floor(share * 20)} current={r.running} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function RunDetail({ run, cat }: { run: RunSummary; cat: Catalogue | null }) {
  const live = runLive.value[run.name];
  const p = live?.progress;
  const total = run.total || p?.total || 1;
  const steps = p?.steps ?? run.steps;
  const share = Math.min(1, steps / total);
  const rate = p?.rate ?? 0;
  const remaining = rate > 0 && run.running ? (total - steps) / rate : null;
  const report = live?.codesign;
  const params = meta.value?.robot.design ?? {};
  const cad = Object.fromEntries(Object.values(params).map((d) => [d.key, d.default]));
  const labels = Object.fromEntries((cat?.rewards ?? []).map((r) => [r.key, r.label]));
  const running = live?.state === 'running' || run.running;
  const noPolicy = run.has_policy
    ? null
    : running
      ? 'No policy yet: the run saves its first one after a round of practice, within a minute or two.'
      : 'This run stopped before saving a policy: press Continue to train it.';

  async function watch(): Promise<void> {
    try {
      const result = await api.post<{ design: Record<string, number> }>('/api/sim/policy', { run: run.name });
      if (result.design && Object.keys(result.design).length) design.value = { ...design.value, ...result.design };
      viewMode.value = 'live';
      say(watchHint(run.name, run.skill));
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }
  async function stop(): Promise<void> {
    await api.post(`/api/runs/${encodeURIComponent(run.name)}/stop`).catch((e) => say((e as Error).message, 'error'));
    say(`Stopping ${run.name} after this round; it keeps a checkpoint.`);
  }
  async function resume(): Promise<void> {
    await api.post(`/api/runs/${encodeURIComponent(run.name)}/resume`, { device: 'auto' }).then(() => say(`Continuing ${run.name}.`)).catch((e) => say((e as Error).message, 'error'));
  }
  async function adopt(): Promise<void> {
    if (!report) return;
    const robot = meta.value?.robot.id;
    try {
      await api.post(`/api/robots/${robot}/design`, { design: report.chosen });
      design.value = { ...design.value, ...report.chosen };
      say('The design the search chose is now the robot\'s design.');
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }

  return (
    <article class="run-detail" aria-label={`Run ${run.name}`}>
      <div class="run-title">
        <h2 class="t-title">{run.name}</h2>
        <Status tone={running ? 'busy' : live?.state === 'error' ? 'fault' : run.status === 'finished' ? 'ok' : 'off'}>
          {running ? 'Training' : live?.state === 'error' ? 'Error' : run.status === 'finished' ? 'Finished' : 'Stopped'}
        </Status>
      </div>
      <p class="t-meta">
        {skillTitle(cat, run.skill)}
        {run.codesign ? ' with design search' : ''} · {run.robots.toLocaleString()} robots · {run.device === 'gpu' ? status.value?.gpu.name ?? 'GPU' : 'processor'}
      </p>
      <Segments count={24} done={Math.floor(share * 24)} current={running} label="Practice done" />
      <div class="readout-grid">
        <Readout label="Practice" value={`${compact(steps)} / ${compact(total)}`} />
        <Readout label="Score" value={fixed(p?.score, 2)} />
        <Readout label="Falls" value={percent(p?.falls)} />
        {steers(cat, run.skill) ? (
          <Readout label="Speed error" value={fixed(p?.speed_error, 2)} unit="m/s" />
        ) : (
          <Readout label="Try length" value={fixed(p?.length, 1)} unit="s" />
        )}
        <Readout label="Rate" value={compact(rate)} unit="moves/s" />
        <Readout label={running ? 'Time left' : 'Trained for'} value={running ? duration(remaining) : duration(p?.seconds ?? run.seconds)} />
      </div>
      {live?.error ? <p class="error-line"><Icon name="alert" /> {live.error}</p> : null}
      <div class="actions">
        <Action onClick={watch} blocked={noPolicy}>
          <Icon name="eye" />
          Watch policy
        </Action>
        <Action
          class="btn ghost"
          onClick={() => (viewMode.value = 'swarm')}
          blocked={live?.swarm ? null : running ? 'The practice robots appear after the first round of practice.' : 'Practice robots show while the run trains: press Continue to see them.'}
        >
          <Icon name="grid" />
          Practice robots
        </Action>
        {running ? (
          <button class="btn ghost" type="button" onClick={stop}>
            <Icon name="pause" />
            Stop
          </button>
        ) : (
          <Action class="btn ghost" onClick={resume} blocked={run.status === 'finished' && `${run.name} finished all its practice: start a new run to train further.`}>
            <Icon name="play" />
            Continue
          </Action>
        )}
        {run.has_policy ? (
          <a class="btn ghost icon" href={`/api/runs/${encodeURIComponent(run.name)}/policy.json`} aria-label="Download the policy" title="Download the policy" download>
            <Icon name="download" />
          </a>
        ) : (
          <Action class="btn ghost icon" label="Download the policy" blocked={noPolicy} onClick={() => undefined}>
            <Icon name="download" />
          </Action>
        )}
      </div>
      {report ? (
        <div class="block">
          <h3 class="block-title">Design search</h3>
          <p class="t-meta">
            {report.phase === 'finetune' ? 'Settled. Practising on the chosen design.' : report.phase === 'warmup' ? 'Warming up across the whole design range.' : `Generation ${report.generation}: the best quarter pulls the search toward it.`}
            {' '}
            {report.chosen_metrics ? `Robot height ${fixed(report.chosen_metrics.height, 3)} m, leaning ${fixed(report.chosen_metrics.pitch, 1)}° standing.` : ''}
          </p>
          <DesignTracks history={live!.codesignHistory.length ? live!.codesignHistory : [report]} params={params} cad={cad} />
          <button class="btn small" type="button" onClick={adopt}>
            <Icon name="check" />
            Use this design
          </button>
        </div>
      ) : null}
      {p && Object.keys(p.rewards ?? {}).length ? (
        <details class="block">
          <summary class="block-title">Where the points come from</summary>
          <RewardBars rewards={p.rewards} labels={labels} />
        </details>
      ) : null}
    </article>
  );
}

function skillTitle(cat: Catalogue | null, key: string): string {
  return cat?.skills.find((s) => s.key === key)?.title ?? key;
}

function Composer({ cat, onStarted, initial: asked }: { cat: Catalogue | null; onStarted: () => void; initial: Preset }) {
  const gpu = status.value?.gpu.ready ?? false;
  // Robots whose profile lists no hardware values have nothing for a design search to choose.
  const canSearch = Object.keys(meta.value?.robot.design ?? {}).length > 0;
  const initial: Preset = asked === 'codesign' && !canSearch ? 'move' : asked;
  const plan = PRESETS[initial];
  const [preset, setPreset] = useState<Preset>(initial);
  const [advanced, setAdvanced] = useState(false);
  const [name, setName] = useState('');
  const [skill, setSkill] = useState(plan.skill);
  const [robots, setRobots] = useState(gpu ? plan.gpu[0] : plan.cpu[0]);
  const [millions, setMillions] = useState(gpu ? plan.gpu[1] : plan.cpu[1]);
  const [device, setDevice] = useState<'auto' | 'gpu' | 'cpu'>('auto');
  const [search, setSearch] = useState(plan.search);
  const [params, setParams] = useState<string[]>(Object.keys(meta.value?.robot.design ?? {}));
  const [target, setTarget] = useState('');
  const [pushSpeed, setPushSpeed] = useState('');
  const [busy, setBusy] = useState(false);
  const designParams = Object.values(meta.value?.robot.design ?? {});

  function choose(next: Preset): void {
    const chosen = PRESETS[next];
    setPreset(next);
    setSkill(chosen.skill);
    setSearch(chosen.search);
    setRobots(gpu ? chosen.gpu[0] : chosen.cpu[0]);
    setMillions(gpu ? chosen.gpu[1] : chosen.cpu[1]);
  }

  const estimate = (millions * 1e6) / (gpu && device !== 'cpu' ? (robots >= 8192 ? 50000 : 40000) : 12000);

  async function installGpu(): Promise<void> {
    gpuInstalling.value = true;
    try {
      await api.post('/api/gpu/install', {});
      say('Installing NVIDIA GPU support: a large download that takes a few minutes. Studio says here when it is done.');
    } catch (error) {
      gpuInstalling.value = false;
      say((error as Error).message, 'error');
    }
  }

  async function start(): Promise<void> {
    setBusy(true);
    const runName = name.trim() || `${meta.value?.robot.id ?? 'robot'}-${preset === 'codesign' ? 'design' : skill}-${new Date().toISOString().slice(5, 16).replace(/[-T:]/g, '')}`;
    const setup: Record<string, unknown> = {
      skill,
      robots,
      steps: Math.round(millions * 1e6),
      codesign: { enabled: search && params.length > 0, params, target_height: target ? Number(target) : null },
    };
    if (pushSpeed) setup.robustness = { push_speed: Number(pushSpeed) };
    try {
      await api.post('/api/runs', { name: runName, setup, device });
      activeRun.value = runName;
      say(`Started ${runName}. The first run on a GPU compiles for a few minutes.`);
      await refreshRuns();
      onStarted();
    } catch (error) {
      say((error as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="composer">
      <div class="presets" role="radiogroup" aria-label="What to teach">
        <PresetCard id="move" active={preset} onPick={choose} icon="route" title="Walk and run" text="Walk, run, side-step and turn at the speed asked. Running opens up as walking gets steady." />
        <PresetCard id="jump" active={preset} onPick={choose} icon="jump" title="Jump" text="Crouch, take off and land standing, on command. Press J while watching it." />
        <PresetCard id="stand" active={preset} onPick={choose} icon="shield" title="Stand and recover" text="Stay up while being shoved from every side. The quickest lesson: a few minutes on a GPU." />
        {canSearch ? (
          <PresetCard id="codesign" active={preset} onPick={choose} icon="ruler" title="Find the best leg design" text="Learn to walk while thousands of robots try different rod lengths and shock absorbers; keep the design that walks best." />
        ) : null}
      </div>
      <p class="t-meta">
        About {duration(estimate)} on {gpu && device !== 'cpu' ? status.value?.gpu.name : `this computer's processor (${status.value?.cpus ?? '?'} threads)`}.
        {!gpu ? ' An NVIDIA GPU trains about five times faster.' : ''}
      </p>
      {status.value && !status.value.gpu.installed && status.value.os !== 'Darwin' ? (
        <div class="actions">
          <Action
            class="btn ghost"
            onClick={installGpu}
            blocked={gpuInstalling.value && 'Installing NVIDIA GPU support: a few minutes. Runs can start on the processor meanwhile.'}
            title="Installs CUDA and the GPU packages into Studio's own environment (a large download)"
          >
            <Icon name="download" /> {gpuInstalling.value ? 'Installing GPU support' : 'Install NVIDIA GPU support'}
          </Action>
        </div>
      ) : null}
      <details class="block" open={advanced} onToggle={(e) => setAdvanced((e.currentTarget as HTMLDetailsElement).open)}>
        <summary class="block-title">Every option</summary>
        <div class="form-grid">
          <label class="field">
            <span>Run name</span>
            <input class="input" value={name} placeholder="Named from the date if left empty" onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)} />
          </label>
          <label class="field">
            <span>Skill</span>
            <select class="input" value={skill} onChange={(e) => setSkill((e.currentTarget as HTMLSelectElement).value)}>
              {(cat?.skills ?? []).map((s) => (
                <option value={s.key} key={s.key}>{s.title}</option>
              ))}
            </select>
          </label>
          <label class="field">
            <span>Robots at once</span>
            <select class="input num-input" value={robots} onChange={(e) => setRobots(Number((e.currentTarget as HTMLSelectElement).value))}>
              {[256, 512, 1024, 2048, 4096, 8192].map((n) => (
                <option value={n} key={n}>{n.toLocaleString()}</option>
              ))}
            </select>
          </label>
          <label class="field">
            <span>Practice, million moves</span>
            <input class="input num-input" type="number" min={1} max={2000} value={millions} onInput={(e) => setMillions(Number((e.currentTarget as HTMLInputElement).value) || 1)} />
          </label>
          <label class="field">
            <span>Train on</span>
            <select
              class="input"
              value={device}
              onChange={(e) => {
                const field = e.currentTarget as HTMLSelectElement;
                if (field.value === 'gpu' && !gpu) {
                  say(status.value?.gpu.problem ?? 'No NVIDIA GPU is ready on this computer: training runs on the processor.');
                  field.value = device;
                  return;
                }
                setDevice(field.value as 'auto');
              }}
            >
              <option value="auto">GPU when ready, else processor</option>
              <option value="gpu">{gpu ? 'NVIDIA GPU' : 'NVIDIA GPU (not ready)'}</option>
              <option value="cpu">Processor</option>
            </select>
          </label>
          <label class="field">
            <span>Push strength, m/s</span>
            <input class="input num-input" type="number" step="0.1" min={0} max={3} value={pushSpeed} placeholder="Skill default" onInput={(e) => setPushSpeed((e.currentTarget as HTMLInputElement).value)} />
          </label>
        </div>
        <label class="check">
          <input type="checkbox" checked={search} onChange={(e) => setSearch((e.currentTarget as HTMLInputElement).checked)} />
          <span>Search the hardware design while learning</span>
        </label>
        {search ? (
          <>
            <div class="check-grid" role="group" aria-label="Design values the search may change">
              {designParams.map((d) => (
                <label class="check" key={d.key}>
                  <input
                    type="checkbox"
                    checked={params.includes(d.key)}
                    onChange={(e) => setParams((e.currentTarget as HTMLInputElement).checked ? [...params, d.key] : params.filter((k) => k !== d.key))}
                  />
                  <span>{d.label} <em class="num">{designValue(d.range[0], d.unit, d.kind)} to {designValue(d.range[1], d.unit, d.kind)}</em></span>
                </label>
              ))}
            </div>
            <label class="field narrow">
              <span>Target height, m (optional)</span>
              <input class="input num-input" type="number" step="0.01" min={0.5} max={2.5} value={target} placeholder="Let the physics choose" onInput={(e) => setTarget((e.currentTarget as HTMLInputElement).value)} />
            </label>
          </>
        ) : null}
      </details>
      <div class="actions">
        <Action
          onClick={start}
          blocked={busy ? 'Starting the run: one moment.' : search && !params.length ? 'Tick at least one design value for the search, or untick the design search.' : null}
        >
          <Icon name="play" />
          {busy ? 'Starting' : 'Start training'}
        </Action>
      </div>
    </div>
  );
}

function PresetCard({ id, active, onPick, icon, title, text }: { id: Preset; active: Preset; onPick: (p: Preset) => void; icon: string; title: string; text: string }) {
  return (
    <button type="button" role="radio" aria-checked={active === id} class={`preset${active === id ? ' on' : ''}`} onClick={() => onPick(id)}>
      <Icon name={icon} size={22} />
      <span class="preset-title">{title}</span>
      <span class="preset-text">{text}</span>
    </button>
  );
}
