// Tried in full physics: a policy put through its paces in the live view's physics, and
// what its shock absorbers and motors went through, for choosing the parts to buy.

import { useEffect, useState } from 'preact/hooks';
import { api } from '../lib/api';
import { compact, fixed, jointLabel, percent, signed } from '../lib/format';
import { meta, say, tests } from '../lib/store';
import type { Evaluation, EvaluatedMove } from '../lib/types';
import { Icon } from './icons';
import { Action, Choice, Readout, Segments } from './ui';

/** Within this much of a stroke's end counts as reaching it, in mm. */
const AT_END = 0.5;

export function Tested({ run }: { run: string }) {
  const [report, setReport] = useState<Evaluation | null>(null);
  const [testing, setTesting] = useState(false);
  const live = tests.value[run];

  useEffect(() => {
    let current = true;
    api
      .get<{ testing: boolean; report: Evaluation | null }>(`/api/runs/${encodeURIComponent(run)}/evaluation`)
      .then((answer) => {
        if (!current) return;
        setReport(answer.report);
        setTesting(answer.testing);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [run, live?.state]);

  const busy = live ? live.state === 'running' : testing;

  async function start(): Promise<void> {
    try {
      await api.post(`/api/runs/${encodeURIComponent(run)}/evaluate`);
      setTesting(true);
      say(`Testing ${run} in full physics. It takes a few minutes; training carries on.`);
    } catch (error) {
      say((error as Error).message, 'error');
    }
  }

  return (
    <section class="block" aria-labelledby="tested-title" aria-busy={busy}>
      <h2 class="block-title" id="tested-title">
        <Icon name="target" /> Tried in full physics
      </h2>
      {busy ? (
        <>
          <Segments count={16} done={5} current label="Testing" />
          <p class="t-meta">Testing now: every speed, shoves from eight sides, and the loads on every part. A few minutes.</p>
        </>
      ) : report ? null : (
        <p class="t-meta">
          Not tested yet. The test puts the policy through every speed it learned, shoves it from eight sides, and records the forces on the shock absorbers and motors: the numbers to buy parts by.
        </p>
      )}
      {report ? <Results report={report} /> : null}
      <div class="actions">
        <Action class="btn ghost" onClick={start} blocked={busy && 'Testing: the results appear here when it is done.'}>
          <Icon name="target" />
          {report ? 'Test again' : 'Test in full physics'}
        </Action>
      </div>
    </section>
  );
}

function Results({ report }: { report: Evaluation }) {
  return (
    <>
      <p class="t-meta">
        Tested {since(report.tested)}
        {report.steps ? ` after ${compact(report.steps)} moves of practice` : ''}, in the live view's physics: 2 ms steps, each motor held to its
        torque-speed limit, the shock absorbers as designed.
      </p>
      {report.walking ? <Walking walking={report.walking} /> : null}
      {report.jumping ? <Jumping jumping={report.jumping} /> : null}
      {report.standing ? (
        <div class="readout-grid">
          <Readout label="Stands still" value={report.standing.stands ? 'Yes' : 'No'} />
          <Readout label="Shoves held" value={fixed(report.standing.shoves.every_side_up_to, 1)} unit="m/s" title="From every side" />
        </div>
      ) : null}
      <Loads report={report} />
    </>
  );
}

/** What the parts went through, one kind of trial at a time: walking, jumping, being shoved. */
function Loads({ report }: { report: Evaluation }) {
  const phases = Object.entries(report.phases ?? {}).filter(([, p]) => p.shocks.length || p.motors.length);
  const main = phases.find(([key]) => key === 'moving' || key === 'jumping')?.[0] ?? phases[0]?.[0] ?? '';
  const [picked, setPicked] = useState(main);
  if (!phases.length) {
    // Reports from before loads were kept by kind of trial.
    return (
      <>
        <Shocks shocks={report.shocks} />
        <Motors motors={report.motors} />
      </>
    );
  }
  const [key, phase] = phases.find(([k]) => k === picked) ?? phases[0];
  const kept = phase.trials - phase.falls;
  return (
    <>
      <h3 class="sub-title">What the parts went through</h3>
      <Choice label="Kind of trial" value={key} options={phases.map(([k, p]) => ({ value: k, label: p.label }))} onChange={setPicked} />
      <p class="t-meta">
        {kept} of {phase.trials} {phase.trials === 1 ? 'trial' : 'trials'}
        {phase.falls ? `; the ${phase.falls} it fell in are left out, since a fall slams the ankles in ways no part is sized for` : ''}.
      </p>
      <Shocks shocks={phase.shocks} />
      <Motors motors={phase.motors} />
    </>
  );
}

function Walking({ walking: w }: { walking: NonNullable<Evaluation['walking']> }) {
  const kept = w.forward.filter((r) => !r.fell);
  const top = kept.length ? Math.max(...kept.map((r) => r.forward)) : null;
  const moves: EvaluatedMove[] = [w.backward, w.left, w.right, w.turn_left, w.turn_right, w.curve];
  const falls = moves.filter((m) => m.fell).length + w.forward.filter((r) => r.fell).length;
  // How much the torso rocks at walking pace: the steadiness that matters most.
  const walking = w.forward.filter((r) => !r.fell && r.asked <= 0.61 && r.sway !== undefined);
  const sway = walking.length ? walking.reduce((sum, r) => sum + r.sway!, 0) / walking.length : null;
  return (
    <>
      <h3 class="sub-title">Walking and running</h3>
      <div class="readout-grid">
        <Readout label="Top speed" value={fixed(top, 2)} unit="m/s" title={`Asked for up to ${fixed(w.commands.forward[1], 1)} m/s`} />
        {sway !== null ? <Readout label="Sway" value={`±${fixed(sway, 1)}`} unit="°" title="How much the torso rocks standing and walking up to 0.6 m/s, RMS about its mean lean" /> : null}
        <Readout label="Backward" value={fixed(Math.abs(w.backward.forward), 2)} unit="m/s" title={`Asked for ${fixed(Math.abs(w.backward.asked[0]), 1)} m/s`} />
        <Readout label="Sideways" value={fixed((Math.abs(w.left.left) + Math.abs(w.right.left)) / 2, 2)} unit="m/s" title={`Asked for ${fixed(w.commands.sideways, 1)} m/s`} />
        <Readout label="Turning" value={fixed((Math.abs(w.turn_left.turn) + Math.abs(w.turn_right.turn)) / 2, 2)} unit="rad/s" title={`Asked for ${fixed(w.commands.turning, 1)} rad/s`} />
        <Readout
          label="Shoves held"
          value={fixed(w.shoves.walking.every_side_up_to, 1)}
          unit="m/s"
          title={`From every side while walking; ${fixed(w.shoves.standing.every_side_up_to, 1)} m/s standing`}
        />
        <Readout label="Roaming falls" value={w.roam.falls} title={`In ${w.roam.seconds} s of random commands, missing the speed asked by ${fixed(w.roam.speed_error, 2)} m/s on average`} />
      </div>
      <table class="table compact">
        <thead>
          <tr>
            <th>Asked</th>
            <th title="Average speed reached; drift sideways in each row's tooltip">Reached</th>
            <th title="How much the torso rocks, RMS degrees about its mean lean">Sway</th>
            <th title="How fast it rocks, RMS degrees a second">Wobble</th>
          </tr>
        </thead>
        <tbody>
          {w.forward.map((r) => (
            <tr key={r.asked}>
              <td>{r.asked === 0 ? 'Standing' : `${fixed(r.asked, 1)} m/s`}</td>
              <td title={`Drifting ${signed(r.left, 2)} m/s sideways; leaning up to ${fixed(r.max_tilt, 0)}°`}>{r.fell ? 'Fell' : `${fixed(r.forward, 2)} m/s`}</td>
              <td>{r.sway === undefined ? '—' : `±${fixed(r.sway, 1)}°`}</td>
              <td>{r.wobble === undefined ? '—' : `${fixed(r.wobble, 0)}°/s`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {falls ? (
        <p class="verdict warn">
          <Icon name="alert" /> It fell in {falls} of the {moves.length + w.forward.length} speed trials.
        </p>
      ) : null}
    </>
  );
}

function Jumping({ jumping: j }: { jumping: NonNullable<Evaluation['jumping']> }) {
  return (
    <>
      <h3 class="sub-title">Jumping</h3>
      <div class="readout-grid">
        <Readout label="Landed" value={j.landed} />
        <Readout label="Rise" value={fixed(j.rise_cm_median, 1)} unit="cm" title={`Centre of mass, typical jump; best ${fixed(j.rise_cm_best, 1)} cm`} />
        <Readout label="Feet clear" value={fixed(j.clearance_cm_median, 1)} unit="cm" title={`Typical jump; best ${fixed(j.clearance_cm_best, 1)} cm`} />
        <Readout label="In the air" value={fixed(j.airborne_ms_median, 0)} unit="ms" title={`Typical jump; best ${fixed(j.airborne_ms_best, 0)} ms`} />
        <Readout label="Stands still" value={j.stands ? 'Yes' : 'No'} title="Between jumps, for 10 s" />
        <Readout label="Shoves held" value={fixed(j.shoves.every_side_up_to, 1)} unit="m/s" title="From every side, standing" />
      </div>
    </>
  );
}

function Shocks({ shocks }: { shocks: Evaluation['shocks'] }) {
  if (!shocks.length) return null;
  const stroke = shocks[0].stroke_mm;
  const bottomed = shocks.filter((s) => s.closest_to_bottom_mm <= AT_END);
  const hardest = Math.max(...shocks.map((s) => s.force_peak));
  return (
    <>
      <table class="table compact" aria-label="Shock absorbers">
        <thead>
          <tr>
            <th>Shock</th>
            <th>Peak</th>
            <th title={`Of the ${fixed(stroke, 0)} mm stroke`}>Stroke</th>
            <th title="Fastest the shaft moved">Shaft</th>
          </tr>
        </thead>
        <tbody>
          {shocks.map((s) => (
            <tr key={s.name}>
              <td>{shockLabel(s.name)}</td>
              <td>{fixed(s.force_peak, 0)} N</td>
              <td title={`${percent(s.used_mm / s.stroke_mm)} of the stroke`}>
                {fixed(s.used_mm, 1)} mm
              </td>
              <td>{fixed(s.shaft_speed_peak, 2)} m/s</td>
            </tr>
          ))}
        </tbody>
      </table>
      {bottomed.length ? (
        <p class="verdict warn">
          <Icon name="alert" /> {bottomed.map((s) => shockLabel(s.name)).join(', ')} reached the end of the {fixed(stroke, 0)} mm stroke, pressing the stop with up to{' '}
          {fixed(Math.max(...bottomed.map((s) => s.stop_force_peak)), 0)} N. Choose shocks with more stroke, or firmer springs.
        </p>
      ) : (
        <p class="verdict">
          <Icon name="check" /> No shock reached the end of its {fixed(stroke, 0)} mm stroke. The hardest push was {fixed(hardest, 0)} N: choose shocks rated above it.
        </p>
      )}
    </>
  );
}

function Motors({ motors }: { motors: Evaluation['motors'] }) {
  const types = [...new Set(motors.map((m) => m.type))];
  const rows = types.map((type) => {
    const group = motors.filter((m) => m.type === type);
    const strained = group.reduce((a, b) => (b.torque_peak / b.rating > a.torque_peak / a.rating ? b : a));
    const fastest = group.reduce((a, b) => (b.speed_peak > a.speed_peak ? b : a));
    return {
      type,
      label: meta.value?.robot.actuator_types[type]?.label ?? type,
      rating: group[0].rating,
      peak: strained,
      rms: Math.max(...group.map((m) => m.torque_rms)),
      limit: Math.max(...group.map((m) => m.saturated_share)),
      fastest,
    };
  });
  const hot = rows.filter((r) => r.rms > r.rating);
  const limited = rows.filter((r) => r.limit >= 0.001);
  return (
    <>
      <table class="table compact" aria-label="Motors">
        <colgroup>
          <col style={{ width: '22%' }} />
          <col style={{ width: '30%' }} />
          <col style={{ width: '18%' }} />
          <col style={{ width: '30%' }} />
        </colgroup>
        <thead>
          <tr>
            <th>Type</th>
            <th title="Peak torque, of the rating, N·m">Peak</th>
            <th title="RMS torque, N·m: the heating load">RMS</th>
            <th title="Fastest joint, of the no-load speed, rad/s">Speed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.type}>
              <td title={r.label}>{r.type.charAt(0).toUpperCase() + r.type.slice(1)}</td>
              <td title={jointLabel(r.peak.joint)}>
                {fixed(r.peak.torque_peak, 1)} <span class="quiet">/ {fixed(r.rating, r.rating % 1 ? 1 : 0)}</span>
              </td>
              <td>{fixed(r.rms, 1)}</td>
              <td title={jointLabel(r.fastest.joint)}>
                {fixed(r.fastest.speed_peak, 1)}
                {r.fastest.no_load_speed ? <span class="quiet"> / {fixed(r.fastest.no_load_speed, 1)}</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p class="t-meta">
        Torque in N·m against the continuous rating, speed in rad/s against the no-load speed (assumed until measured). RMS is the heating load.
        {limited.length ? ` ${limited.map((r) => `${r.type.charAt(0).toUpperCase() + r.type.slice(1)} motors were at their torque limit ${percent(r.limit, 1)} of the time.`).join(' ')}` : ''}
      </p>
      {hot.length ? (
        <p class="verdict warn">
          <Icon name="alert" /> {hot.map((r) => r.label).join(', ')}: RMS torque above the continuous rating. They would overheat over time.
        </p>
      ) : null}
    </>
  );
}

function shockLabel(name: string): string {
  const [position, side] = name.split('_');
  const where = position === 'front' ? 'Front' : position === 'back' ? 'Back' : position;
  return side === 'left' ? `${where} L` : side === 'right' ? `${where} R` : where;
}

function since(stamp: string | undefined): string {
  if (!stamp) return '';
  const seconds = (Date.now() - Date.parse(stamp)) / 1000;
  if (!Number.isFinite(seconds) || seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `on ${new Date(stamp).toLocaleDateString()}`;
}
