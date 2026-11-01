// Design: the hardware the robot is built with, and what the physics says about it.

import { useEffect, useRef, useState } from 'preact/hooks';
import { api } from '../lib/api';
import { designValue, fixed, jointLabel, percent, signed } from '../lib/format';
import { design, meta, robotRuns, runLive, say } from '../lib/store';
import type { DesignParam } from '../lib/types';
import { Icon } from '../components/icons';
import { Action, Meter, Readout } from '../components/ui';
import { loadRun, refreshRuns } from './Train';

interface Analysis {
  size: { height: number; hip_height: number; com_height: number; lean_deg: number; mass: number; leg_length: number };
  rods: Record<string, { segment_cad: number; segment: number; rod_length: number | null }>;
  ankle: {
    stiffness: number;
    preload_torque: number;
    cop_shift: number;
    toe_down_deg: number;
    toe_up_deg: number;
    passive_ratio: number;
    rocking_hz: number;
    damping_ratio: number;
    struts: {
      position: string;
      length: number;
      stroke_used: number;
      force: number;
      rate_n_per_mm: number;
      rate_lb_per_in: number;
      damping: number;
      moment_arm: number;
      eye_to_eye_range: [number, number]; // m, fully compressed to fully extended
    }[];
  };
  motors: { stand: Record<string, number>; crouch: Record<string, number>; crouch_lean_deg: number; peaks: Record<string, number>; crouch_load: number };
  standing: { seconds: number; drift_deg: number; unaided: boolean };
  walking: { period: number; no_load_speed: number; rows: { speed: number; hip_rate: number; share: number }[] };
}

