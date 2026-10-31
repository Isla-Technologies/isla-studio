// Connect: reach the physical robot, bring its motors up safely, watch every one.

import { useEffect, useState } from 'preact/hooks';
import { api } from '../lib/api';
import { send } from '../lib/live';
import { degrees, fixed, jointLabel } from '../lib/format';
import { activeRun, anatomySource, hardware, hardwareLog, meta, robotRuns, say, selection, simState } from '../lib/store';
import type { HardwareStatus, JointMeta } from '../lib/types';
import { Icon } from '../components/icons';
import { Action, Choice, Meter, Readout, Status } from '../components/ui';

interface Settings {
  driver: 'virtual' | 'can' | 'remote';
  buses: { name: string; interface: string; channel: string; bitrate: number }[];
  protocols: Record<string, { protocol: string; gear_ratio: number; p_max: number; v_max: number; kp_max: number; kd_max: number; t_max: number }>;
  remote: string;
  virtual_floor: boolean;
  limits: Record<string, number>;
}

const LIMITS: { key: string; label: string; unit: string; step: number }[] = [
  { key: 'max_speed', label: 'Top target speed', unit: 'rad/s', step: 0.1 },
  { key: 'torque_fraction', label: 'Torque allowed', unit: 'of peak', step: 0.05 },
  { key: 'stiffness_fraction', label: 'Stiffness allowed', unit: 'of trained', step: 0.05 },
  { key: 'soft_start', label: 'Soft start', unit: 's', step: 0.5 },
  { key: 'limit_margin', label: 'Clear of joint limits', unit: 'rad', step: 0.01 },
  { key: 'state_timeout', label: 'Stop after silence', unit: 's', step: 0.01 },
  { key: 'max_temperature', label: 'Hottest allowed', unit: '°C', step: 1 },
  { key: 'max_tracking_error', label: 'Stop when off target by', unit: 'rad', step: 0.05 },
];

