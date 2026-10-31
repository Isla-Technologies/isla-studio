// Robot: what the platform knows about the robot, and its motor map.

import { useState } from 'preact/hooks';
import { api } from '../lib/api';
import { fixed, jointLabel } from '../lib/format';
import { meta, say } from '../lib/store';
import type { MotorView } from '../lib/types';
import { Icon } from '../components/icons';
import { Action, Readout } from '../components/ui';

export function Robot() {
  const m = meta.value!;
  const r = m.robot;
  const [motors, setMotors] = useState<MotorView[]>(r.motors);
  const [dirty, setDirty] = useState(false);
  const types = Object.entries(r.actuator_types);
  const count = (type: string) => r.motors.filter((x) => x.type === type).length;
  const speeds = types.some(([, t]) => t.no_load_speed);

  function edit(joint: string, change: Partial<MotorView>): void {
    setMotors(motors.map((x) => (x.joint === joint ? { ...x, ...change } : x)));
    setDirty(true);
  }

  async function save(): Promise<void> {
    try {
      const result = await api.put<{ motors: MotorView[] }>(`/api/robots/${r.id}/motors`, { motors });
      setMotors(result.motors);
      setDirty(false);
      meta.value = { ...m, robot: { ...r, motors: result.motors } };
      say('Motor map saved in the robot profile.');
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }

  return (
    <div class="section">
      <header class="section-head">
        <h1 class="t-headline">{r.name}</h1>
      </header>
      <p class="t-body">{r.about}</p>
      <div class="readout-grid">
        <Readout label="Mass" value={fixed(m.info.total_mass, 2)} unit="kg" />
        {m.info.virtual_mass > 0 ? (
          <>
            <Readout label="From CAD" value={fixed(m.info.cad_mass, 2)} unit="kg" />
            <Readout label="Virtual mass" value={`+${fixed(m.info.virtual_mass, 2)}`} unit="kg" title={r.mass.note} />
          </>
        ) : null}
        <Readout label="Height" value={fixed(m.info.height, 3)} unit="m" />
        <Readout label="Motors" value={r.motors.length} />
        <Readout label="Passive" value={m.info.passive.length} title="Joints without a motor" />
      </div>
      {r.mass.note ? <p class="t-meta">{r.mass.note}</p> : null}

      <section class="block">
        <h2 class="block-title"><Icon name="actuator" /> Actuators</h2>
        <table class="table compact">
          <thead>
            <tr>
              <th style={{ width: speeds ? '28%' : '34%' }}>Type</th>
              <th style={{ width: '18%' }}>Count</th>
              <th>Peak</th>
              <th>kp / kd</th>
              {speeds ? <th>No-load</th> : null}
            </tr>
          </thead>
          <tbody>
            {types.map(([key, t]) => (
              <tr key={key}>
                <td title={[t.label, t.driver, t.protocol && `${t.protocol.toUpperCase()} protocol`].filter(Boolean).join(' · ')}>{t.label}</td>
                <td>{count(key)}</td>
                <td>{fixed(t.peak_torque, Number.isInteger(t.peak_torque) ? 0 : 2)} N·m</td>
                <td title={t.assumed?.includes('kp') ? 'Estimated from the torque limit' : undefined}>{fixed(t.kp, 0)} / {fixed(t.kd, 2)}</td>
                {speeds ? <td title={t.assumed?.includes('no_load_speed') ? 'Assumed until measured' : undefined}>{t.no_load_speed ? `${fixed(t.no_load_speed, 1)} rad/s` : 'Not given'}</td> : null}
              </tr>
            ))}
          </tbody>
        </table>
        {types.map(([key, t]) => (t.note ? <p class="t-meta" key={key}>{t.label}: {t.note}</p> : null))}
      </section>

      {m.struts.length ? (
      <section class="block">
        <h2 class="block-title"><Icon name="spring" /> Passive ankles</h2>
        <p class="t-meta">{r.struts.note} Stroke {fixed(r.struts.stroke_mm ?? null, 0)} mm (assumed until the shock is chosen).</p>
        <table class="table compact">
          <thead>
            <tr><th>Shock</th><th>Eye to eye</th><th>Stroke</th></tr>
          </thead>
          <tbody>
            {m.struts.filter((s) => s.side === 'right').map((s) => (
              <tr key={s.name}>
                <td>{s.position === 'front' ? 'Front' : 'Back'}, each leg</td>
                <td>{fixed(s.length_cad * 1000, 1)} mm</td>
                <td>{fixed(s.length_min * 1000, 1)} to {fixed(s.length_max * 1000, 1)} mm</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      ) : null}

      <section class="block">
        <h2 class="block-title"><Icon name="route" /> Motor map</h2>
        <p class="t-meta">Which CAN bus and node drives each joint, its direction and its zero. Connect sets zeros from the robot; change the rest here.</p>
        <div class="table-wrap">
          <table class="table compact">
            <thead>
              <tr>
                <th style={{ width: '28%' }}>Joint</th>
                <th style={{ width: '21%' }}>Bus</th>
                <th style={{ width: '13%' }}>Node</th>
                <th style={{ width: '21%' }}>Turns</th>
                <th title="Zero, in radians">Zero</th>
              </tr>
            </thead>
            <tbody>
              {motors.map((x) => (
                <tr key={x.joint}>
                  <td title={`${jointLabel(x.joint)} · ${r.actuator_types[x.type]?.label ?? x.type}`}>{jointLabel(x.joint)}</td>
                  <td>
                    <select class="input" value={x.bus} aria-label={`${x.joint} bus`} onChange={(e) => edit(x.joint, { bus: (e.currentTarget as HTMLSelectElement).value })}>
                      {['can0', 'can1', 'can2', 'can3', 'can4', 'can5'].map((b) => <option key={b} value={b}>{b}</option>)}
                    </select>
                  </td>
                  <td><input class="input num-input" type="number" min={0} max={63} value={x.node} aria-label={`${x.joint} node`} onChange={(e) => edit(x.joint, { node: Number((e.currentTarget as HTMLInputElement).value) })} /></td>
                  <td>
                    <select class="input" value={x.direction} aria-label={`${x.joint} direction`} onChange={(e) => edit(x.joint, { direction: Number((e.currentTarget as HTMLSelectElement).value) })}>
                      <option value={1}>As is</option>
                      <option value={-1}>Reversed</option>
                    </select>
                  </td>
                  <td class="num">{fixed(x.zero, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div class="actions">
          <Action onClick={save} blocked={!dirty && 'Nothing to save: change a motor\'s bus, node or direction first.'}>
            <Icon name="check" /> {dirty ? 'Save motor map' : 'Saved'}
          </Action>
        </div>
      </section>
    </div>
  );
}