export function Design() {
  const m = meta.value!;
  const params = m.robot.design;
  const applied = m.robot.design_values;
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef(0);
  const values = { ...applied, ...design.value };
  const changed = Object.keys(params).some((k) => Math.abs((values[k] ?? 0) - (applied[k] ?? 0)) > 1e-9);

  // Design searches that ended before this page opened: fetch the runs (Studio may have
  // opened here), then what each search chose, once.
  useEffect(() => {
    refreshRuns();
  }, []);
  useEffect(() => {
    for (const r of robotRuns.value) {
      if (r.codesign && !runLive.value[r.name]?.codesign) loadRun(r.name).catch(() => undefined);
    }
  }, [robotRuns.value]);

  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        setAnalysis(await api.post<Analysis>(`/api/robots/${m.robot.id}/analysis`, { design: values }));
      } catch (error) {
        say((error as Error).message, 'error');
      }
    }, 120);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(values)]);

  function set(key: string, value: number): void {
    design.value = { ...design.value, [key]: value };
  }

  async function apply(): Promise<void> {
    setBusy(true);
    try {
      await api.post(`/api/robots/${m.robot.id}/design`, { design: values });
      meta.value = { ...m, robot: { ...m.robot, design_values: { ...values } } };
      say('Saved as the robot\'s design. The simulation now runs on it.');
    } catch (error) {
      say((error as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  function toCad(): void {
    const cad = Object.fromEntries(Object.values(params).map((p) => [p.key, p.default]));
    if (Object.keys(cad).every((k) => Math.abs((values[k] ?? 0) - cad[k]) < 1e-9)) say('Already at the CAD values.');
    design.value = cad;
  }

  const found = robotRuns.value
    .map((r) => ({ run: r.name, report: runLive.value[r.name]?.codesign }))
    .filter((x) => x.report);
  const rodParams = Object.values(params).filter((p) => p.kind === 'rod');
  const strutParams = (position: string) => Object.values(params).filter((p) => p.position === position);
  const a = analysis;
  const stroke = m.robot.struts.stroke_mm;
  const searchable = Object.keys(params).length > 0;
  const shocks = Object.values(params).some((p) => p.position);

  return (
    <div class="section">
      <header class="section-head">
        <h1 class="t-headline">Design</h1>
        <span class="t-meta">{m.robot.name}</span>
      </header>
      <p class="t-meta lead">
        {searchable
          ? "Change the robot's hardware and see what the physics says, before building it. The 3D views follow every change; Save makes it the robot's design."
          : `${m.robot.name}'s profile lists no hardware for the search to choose (rod lengths, shock absorbers), so its design is as built. This is what the physics says about it.`}
      </p>

      {!rodParams.length ? (
        <section class="block" aria-labelledby="size-title">
          <h2 class="block-title" id="size-title"><Icon name="ruler" /> Size</h2>
          <div class="readout-grid">
            <Readout label="Height" value={fixed(a?.size.height, 3)} unit="m" />
            <Readout label="Hips at" value={fixed(a?.size.hip_height, 3)} unit="m" />
            <Readout label="Mass" value={fixed(a?.size.mass, 2)} unit="kg" />
          </div>
          {a && !shocks ? (
            <p class={`verdict${a.standing.unaided ? '' : ' warn'}`}>
              <Icon name={a.standing.unaided ? 'check' : 'alert'} />
              {standingVerdict(a)}
            </p>
          ) : null}
        </section>
      ) : null}

      {rodParams.length ? (
      <section class="block" aria-labelledby="rods-title">
        <h2 class="block-title" id="rods-title"><Icon name="ruler" /> Carbon-fibre rods</h2>
        {rodParams.map((p) => (
          <Slider key={p.key} param={p} value={values[p.key]} applied={applied[p.key]} onChange={(v) => set(p.key, v)} />
        ))}
        <div class="readout-grid">
          <Readout label="Height" value={fixed(a?.size.height, 3)} unit="m" />
          <Readout label="Hips at" value={fixed(a?.size.hip_height, 3)} unit="m" />
          <Readout label="Leg" value={fixed(a ? a.size.leg_length * 1000 : null, 0)} unit="mm" />
          <Readout label="Standing lean" value={fixed(a?.size.lean_deg, 1)} unit="°" />
          {a ? Object.entries(a.rods).map(([name, rod]) => (
            <Readout key={name} label={name} value={fixed(rod.segment * 1000, 0)} unit="mm" title="Joint to joint" />
          )) : null}
        </div>
      </section>
      ) : null}

      {shocks ? (
      <section class="block" aria-labelledby="struts-title">
        <h2 class="block-title" id="struts-title"><Icon name="spring" /> Shock absorbers</h2>
        <p class="t-meta">Two per ankle, front and back. The ankle has no motor: these four springs hold it.</p>
        <div class="two-col">
          {['front', 'back'].map((position) => (
            <div key={position}>
              <h3 class="sub-title">{position === 'front' ? 'Front' : 'Back'}</h3>
              {strutParams(position).map((p) => (
                <Slider key={p.key} param={p} value={values[p.key]} applied={applied[p.key]} onChange={(v) => set(p.key, v)} compact />
              ))}
            </div>
          ))}
        </div>
        {a?.ankle ? (
          <>
            <p class={`verdict${a.standing.unaided ? '' : ' warn'}`}>
              <Icon name={a.standing.unaided ? 'check' : 'alert'} />
              {standingVerdict(a)}
            </p>
            <div class="readout-grid">
              <Readout label="Ankle stiffness" value={fixed(a.ankle.stiffness, 0)} unit="N·m/rad" />
              <Readout label="Preload torque" value={signed(a.ankle.preload_torque, 2)} unit="N·m" title="Positive presses the toes down" />
              <Readout label="Pressure ahead" value={fixed(a.ankle.cop_shift * 1000, 0)} unit="mm" title="How far the springs move the centre of pressure ahead of the ankles" />
              <Readout label="Ankle travel" value={`+${fixed(a.ankle.toe_down_deg, 0)} / −${fixed(a.ankle.toe_up_deg, 0)}`} unit="°" assumed title={`Toe down / toe up before a shock reaches the end of its ${stroke ?? 24} mm stroke`} />
              <Readout label="Rocking" value={fixed(a.ankle.rocking_hz, 2)} unit="Hz" />
              <Readout label="Damping ratio" value={fixed(a.ankle.damping_ratio, 2)} />
            </div>
            <table class="table compact">
              <thead>
                <tr><th>Shock</th><th>Force</th><th>Arm</th><th>Rate</th><th title="Share of the stroke used standing">Stroke</th></tr>
              </thead>
              <tbody>
                {a.ankle.struts.map((s) => (
                  <tr key={s.position}>
                    <td>{s.position === 'front' ? 'Front' : 'Back'}</td>
                    <td>{fixed(s.force, 0)} N</td>
                    <td>{fixed(s.moment_arm * 1000, 0)} mm</td>
                    <td>{fixed(s.rate_lb_per_in, 0)} lb/in</td>
                    <td>{percent(s.stroke_used)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : null}
      </section>
      ) : null}

      {a && (rodParams.length || shocks) ? <Parts analysis={a} values={values} changed={changed} stroke={stroke} /> : null}

      {a ? (
        <section class="block" aria-labelledby="motors-title">
          <h2 class="block-title" id="motors-title"><Icon name="actuator" /> What the motors must hold</h2>
          <p class="t-meta">
            Static torque to hold the standing pose and a deeper crouch
            {shocks ? ` (the body leans ${fixed(a.motors.crouch_lean_deg, 0)}° there; passive ankles keep the shins upright, so crouching loads the hips)` : ''}.
          </p>
          <table class="table compact">
            <thead>
              <tr><th>Joint</th><th>Standing</th><th>Crouch</th><th>Of peak</th></tr>
            </thead>
            <tbody>
              {Object.entries(a.motors.crouch)
                .filter(([j]) => j.endsWith('right') || j.startsWith('waist'))
                .map(([joint, torque]) => (
                  <tr key={joint}>
                    <td>{jointLabel(joint).replace(/^R /, '')}</td>
                    <td>{fixed(Math.abs(a.motors.stand[joint] ?? 0), 1)} N·m</td>
                    <td>{fixed(Math.abs(torque), 1)} N·m</td>
                    <td><Meter share={Math.abs(torque) / (a.motors.peaks[joint] || 1)} label="Crouch torque against peak" /> <span class="num">{percent(Math.abs(torque) / (a.motors.peaks[joint] || 1))}</span></td>
                  </tr>
                ))}
            </tbody>
          </table>
          <h3 class="sub-title">Fast enough to walk?</h3>
          <p class="t-meta">Hip swing speed a walk needs at a {fixed(a.walking.period, 1)} s stride, against the hip actuator's {fixed(a.walking.no_load_speed, 1)} rad/s no-load speed (assumed until measured).</p>
          <table class="table compact">
            <thead>
              <tr><th>Walking</th><th>Hip swing</th><th>Of limit</th></tr>
            </thead>
            <tbody>
              {a.walking.rows.map((r) => (
                <tr key={r.speed}>
                  <td>{fixed(r.speed, 1)} m/s</td>
                  <td>{fixed(r.hip_rate, 1)} rad/s</td>
                  <td><Meter share={r.share} hot={0.9} label="Share of the speed limit" /> <span class="num">{percent(r.share)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {searchable ? (
      <div class="actions sticky-actions">
        <Action onClick={apply} blocked={busy ? 'Saving: one moment.' : !changed && 'Nothing to save: this is the robot\'s design. Change a value first.'}>
          <Icon name="check" />
          {busy ? 'Saving' : changed ? 'Save design' : 'Saved'}
        </Action>
        <button class="btn ghost" type="button" onClick={toCad}>
          <Icon name="reset" />
          Back to CAD
        </button>
        {found.length ? (
          <select class="input" aria-label="Use a design a training run found" value="" onChange={(e) => {
            const pick = found.find((f) => f.run === (e.currentTarget as HTMLSelectElement).value);
            if (pick?.report) design.value = { ...design.value, ...pick.report.chosen };
          }}>
            <option value="" disabled>Use a run's design</option>
            {found.map((f) => (
              <option key={f.run} value={f.run}>{f.run}</option>
            ))}
          </select>
        ) : null}
      </div>
      ) : null}
    </div>
  );
}

function Slider({ param, value, applied, onChange, compact }: { param: DesignParam; value: number; applied: number; onChange: (v: number) => void; compact?: boolean }) {
  const [low, high] = param.range;
  const fill = ((value - low) / (high - low || 1)) * 100;
  const id = `design-${param.key}`;
  return (
    <div class={`slider${compact ? ' compact' : ''}`}>
      <label for={id}>
        <span class="slider-label">{compact ? param.label.replace(/^(Front|Back) /, '') : param.label}</span>
        <input
          class="input num-input slider-value"
          type="number"
          step={param.step || 'any'}
          min={low}
          max={high}
          value={Number(value.toFixed(param.step >= 1 ? 0 : 2))}
          aria-label={`${param.label} in ${param.unit}`}
          onChange={(e) => {
            const v = Number((e.currentTarget as HTMLInputElement).value);
            if (Number.isFinite(v)) onChange(Math.min(high, Math.max(low, v)));
          }}
        />
        <span class="slider-unit">{param.unit}</span>
      </label>
      <input
        id={id}
        type="range"
        min={low}
        max={high}
        step={param.step || (high - low) / 200}
        value={value}
        style={{ '--fill': `${fill}%` } as Record<string, string>}
        onInput={(e) => onChange(Number((e.currentTarget as HTMLInputElement).value))}
        aria-valuetext={designValue(value, param.unit, param.kind)}
      />
      {!compact ? <p class="t-meta slider-help">{param.help}{Math.abs(value - applied) > 1e-9 ? ` Saved: ${designValue(applied, param.unit, param.kind)}.` : ''}</p> : null}
    </div>
  );
}

/** What the stand test says, with the stiffness ratio as the reason. */
function standingVerdict(a: Analysis): string {
  const ratio = fixed(a.ankle.passive_ratio, 2);
  const drift = fixed(a.standing.drift_deg, 0);
  if (a.ankle.passive_ratio === undefined) {
    if (a.standing.unaided) return `Stands unaided: holding its standing pose for ${a.standing.seconds} s, it turns ${fixed(a.standing.drift_deg, 1)}°.`;
    const tips = a.standing.drift_deg >= 80 ? `falls over within ${a.standing.seconds} s` : `tips ${drift}° in ${a.standing.seconds} s`;
    return `Needs a policy to balance: holding its standing pose, it ${tips}.`;
  }
  if (a.standing.unaided) return `Stands unaided: holding its pose for ${a.standing.seconds} s, it turns ${fixed(a.standing.drift_deg, 1)}°. The ankles are ${ratio} times stiffer than its tendency to topple.`;
  if (a.ankle.passive_ratio >= 1)
    return `Needs the policy to balance: the ankles are ${ratio} times stiffer than its tendency to topple, but the leg motors give way too, and holding its pose it tips ${drift}° in ${a.standing.seconds} s.`;
  return `Needs the policy to balance: the ankles have ${ratio} of the stiffness to hold it, and holding its pose it tips ${drift}° in ${a.standing.seconds} s.`;
}

/** What to cut and buy to build the design: rod lengths and each shock absorber's specification. */
function Parts({ analysis: a, values, changed, stroke }: { analysis: Analysis; values: Record<string, number>; changed: boolean; stroke: number | undefined }) {
  const m = meta.value!;
  const rods = Object.entries(a.rods).filter(([, rod]) => rod.rod_length !== null);
  const pieces = (name: string) => m.rods.filter((r) => r.name === name).reduce((sum, r) => sum + r.rods.length, 0);
  const count = (position: string) => m.struts.filter((s) => s.position === position).length;
  const preload = (position: string) => {
    const param = Object.values(m.robot.design).find((p) => p.kind === 'strut_preload' && p.position === position);
    return param ? values[param.key] : null;
  };
  const cad = (rod: Analysis['rods'][string]) => (rod.rod_length! - rod.segment + rod.segment_cad) * 1000;
  return (
    <section class="block" aria-labelledby="parts-title">
      <h2 class="block-title" id="parts-title"><Icon name="tool" /> Parts for this design</h2>
      <p class="t-meta">
        {changed ? 'For the values above, which are not saved yet. ' : ''}
        Cut lengths are the CAD's rods ({rods.map(([name, rod]) => `${name} ${fixed(cad(rod), 0)} mm`).join(', ')}) plus the change: check them against the rods in hand before cutting.
      </p>
      {rods.length ? (
        <div class="readout-grid">
          {rods.map(([name, rod]) => (
            <Readout key={name} label={`${name} rods ×${pieces(name)}`} value={fixed(rod.rod_length! * 1000, 0)} unit="mm" title={`Cut length; the CAD's rod is ${fixed(cad(rod), 0)} mm`} />
          ))}
        </div>
      ) : null}
      {(a.ankle?.struts ?? []).map((s) => (
        <div key={s.position}>
          <h3 class="sub-title">{s.position === 'front' ? 'Front' : 'Back'} shock absorbers ×{count(s.position)}</h3>
          <div class="readout-grid">
            <Readout label="Eye to eye" value={`${fixed(s.eye_to_eye_range[1] * 1000, 1)}–${fixed(s.eye_to_eye_range[0] * 1000, 1)}`} unit="mm" title="Fully extended to fully compressed" />
            <Readout label="Stroke" value={fixed(stroke ?? 24, 0)} unit="mm" assumed />
            <Readout label="Spring" value={fixed(s.rate_n_per_mm, 1)} unit="N/mm" title={`${fixed(s.rate_lb_per_in, 0)} lb/in`} />
            <Readout label="Preload" value={fixed(preload(s.position), 1)} unit="mm" title="How far the spring is compressed with the shock fully extended" />
            <Readout label="Damping" value={fixed(s.damping, 0)} unit="N·s/m" />
            <Readout label="Standing load" value={fixed(s.force, 0)} unit="N" title="Force on each shock with the robot standing still" />
          </div>
        </div>
      ))}
    </section>
  );
}