export function Connect() {
  const m = meta.value!;
  const hw = hardware.value;
  const [settings, setSettings] = useState<Settings | null>(null);
  const [ports, setPorts] = useState<{ device: string; description: string }[]>([]);
  const [busy, setBusy] = useState('');
  const state = hw?.state ?? 'disconnected';
  const connected = state !== 'disconnected';

  useEffect(() => {
    api.get<HardwareStatus & { settings: Settings; log: string[] }>('/api/hardware').then((data) => {
      setSettings(data.settings);
      hardware.value = data;
      if (!hardwareLog.value.length) hardwareLog.value = data.log;
    });
    api.get<{ ports: { device: string; description: string }[] }>('/api/hardware/ports').then((d) => setPorts(d.ports)).catch(() => undefined);
  }, []);

  async function act(action: string, body: Record<string, unknown> = {}, note?: string): Promise<void> {
    setBusy(action);
    try {
      await api.post(`/api/hardware/${action}`, body);
      if (action === 'connect') anatomySource.value = 'robot';
      if (action === 'disconnect') anatomySource.value = 'sim';
      if (note) say(note);
    } catch (error) {
      say((error as Error).message, 'error');
    } finally {
      setBusy('');
    }
  }

  async function save(change: Partial<Settings>): Promise<void> {
    if (!settings) return;
    const next = { ...settings, ...change };
    setSettings(next);
    try {
      const result = await api.put<{ settings: Settings }>('/api/hardware/settings', change);
      setSettings(result.settings);
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }

  const motors = m.joints.filter((j) => j.motor);
  const online = hw ? Object.values(hw.joints).filter((j) => j.online).length : 0;
  const hottest = hw ? Math.max(0, ...Object.values(hw.joints).map((j) => j.temp ?? 0)) : 0;
  const mirror = simState.value.mode === 'mirror';
  const policyRun = robotRuns.value.find((r) => r.name === activeRun.value && r.has_policy);
  const disarmFirst = state === 'armed' && 'Disarm first: the motors must be relaxed.';

  return (
    <div class="section">
      <header class="section-head">
        <h1 class="t-headline">Connect</h1>
        <Status tone={state === 'armed' ? 'busy' : state === 'connected' ? 'ok' : state === 'fault' ? 'fault' : 'off'}>
          {state === 'armed' ? 'Armed' : state === 'connected' ? 'Connected · relaxed' : state === 'fault' ? 'Stopped' : 'Not connected'}
        </Status>
      </header>

      {state === 'fault' ? (
        <div class="stop-banner" role="alert">
          <Icon name="alert" size={20} />
          <div>
            <p class="t-title">Stopped: {hw?.fault}</p>
            <p class="t-meta">Every motor is relaxed. Fix the cause, then clear the stop; the robot stays relaxed until armed again.</p>
          </div>
          <button class="btn" type="button" onClick={() => act('clear')}>Clear stop</button>
        </div>
      ) : null}

      {!connected && settings ? (
        <section class="block">
          <h2 class="block-title"><Icon name="plug" /> Reach the robot</h2>
          <div class="driver-choice" role="radiogroup" aria-label="How to reach the robot">
            <DriverOption id="virtual" current={settings.driver} onPick={(d) => save({ driver: d })} title="Practice robot" text="A simulated robot behind the same driver. Rehearse bring-up, arming and faults with no hardware." />
            <DriverOption id="can" current={settings.driver} onPick={(d) => save({ driver: d })} title="CAN adapters on this computer" text="USB-CAN adapters straight to the drives (MIT or ODrive protocol), for the bench." />
            <DriverOption id="remote" current={settings.driver} onPick={(d) => save({ driver: d })} title="The robot's computer" text="The robot runs python -m isla_rl.runtime and owns its CAN buses and IMU; this laptop connects over Wi-Fi." />
          </div>
          {settings.driver === 'virtual' ? (
            <Choice label="Where the practice robot is" value={settings.virtual_floor ? 'floor' : 'gantry'} options={[{ value: 'gantry', label: 'In a gantry' }, { value: 'floor', label: 'On the floor' }]} onChange={(v) => save({ virtual_floor: v === 'floor' })} />
          ) : null}
          {settings.driver === 'can' ? <CanSettings settings={settings} ports={ports} onSave={save} /> : null}
          {settings.driver === 'remote' ? (
            <label class="field">
              <span>Robot address</span>
              <input class="input num-input" value={settings.remote} onChange={(e) => save({ remote: (e.currentTarget as HTMLInputElement).value })} placeholder="192.168.4.1:47210" />
            </label>
          ) : null}
          <div class="actions">
            <Action onClick={() => act('connect', {}, 'Connected with every motor relaxed. Move each joint by hand and check the model follows.')} blocked={busy && 'Connecting: one moment.'}>
              <Icon name="plug" />
              {busy === 'connect' ? 'Connecting' : 'Connect'}
            </Action>
          </div>
        </section>
      ) : null}

      {connected && hw ? (
        <>
          <div class="readout-grid">
            <Readout label="Motors answering" value={`${online} / ${motors.length}`} />
            <Readout label="Control loop" value={fixed(hw.rate, 0)} unit="Hz" />
            <Readout label="Hottest motor" value={hottest ? fixed(hottest, 0) : '—'} unit="°C" />
            <Readout label="Armed for" value={state === 'armed' ? fixed(hw.armed_for, 0) : '—'} unit="s" />
          </div>
          <div class="actions">
            {state === 'armed' ? (
              <button class="btn" type="button" onClick={() => act('disarm', {}, 'Disarmed: motors relaxed.')}>
                <Icon name="lock" /> Disarm
              </button>
            ) : (
              <Action
                onClick={() => act('arm', {}, "Armed: stiffness ramps up from the robot's measured pose.")}
                blocked={state === 'fault' ? 'Clear the stop first (the button in the red banner), then arm.' : busy && 'One moment: the last action is still running.'}
                title="Stiffness ramps up from the robot's measured pose"
              >
                <Icon name="bolt" /> Arm
              </Action>
            )}
            <Action class="btn ghost" onClick={() => act('scan')} blocked={disarmFirst}>
              <Icon name="search" /> Scan
            </Action>
            <button class="btn ghost" type="button" onClick={() => send({ type: 'sim.mode', mode: mirror ? 'stand' : 'mirror' })} aria-pressed={mirror}>
              <Icon name="eye" /> {mirror ? 'Back to simulation' : 'Show it in 3D'}
            </button>
            <Action class="btn ghost" onClick={() => act('disconnect', {}, 'Disconnected.')} blocked={disarmFirst}>
              <Icon name="unplug" /> Disconnect
            </Action>
          </div>
          <div class="mode-row">
            <span class="t-label quiet">While armed</span>
            <Choice
              label="What the armed robot does"
              value={hw.mode}
              options={[
                { value: 'hold', label: 'Hold still' },
                { value: 'follow', label: 'Follow the sim' },
                { value: 'policy', label: 'Run policy', blocked: !policyRun && 'Choose a trained policy first, in Deploy.' },
              ]}
              onChange={(mode) => act('mode', { mode, run: activeRun.value }, mode === 'follow' ? 'The robot now moves as the simulation does, no faster than the top speed.' : undefined)}
            />
          </div>
          <BusMap hw={hw} joints={motors} />
          <MotorTable hw={hw} joints={motors} />
        </>
      ) : null}

      {settings ? <Limits settings={settings} locked={state === 'armed'} onSave={save} /> : null}

      {connected && settings?.driver === 'virtual' ? (
        <Rehearse
          joints={motors}
          onInject={(joint, fault) =>
            act('inject', { joint, fault }, fault ? `${jointLabel(joint)}: ${fault}. Watch its row in the motor table.` : `${jointLabel(joint)} repaired.`)
          }
        />
      ) : null}

      <details class="block">
        <summary class="block-title">Log</summary>
        <pre class="log" aria-live="polite">{hardwareLog.value.slice(-60).join('\n') || 'Nothing yet.'}</pre>
      </details>
    </div>
  );
}

function DriverOption({ id, current, onPick, title, text }: { id: Settings['driver']; current: string; onPick: (d: Settings['driver']) => void; title: string; text: string }) {
  return (
    <button type="button" role="radio" aria-checked={current === id} class={`preset${current === id ? ' on' : ''}`} onClick={() => onPick(id)}>
      <span class="preset-title">{title}</span>
      <span class="preset-text">{text}</span>
    </button>
  );
}

function CanSettings({ settings, ports, onSave }: { settings: Settings; ports: { device: string; description: string }[]; onSave: (s: Partial<Settings>) => void }) {
  const update = (index: number, change: Partial<Settings['buses'][number]>) =>
    onSave({ buses: settings.buses.map((b, i) => (i === index ? { ...b, ...change } : b)) });
  return (
    <div class="can-settings">
      <table class="table compact">
        <thead>
          <tr><th>Bus</th><th>Adapter</th><th>Channel</th><th>Bit rate</th></tr>
        </thead>
        <tbody>
          {settings.buses.map((b, i) => (
            <tr key={b.name}>
              <td>{b.name}</td>
              <td>
                <select class="input" value={b.interface} aria-label={`${b.name} adapter`} onChange={(e) => update(i, { interface: (e.currentTarget as HTMLSelectElement).value })}>
                  {['slcan', 'gs_usb', 'pcan', 'kvaser', 'socketcan', 'virtual'].map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              </td>
              <td>
                <input class="input" list="serial-ports" value={b.channel} placeholder={b.interface === 'socketcan' ? 'can0' : 'COM5'} aria-label={`${b.name} channel`} onChange={(e) => update(i, { channel: (e.currentTarget as HTMLInputElement).value })} />
              </td>
              <td>
                <select class="input num-input" value={b.bitrate} aria-label={`${b.name} bit rate`} onChange={(e) => update(i, { bitrate: Number((e.currentTarget as HTMLSelectElement).value) })}>
                  {[1_000_000, 500_000, 250_000].map((x) => <option key={x} value={x}>{x / 1000} kbit/s</option>)}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <datalist id="serial-ports">
        {ports.map((p) => <option key={p.device} value={p.device}>{p.description}</option>)}
      </datalist>
      <table class="table compact">
        <thead>
          <tr><th>Actuator</th><th>Protocol</th><th>Gear ratio</th><th>Torque range</th></tr>
        </thead>
        <tbody>
          {Object.entries(settings.protocols).map(([type, p]) => (
            <tr key={type}>
              <td>{type}</td>
              <td>
                <select class="input" value={p.protocol} aria-label={`${type} protocol`} onChange={(e) => onSave({ protocols: { ...settings.protocols, [type]: { ...p, protocol: (e.currentTarget as HTMLSelectElement).value } } })}>
                  <option value="mit">MIT</option>
                  <option value="odrive">ODrive CANSimple</option>
                </select>
              </td>
              <td>
                <input class="input num-input" type="number" step="0.5" min={1} value={p.gear_ratio} aria-label={`${type} gear ratio`} onChange={(e) => onSave({ protocols: { ...settings.protocols, [type]: { ...p, gear_ratio: Number((e.currentTarget as HTMLInputElement).value) || 1 } } })} />
              </td>
              <td class="num">±{fixed(p.t_max, 0)} N·m</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p class="t-meta">Isla JD-1 drives: set <code>can.protocol</code> to MIT or ODrive until the native CAN-FD protocol is published.</p>
    </div>
  );
}

/** Each CAN bus as a line, its motors as stations in node order. */
function BusMap({ hw, joints }: { hw: HardwareStatus; joints: JointMeta[] }) {
  const buses = new Map<string, JointMeta[]>();
  for (const j of joints) {
    const bus = j.motor!.bus;
    if (!buses.has(bus)) buses.set(bus, []);
    buses.get(bus)!.push(j);
  }
  const selected = selection.value?.kind === 'joint' ? selection.value.id : null;
  return (
    <section class="block" aria-labelledby="bus-title">
      <h2 class="block-title" id="bus-title"><Icon name="route" /> Buses</h2>
      <div class="bus-map">
        {[...buses.entries()].sort().map(([bus, list]) => (
          <div class="bus" key={bus}>
            <span class="bus-name num">{bus}</span>
            <ol class="bus-line">
              {list.sort((a, b) => a.motor!.node - b.motor!.node).map((j) => {
                const s = hw.joints[j.name];
                const tone = !s?.online ? 'off' : s.fault ? 'fault' : hw.state === 'armed' ? 'armed' : 'ok';
                return (
                  <li key={j.id}>
                    <button type="button" class={`station ${tone}${selected === j.id ? ' selected' : ''}`} onClick={() => (selection.value = { kind: 'joint', id: j.id })} aria-label={`${jointLabel(j.name)}, node ${j.motor!.node}: ${tone === 'off' ? 'not answering' : tone === 'fault' ? `fault, ${s?.fault}` : 'answering'}`}>
                      <i />
                      <span class="station-name">{jointLabel(j.name)}</span>
                      <span class="station-node num">{j.motor!.node}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}

function MotorTable({ hw, joints }: { hw: HardwareStatus; joints: JointMeta[] }) {
  const selected = selection.value?.kind === 'joint' ? selection.value.id : null;
  return (
    <div class="table-wrap">
      <table class="table motor-table" aria-label="Motors, live">
        <colgroup>
          <col style={{ width: '30%' }} />
          <col style={{ width: '14%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '24%' }} />
          <col style={{ width: '16%' }} />
        </colgroup>
        <thead>
          <tr><th>Joint</th><th>Node</th><th>Angle</th><th>Torque</th><th>Temp</th></tr>
        </thead>
        <tbody>
          {joints.map((j) => {
            const s = hw.joints[j.name];
            const peak = j.motor!.peak_torque;
            return (
              <tr key={j.id} aria-selected={selected === j.id} onClick={() => (selection.value = { kind: 'joint', id: j.id })}>
                <td>
                  <Status tone={!s?.online ? 'off' : s.fault ? 'fault' : 'ok'}>{jointLabel(j.name)}</Status>
                </td>
                <td>{j.motor!.bus.replace('can', '')}·{j.motor!.node}</td>
                <td>{s?.online ? degrees(s.q) : s?.fault ? 'fault' : 'no reply'}</td>
                <td>
                  <Meter share={Math.abs(s?.tau ?? 0) / peak} label={`Torque against ${peak} N·m peak`} />
                  <span class="num"> {fixed(s?.tau ?? null, 1)}</span>
                </td>
                <td>{s?.temp !== null && s?.temp !== undefined ? `${fixed(s.temp, 0)} °C` : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Limits({ settings, locked, onSave }: { settings: Settings; locked: boolean; onSave: (s: Partial<Settings>) => void }) {
  return (
    <details class="block">
      <summary class="block-title"><Icon name="shield" /> Safety limits</summary>
      <p class="t-meta">{locked ? 'Disarm to change these.' : 'Start conservative and raise them in a gantry. Policies learned with full stiffness: raise both shares before running one.'}</p>
      <div class="form-grid">
        {LIMITS.map((l) => (
          <label class="field" key={l.key}>
            <span>{l.label} <em>({l.unit})</em></span>
            <input
              class="input num-input"
              type="number"
              step={l.step}
              value={settings.limits[l.key]}
              readOnly={locked}
              aria-readonly={locked}
              onClick={() => locked && say('Disarm the robot to change its safety limits.')}
              onChange={(e) => onSave({ limits: { ...settings.limits, [l.key]: Number((e.currentTarget as HTMLInputElement).value) } })}
            />
          </label>
        ))}
      </div>
    </details>
  );
}

function Rehearse({ joints, onInject }: { joints: JointMeta[]; onInject: (joint: string, fault: string | null) => void }) {
  const [joint, setJoint] = useState(joints[0]?.name ?? '');
  return (
    <details class="block">
      <summary class="block-title"><Icon name="flask" /> Rehearse a fault</summary>
      <p class="t-meta">Practice robot only: see what the robot and this screen do when a motor fails.</p>
      <div class="actions">
        <select class="input" value={joint} aria-label="Motor" onChange={(e) => setJoint((e.currentTarget as HTMLSelectElement).value)}>
          {joints.map((j) => <option key={j.id} value={j.name}>{jointLabel(j.name)}</option>)}
        </select>
        <button class="btn ghost small" type="button" onClick={() => onInject(joint, 'offline')}>Unplug</button>
        <button class="btn ghost small" type="button" onClick={() => onInject(joint, 'overheat')}>Overheat</button>
        <button class="btn ghost small" type="button" onClick={() => onInject(joint, 'encoder error')}>Encoder error</button>
        <button class="btn ghost small" type="button" onClick={() => onInject(joint, null)}>Repair</button>
      </div>
    </details>
  );
}
