// The live charts panel, bottom right: the selected run's progress and its design search.

import { useState } from 'preact/hooks';
import { compact, fixed } from '../lib/format';
import { activeRun, meta, runLive, runs } from '../lib/store';
import { Curve, DesignScatter, METRICS } from './charts';
import { IconButton, Choice } from './ui';

export function ChartsPanel() {
  const [folded, setFolded] = useState(false);
  const [tab, setTab] = useState<'progress' | 'design' | 'table'>('progress');
  const params = meta.value?.robot.design ?? {};
  const keys = Object.keys(params);
  const [axes, setAxes] = useState<[string, string]>([keys[0] ?? '', keys[1] ?? '']);
  const name = activeRun.value;
  const run = name ? runLive.value[name] : undefined;
  if (!name || !run) return null;
  const history = run.history;
  const skill = runs.value.find((r) => r.name === name)?.skill ?? 'walk';
  // Steered skills are judged by how well they keep the speed asked; the rest by how long they stay up.
  const steered = skill === 'walk' || skill === 'move';
  const metrics = METRICS.filter((mt) => (steered ? mt.key !== 'length' : mt.key !== 'speed_error'));
  // Phase changes on the time axis: where the design search started and settled.
  const phases: { step: number; label: string }[] = [];
  for (let i = 1; i < history.length; i++) {
    if (history[i].phase && history[i].phase !== history[i - 1].phase) phases.push({ step: history[i].steps, label: phaseLabel(history[i].phase) });
  }
  const cad = Object.fromEntries(Object.values(params).map((d) => [d.key, d.default]));

  if (folded) {
    return (
      <button class="charts-folded" type="button" onClick={() => setFolded(false)}>
        Charts · {name}
      </button>
    );
  }
  return (
    <section class="charts hud-panel" aria-label={`Charts for ${name}`}>
      <header class="hud-panel-head">
        <h2 class="charts-run" title={name}>{name}</h2>
        <div class="hud-panel-actions">
          <IconButton icon="fold" label="Fold the charts away" onClick={() => setFolded(true)} />
        </div>
      </header>
      <div class="charts-tabs">
        <Choice
          label="Chart"
          value={tab}
          options={[
            { value: 'progress', label: 'Progress' },
            ...(run.codesign ? [{ value: 'design' as const, label: 'Design' }] : []),
            { value: 'table', label: 'Table' },
          ]}
          onChange={setTab}
        />
      </div>
      {tab === 'progress' ? (
        history.length ? (
          <div class="curves">
            {metrics.map((mt) => (
              <Curve key={mt.key} history={history} metric={mt} phases={phases} />
            ))}
          </div>
        ) : (
          <p class="t-meta">Waiting for the first round of practice.</p>
        )
      ) : null}
      {tab === 'design' && run.codesign ? (
        <div>
          <div class="axis-pick">
            <select class="input" aria-label="Across" value={axes[0]} onChange={(e) => setAxes([(e.currentTarget as HTMLSelectElement).value, axes[1]])}>
              {keys.map((k) => <option key={k} value={k}>{params[k].label}</option>)}
            </select>
            <span class="t-meta">against</span>
            <select class="input" aria-label="Up" value={axes[1]} onChange={(e) => setAxes([axes[0], (e.currentTarget as HTMLSelectElement).value])}>
              {keys.map((k) => <option key={k} value={k}>{params[k].label}</option>)}
            </select>
          </div>
          <DesignScatter report={run.codesign} params={params} x={axes[0]} y={axes[1]} cad={cad} />
        </div>
      ) : null}
      {tab === 'table' ? (
        <div class="table-wrap short">
          <table class="table compact">
            <thead>
              <tr><th>Moves</th><th>Score</th><th>Falls</th><th>{skill === 'walk' ? 'Speed err.' : 'Length'}</th><th>Phase</th></tr>
            </thead>
            <tbody>
              {history.slice(-40).reverse().map((p) => (
                <tr key={p.iteration}>
                  <td>{compact(p.steps)}</td>
                  <td>{fixed(p.score, 2)}</td>
                  <td>{fixed((p.falls ?? NaN) * 100, 0)}%</td>
                  <td>{skill === 'walk' ? fixed(p.speed_error, 2) : fixed(p.length, 1)}</td>
                  <td>{phaseLabel(p.phase)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function phaseLabel(phase: string): string {
  return { warmup: 'Warm-up', search: 'Search', finetune: 'Fine-tune', fixed: 'Fixed design' }[phase] ?? phase;
}
