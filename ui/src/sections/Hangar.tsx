// The hangar: every robot Isla Studio can train, each in its bay. Pick one to
// look it over on the turntable, load it for training, or bring a new one in.

import { effect } from '@preact/signals';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { api } from '../lib/api';
import { fixed } from '../lib/format';
import { forgetRobot, loadRobot } from '../lib/robot';
import { activeRobot, hangarPick, robots, say, section, status } from '../lib/store';
import { theme } from '../lib/theme';
import type { RobotCard } from '../lib/types';
import { HangarView, type ScreenBox } from '../three/hangar';
import { Icon } from '../components/icons';
import { Action, Segments, Status } from '../components/ui';

export const IMPORT = 'import';
/** Height the lineup and the command bar take at the bottom of the hangar (px). */
export const LINEUP_SPACE = 268;
const FIGURE_PX = 104; // The tallest robot's blueprint height.
const MODEL_FILE = /\.(xml|urdf|mjcf)$/i;
const ASSET_FILE = /\.(xml|urdf|mjcf|stl|obj|msh|png)$/i;
const SKIPPED = /(^|\/)(\.[^/]+|node_modules|__pycache__|\.venv|venv)\//;
const ACRONYMS = new Set(['urdf', 'mjcf', 'mk', 'dof', 'imu', 'ai', 'rl', 'cad']);

export async function refreshRobots(): Promise<void> {
  try {
    robots.value = await api.get<RobotCard[]>('/api/robots');
  } catch (error) {
    say((error as Error).message, 'error');
  }
}

/** The robot in view: the one picked, else the one loaded, else the first. */
function pickedCard(): RobotCard | null {
  const list = robots.value ?? [];
  const id = hangarPick.value ?? activeRobot.value;
  return list.find((c) => c.id === id) ?? list.find((c) => c.active) ?? list[0] ?? null;
}

// The stage.

/** Room the HUD leaves the robot at this window size: the panel's side, the bays' bottom. */
function insets(panel: boolean): { left: number; top: number; bottom: number } {
  const width = window.innerWidth;
  // Narrow screens: the bays run along the top and the panel takes the bottom half.
  if (width <= 820) return { left: 0, top: 200, bottom: Math.round(window.innerHeight * 0.5) };
  return { left: panel ? (width <= 1360 ? 380 : 420) : 0, top: 0, bottom: LINEUP_SPACE };
}

export function HangarStage({ panel }: { panel: boolean }) {
  const [room, setRoom] = useState(() => insets(panel));
  const left = room.left;
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<HangarView | null>(null);
  const target = useRef<HTMLDivElement>(null);
  const measure = useRef<HTMLDivElement>(null);
  const [noWebgl, setNoWebgl] = useState(false);
  const [arriving, setArriving] = useState<string | null>(null);
  const importing = hangarPick.value === IMPORT;
  const card = importing ? null : pickedCard();

  useEffect(() => {
    void refreshRobots();
  }, [activeRobot.value]);

  useEffect(() => {
    if (!canvas.current) return;
    let unfollow = () => {};
    try {
      const bay = new HangarView(canvas.current);
      view.current = bay;
      bay.onFrame = (box) => place(box, target.current, measure.current);
      unfollow = effect(() => bay.setTheme(theme.value)); // Light or dark, the bay follows.
    } catch {
      setNoWebgl(true);
    }
    return () => {
      unfollow();
      view.current?.dispose();
      view.current = null;
    };
  }, []);

  useEffect(() => {
    const update = () => setRoom(insets(panel));
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, [panel]);

  useEffect(() => view.current?.setInsets(room.left, room.bottom, room.top), [room.left, room.bottom, room.top]);

  const id = card?.problem ? undefined : card?.id;
  useEffect(() => {
    if (importing) view.current?.clear(); // The import bay: an empty platform waiting for its robot.
  }, [importing]);

  useEffect(() => {
    if (!id || !view.current) return;
    let current = true;
    setArriving(id);
    loadRobot(id)
      .then((assets) => {
        if (!current) return;
        view.current?.show(assets, assets.meta.design_values ?? {});
        setArriving(null);
      })
      .catch((error: Error) => {
        if (!current) return;
        setArriving(null);
        say(error.message, 'error');
      });
    return () => {
      current = false;
    };
  }, [id]);

  const placeholder = card && (arriving === card.id || noWebgl);
  return (
    <div class="stage hangar-stage">
      <canvas
        ref={canvas}
        class="stage-canvas"
        aria-label={card ? `${card.name} on the hangar's turntable. Drag to turn the view, scroll to zoom, double-click to frame it.` : 'The hangar'}
      />
      <div ref={target} class="target" aria-hidden="true" />
      <div ref={measure} class="measure" aria-hidden="true">
        <span class="num">{card && !placeholder ? `${fixed(card.height, 2)} m` : ''}</span>
      </div>
      {placeholder ? (
        <div class="hangar-placeholder" style={{ paddingLeft: `${left}px`, paddingTop: `${room.top}px`, paddingBottom: `${room.bottom}px` }} role="status">
          <Blueprint card={card} scale={360 / Math.max(card.height || 1, 1)} on />
          <p class="t-meta">{noWebgl ? 'This browser cannot draw in 3D (WebGL is off), so the hangar shows blueprints.' : `Bringing ${card.name} into the bay`}</p>
        </div>
      ) : null}
    </div>
  );
}

/** Targeting brackets around the robot and its height beside it, moved every frame without re-rendering. */
function place(box: ScreenBox | null, target: HTMLDivElement | null, measure: HTMLDivElement | null): void {
  if (!target || !measure) return;
  const shown = !!box && box.settled;
  target.classList.toggle('on', shown);
  measure.classList.toggle('on', shown);
  if (!box) return;
  const pad = 14;
  target.style.transform = `translate(${box.left - pad}px, ${box.top - pad}px)`;
  target.style.width = `${box.right - box.left + 2 * pad}px`;
  target.style.height = `${box.bottom - box.top + 2 * pad}px`;
  measure.style.transform = `translate(${box.right + pad + 18}px, ${box.axisTop}px)`;
  measure.style.height = `${Math.max(0, box.axisBottom - box.axisTop)}px`;
}

// The bays.

export function Lineup() {
  const list = robots.value;
  const importing = hangarPick.value === IMPORT;
  const card = pickedCard();
  if (!list) {
    return (
      <nav class="lineup" aria-label="Robots in the hangar" aria-busy="true">
        <p class="t-meta lineup-wait">Opening the hangar</p>
      </nav>
    );
  }
  const tallest = Math.max(1, ...list.map((c) => c.height || 0));
  const scale = FIGURE_PX / tallest;
  const ticks = [0.5, 1, 1.5, 2].filter((t) => t <= tallest + 0.05);
  return (
    <nav class="lineup" aria-label="Robots in the hangar" style={{ '--metre': `${scale}px` }}>
      <ol class="bays">
        <li class="ruler" aria-hidden="true">
          <span class="bay-figure">
            {ticks.map((t) => (
              <i key={t} style={{ bottom: `${t * scale}px` }}>{fixed(t, 1)} m</i>
            ))}
          </span>
        </li>
        {list.map((c) => {
          const on = !importing && card?.id === c.id;
          return (
            <li key={c.id}>
              <button type="button" class={`bay${on ? ' on brackets' : ''}`} aria-pressed={on} onClick={() => (hangarPick.value = c.id)}>
                <span class="bay-figure">{c.blueprint ? <Blueprint card={c} scale={scale} on={on} /> : <Icon name="alert" size={24} />}</span>
                <span class="bay-name">{c.name}</span>
                <span class="bay-meta num">
                  {c.problem ? 'Does not build' : `${fixed(c.mass, 1)} kg · ${fixed(c.height, 2)} m · ${c.motors} motors`}
                </span>
                {c.active ? <span class="status ok bay-loaded">Loaded</span> : null}
              </button>
            </li>
          );
        })}
        <li>
          <button type="button" class={`bay bay-import${importing ? ' on brackets' : ''}`} aria-pressed={importing} onClick={() => (hangarPick.value = IMPORT)}>
            <span class="bay-figure">
              <Icon name="upload" size={26} />
            </span>
            <span class="bay-name">Import a robot</span>
            <span class="bay-meta">MJCF or URDF</span>
          </button>
        </li>
      </ol>
    </nav>
  );
}

/** A robot from the front: an outline per part, back to front, a point per motor. */
export function Blueprint({ card, scale, on }: { card: RobotCard; scale: number; on?: boolean }) {
  const drawn = useMemo(
    () =>
      card.blueprint.parts.map((part) => {
        const p = part.points;
        let text = '';
        for (let i = 0; i < p.length; i += 2) text += `${p[i]},${-p[i + 1]} `;
        return text;
      }),
    [card.blueprint],
  );
  const [x0, y0, x1, y1] = card.blueprint.bounds;
  const pad = 3 / scale;
  return (
    <svg
      class={`blueprint${on ? ' on' : ''}`}
      width={(x1 - x0) * scale + 6}
      height={(y1 - y0) * scale + 6}
      viewBox={`${x0 - pad} ${-y1 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`}
      aria-hidden="true"
    >
      {drawn.map((points, i) => (
        <polygon key={i} points={points} vector-effect="non-scaling-stroke" />
      ))}
      {card.blueprint.motors.map(([u, v], i) => (
        <circle key={`m${i}`} cx={u} cy={-v} r={(on ? 2.4 : 1.8) / scale} />
      ))}
    </svg>
  );
}

// The panel: the robot's numbers, or the import form.

export function HangarSheet() {
  if (hangarPick.value === IMPORT) return <ImportSheet />;
  if (!robots.value) {
    return (
      <div class="section">
        <p class="t-meta">Opening the hangar</p>
      </div>
    );
  }
  const card = pickedCard();
  if (!card) {
    return (
      <div class="section">
        <h1 class="t-headline">The hangar is empty</h1>
        <p class="t-body">Import a robot to train: its MJCF or URDF model and meshes.</p>
      </div>
    );
  }
  return <SpecSheet key={card.id} card={card} />;
}

function balanceText(card: RobotCard): string {
  if (card.stands_alone) return `Stands on its own: holding its standing pose for 2 s, it turns ${fixed(card.stand_drift, 1)}°.`;
  const tips = card.stand_drift >= 80 ? 'falls over within 2 s' : `tips ${fixed(card.stand_drift, 0)}° in 2 s`;
  return `Needs a policy to balance: holding its standing pose, it ${tips}. Until a policy runs, the simulation holds it in a gantry.`;
}

function SpecSheet({ card }: { card: RobotCard }) {
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(false);

  async function load(): Promise<void> {
    setBusy(true);
    try {
      await api.post(`/api/robots/${card.id}/activate`);
      section.value = 'train';
    } catch (error) {
      say((error as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    try {
      await api.del(`/api/robots/${card.id}`);
      forgetRobot(card.id);
      hangarPick.value = null;
      say(`${card.name} has left the hangar.`);
      await refreshRobots();
    } catch (error) {
      say((error as Error).message, 'error');
      setRemoving(false);
    }
  }

  const geared = card.actuators.some((a) => a.gear_ratio);
  const loaded = (robots.value ?? []).find((c) => c.active && c.id !== card.id && !c.problem) ?? null;
  const runs = card.runs ? `${card.runs} ${card.runs === 1 ? 'run' : 'runs'}, ${card.policies} with a policy` : 'No runs yet';
  const table = (
    <table class="table compact">
      <thead>
        <tr>
          <th style={{ width: geared ? '33%' : '44%' }}>Type</th>
          <th style={{ width: geared ? '18%' : '22%' }}>Count</th>
          <th>Torque</th>
          {geared ? <th style={{ width: '22%' }}>Gearing</th> : null}
        </tr>
      </thead>
      <tbody>
        {card.actuators.map((a) => (
          <tr key={a.type}>
            <td title={a.label}>{a.label}</td>
            <td>{a.count}</td>
            <td>{fixed(a.peak_torque, Number.isInteger(a.peak_torque) ? 0 : 2)} N·m</td>
            {geared ? <td>{a.gear_ratio ? `${fixed(a.gear_ratio, Number.isInteger(a.gear_ratio) ? 0 : 2)}:1` : 'Not given'}</td> : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <div class="section hangar-sheet">
      <header class="section-head">
        <h1 class="t-display hangar-name">{card.name}</h1>
      </header>
      <div class="hangar-state">
        {card.active ? <Status tone="ok">Loaded for training</Status> : <Status tone="off">In the hangar</Status>}
        <span class="t-meta">
          {card.imported ? 'Imported · ' : ''}
          {runs}
        </span>
        {card.problem ? null : (
          <span class={`state-balance${card.stands_alone ? '' : ' warn'}`}>
            <Icon name={card.stands_alone ? 'check' : 'alert'} />
            {card.stands_alone ? 'Stands on its own' : 'Needs a policy to balance'}
          </span>
        )}
      </div>
      {card.about ? <p class="t-body">{card.about}</p> : null}
      {card.problem ? (
        <p class="error-line" role="alert">
          <Icon name="alert" />
          <span>Its model does not build: {card.problem}</span>
        </p>
      ) : (
        <>
          <div class="readout-grid">
            <Stat
              label="Mass"
              value={card.mass}
              digits={1}
              unit="kg"
              than={loaded?.mass}
              title={card.virtual_mass > 0 ? `${fixed(card.cad_mass, 2)} kg from the CAD and ${fixed(card.virtual_mass, 2)} kg of virtual mass` : undefined}
            />
            <Stat label="Height" value={card.height} digits={2} unit="m" than={loaded?.height} />
            <Stat label="Motors" value={card.motors} digits={0} than={loaded?.motors} />
            <Stat label="Joints" value={card.dof} digits={0} than={loaded?.dof} title={card.passive ? `${card.passive} of them without a motor` : undefined} secondary />
            <Stat label="Peak torque" value={card.peak_torque} digits={0} unit="N·m" than={loaded?.peak_torque} />
            <Stat label="Hip height" value={card.hip_height} digits={2} unit="m" than={loaded?.hip_height} secondary />
          </div>
          {loaded ? <p class="t-meta compare-note">Under each figure: the difference from {loaded.name}, loaded now.</p> : null}
          <section class="block balance-block">
            <h2 class="block-title">
              <Icon name="shield" /> Balance
            </h2>
            <p class={`verdict${card.stands_alone ? '' : ' warn'}`}>
              <Icon name={card.stands_alone ? 'check' : 'alert'} />
              <span>{balanceText(card)}</span>
            </p>
          </section>
          {card.actuators.length > 3 ? (
            <details class="block">
              <summary class="block-title">
                <Icon name="actuator" /> Actuators · {card.actuators.length} types
              </summary>
              {table}
            </details>
          ) : (
            <section class="block">
              <h2 class="block-title">
                <Icon name="actuator" /> Actuators
              </h2>
              {table}
            </section>
          )}
          <section class="block">
            <div class="block-head">
              <h2 class="block-title">
                <Icon name="ruler" /> Co-design
              </h2>
              {card.active && card.design.length ? (
                <button class="btn ghost small" type="button" onClick={() => (section.value = 'design')}>
                  Change in Design
                </button>
              ) : null}
            </div>
            {card.design.length ? (
              <ul class="plain-list">
                {card.design.map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            ) : (
              <p class="t-meta">None: its hardware is fixed, so training learns the control alone.</p>
            )}
          </section>
        </>
      )}
      {card.imported && !card.active ? (
        <div class="hangar-remove">
          {removing ? (
            <div class="actions">
              <span class="t-meta">Delete {card.name} and its files?</span>
              <button class="btn small" type="button" onClick={() => void remove()}>
                Remove
              </button>
              <button class="btn ghost small" type="button" onClick={() => setRemoving(false)}>
                Keep
              </button>
            </div>
          ) : (
            <button class="btn ghost small" type="button" onClick={() => setRemoving(true)}>
              Remove from the hangar
            </button>
          )}
        </div>
      ) : null}
      <div class="sticky-actions hangar-actions">
        {card.active ? (
          <button class="btn wide" type="button" onClick={() => (section.value = 'train')}>
            <Icon name="flask" />
            Train {card.name}
          </button>
        ) : (
          <Action class="btn wide" blocked={card.problem ? `${card.name} cannot be loaded: ${card.problem}` : busy && `Loading ${card.name}: one moment.`} onClick={load}>
            <Icon name="bolt" />
            {busy ? `Loading ${card.name}` : `Load ${card.name} for training`}
          </Action>
        )}
      </div>
    </div>
  );
}

/** A readout with, for a robot that is not loaded, how far it is from the one that is. */
function Stat({ label, value, digits, unit, than, title, secondary }: { label: string; value: number; digits: number; unit?: string; than?: number; title?: string; secondary?: boolean }) {
  const delta = than === undefined || !Number.isFinite(than) ? null : value - than;
  const same = delta !== null && Math.abs(delta) < 0.5 * 10 ** -digits;
  return (
    <div class={`readout${secondary ? ' secondary' : ''}`} title={title}>
      <span class="key">{label}</span>
      <span class="value">
        {fixed(value, digits)}
        {unit ? <small>{unit}</small> : null}
      </span>
      {delta !== null ? <span class="delta num">{same ? 'Same' : `${delta > 0 ? '+' : '−'}${fixed(Math.abs(delta), digits)}${unit ? ` ${unit}` : ''}`}</span> : null}
    </div>
  );
}

// Importing.

interface Picked {
  file: File;
  path: string;
}

/** Guess a robot's name from its model file, or its folder when the file name says nothing. */
function guessName(path: string): string {
  const parts = path.split('/');
  const stem = (parts[parts.length - 1] ?? '').replace(MODEL_FILE, '');
  const plain = /^(robot|scene|model|main|urdf|mjcf)$/i.test(stem) && parts.length > 1 ? parts[0] : stem;
  return plain
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((w) => (/\d/.test(w) || ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)))
    .join(' ');
}

function ImportSheet() {
  const [files, setFiles] = useState<Picked[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [sent, setSent] = useState<{ done: number; total: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const windows = (status.value?.os ?? '').toLowerCase().startsWith('win');

  async function choose(list: FileList | null): Promise<void> {
    setProblem('');
    const picked = Array.from(list ?? [])
      .map((file) => ({ file, path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name }))
      .filter((f) => ASSET_FILE.test(f.path) && !SKIPPED.test(f.path));
    // Only MJCF (<mujoco>) and URDF (<robot>) files are models; other XML is left out.
    const found: string[] = [];
    for (const f of picked.filter((f) => MODEL_FILE.test(f.path))) {
      const head = await f.file.slice(0, 4096).text();
      if (/<mujoco[\s>]|<robot[\s>]/.test(head)) found.push(f.path);
    }
    found.sort((a, b) => a.split('/').length - b.split('/').length || a.length - b.length);
    setFiles(picked);
    setModels(found);
    setModel(found[0] ?? '');
    setName(found[0] ? guessName(found[0]) : '');
    if (list?.length && !picked.length) setProblem('That folder has no model, mesh or texture files in it.');
    else if (picked.length && !found.length) setProblem('That folder has no MJCF or URDF model in it.');
  }

  async function finish(card: RobotCard): Promise<void> {
    await refreshRobots();
    hangarPick.value = card.id;
    say(`${card.name} is in the hangar.`);
  }

  async function importFolder(): Promise<void> {
    setBusy(true);
    setProblem('');
    const token = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('');
    try {
      setSent({ done: 0, total: files.length });
      let done = 0;
      for (const f of files) {
        const url = `/api/robots/upload/${token}/${f.path.split('/').map(encodeURIComponent).join('/')}`;
        const response = await fetch(url, { method: 'PUT', body: f.file });
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `${f.path} could not be sent`);
        setSent({ done: ++done, total: files.length });
      }
      await finish(await api.post<RobotCard>(`/api/robots/upload/${token}/import`, { model, name }));
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
      setSent(null);
    }
  }

  async function importPath(): Promise<void> {
    setBusy(true);
    setProblem('');
    try {
      await finish(await api.post<RobotCard>('/api/robots/import', { path, name }));
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const megabytes = files.reduce((sum, f) => sum + f.file.size, 0) / 1e6;
  return (
    <div class="section">
      <header class="section-head">
        <h1 class="t-headline">Import a robot</h1>
      </header>
      <p class="t-body">
        Bring in any legged robot as an MJCF or URDF model with its meshes. Isla Studio finds its base, feet, facing and motors, and writes what it found into the robot's profile for you to check.
      </p>
      <section class="block">
        <h2 class="block-title">
          <Icon name="upload" /> From its folder
        </h2>
        <label
          class={`btn ghost file-pick${busy ? ' is-blocked' : ''}`}
          onClick={(event) => {
            if (!busy) return;
            event.preventDefault();
            say('An import is running: wait for it to finish.');
          }}
        >
          <input
            type="file"
            class="sr-only"
            multiple
            disabled={busy}
            // @ts-expect-error Chromium, Firefox and Safari all pick folders with this attribute.
            webkitdirectory=""
            onChange={(event) => void choose((event.currentTarget as HTMLInputElement).files)}
          />
          <Icon name="stack" />
          {files.length ? 'Choose another folder' : "Choose the robot's folder"}
        </label>
        {files.length ? (
          <p class="t-meta num">
            {files.length} model, mesh and texture files, {fixed(megabytes, 1)} MB
          </p>
        ) : null}
        {models.length > 1 ? (
          <label class="field">
            <span>Model</span>
            <select
              class="input"
              value={model}
              onChange={(event) => {
                const value = (event.currentTarget as HTMLSelectElement).value;
                setModel(value);
                setName(guessName(value));
              }}
            >
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        ) : models.length === 1 ? (
          <p class="t-meta">
            Model: <code>{model}</code>
          </p>
        ) : null}
        {model ? (
          <>
            <label class="field">
              <span>Name</span>
              <input class="input" value={name} maxLength={40} onInput={(event) => setName((event.currentTarget as HTMLInputElement).value)} />
            </label>
            <Action blocked={busy ? 'Importing: one moment.' : !name.trim() && 'Give the robot a name first.'} onClick={importFolder}>
              <Icon name="download" />
              {busy && sent ? `Sending ${sent.done} of ${sent.total}` : busy ? 'Importing' : `Import ${name.trim() || 'robot'}`}
            </Action>
            {sent ? <Segments count={16} done={Math.floor((sent.done / Math.max(1, sent.total)) * 16)} current label="Files sent" /> : null}
          </>
        ) : null}
      </section>
      <section class="block">
        <h2 class="block-title">
          <Icon name="search" /> From a file on the server's computer
        </h2>
        <label class="field">
          <span>Model file</span>
          <input
            class="input"
            value={path}
            spellcheck={false}
            placeholder={windows ? 'C:\\robots\\my-robot\\robot.xml' : '/home/me/robots/my-robot/robot.xml'}
            onInput={(event) => setPath((event.currentTarget as HTMLInputElement).value)}
          />
        </label>
        <Action class="btn ghost" blocked={busy ? 'Importing: one moment.' : !path.trim() && "Type the model file's full path first."} onClick={importPath}>
          Import from this file
        </Action>
      </section>
      {problem ? (
        <p class="error-line" role="alert">
          <Icon name="alert" />
          <span>{problem}</span>
        </p>
      ) : null}
      <section class="block">
        <h2 class="block-title">
          <Icon name="check" /> What a robot needs
        </h2>
        <ul class="checklist">
          <li>Two feet that reach the floor in its standing pose (its first keyframe, or its zero pose).</li>
          <li>A motor on every joint training should drive: MJCF position or motor actuators.</li>
          <li>Meshes as STL, OBJ or MSH files inside its folder.</li>
        </ul>
      </section>
    </div>
  );
}
