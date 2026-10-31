// Charts, by the dataviz method under the brand's one-accent rule:
// the run is red (ours), context is grey, every value is reachable by hover,
// keyboard and a table, and nothing is a dual axis.

import { signal } from '@preact/signals';
import { useMemo, useState } from 'preact/hooks';
import { compact, designValue, fixed } from '../lib/format';
import { theme } from '../lib/theme';
import type { CodesignReport, DesignParam, ProgressMessage } from '../lib/types';

/** The step under the pointer, shared by every small multiple. */
const crosshair = signal<number | null>(null);

/** Ordinal ramps, low score to high, validated on #050505 and on #F4F4F2: the best always stands out most. */
const GREYS = { dark: ['#5C5C5C', '#8A8A8A', '#B4B4B4', '#F2F2F2'], light: ['#8A8A8A', '#686868', '#444444', '#121212'] };

function niceTicks(lo: number, hi: number, count = 3): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (hi - lo < 1e-9) {
    hi = lo + 1;
  }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

export interface Metric {
  key: keyof ProgressMessage;
  label: string;
  format: (v: number) => string;
  /** Lower is better: the chart says so in its title. */
  lower?: boolean;
}

export const METRICS: Metric[] = [
  { key: 'score', label: 'Score per try', format: (v) => fixed(v, 2) },
  { key: 'falls', label: 'Tries ending in a fall', format: (v) => `${fixed(v * 100, 0)}%`, lower: true },
  { key: 'length', label: 'Try length', format: (v) => `${fixed(v, 1)} s` },
  { key: 'speed_error', label: 'Speed error', format: (v) => `${fixed(v, 2)} m/s`, lower: true },
];

/** One small multiple: a 2px line of the run in red, its latest value labelled at the end. */
export function Curve({ history, metric, phases, width = 340, height = 74 }: { history: ProgressMessage[]; metric: Metric; phases: { step: number; label: string }[]; width?: number; height?: number }) {
  const pad = { l: 34, r: 8, t: 6, b: 16 };
  const points = history.filter((p) => p[metric.key] !== null && Number.isFinite(p[metric.key] as number));
  const xs = points.map((p) => p.steps);
  const ys = points.map((p) => p[metric.key] as number);
  const total = history[history.length - 1]?.total ?? 1;
  const xMax = Math.max(total, xs[xs.length - 1] ?? 1);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (!ys.length) { lo = 0; hi = 1; }
  if (metric.key === 'falls') { lo = 0; hi = Math.max(hi, 0.05); }
  const ticks = niceTicks(lo, hi, 2);
  const yLo = Math.min(lo, ticks[0] ?? lo), yHi = Math.max(hi, ticks[ticks.length - 1] ?? hi);
  const X = (v: number) => pad.l + (v / xMax) * (width - pad.l - pad.r);
  const Y = (v: number) => pad.t + (1 - (v - yLo) / (yHi - yLo || 1)) * (height - pad.t - pad.b);
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${X(p.steps).toFixed(1)},${Y(p[metric.key] as number).toFixed(1)}`).join('');
  const area = points.length ? `${path}L${X(xs[xs.length - 1]).toFixed(1)},${Y(yLo)}L${X(xs[0]).toFixed(1)},${Y(yLo)}Z` : '';
  const hoverStep = crosshair.value;
  const hovered = hoverStep === null ? null : points.reduce<ProgressMessage | null>((best, p) => (!best || Math.abs(p.steps - hoverStep) < Math.abs(best.steps - hoverStep) ? p : best), null);
  const shown = hovered ?? points[points.length - 1];
  const last = points[points.length - 1];

  function move(event: PointerEvent): void {
    const rect = (event.currentTarget as SVGElement).getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * width;
    crosshair.value = Math.max(0, Math.min(xMax, ((x - pad.l) / (width - pad.l - pad.r)) * xMax));
  }

  function key(event: KeyboardEvent): void {
    if (!points.length) return;
    const index = hovered ? points.indexOf(hovered) : points.length - 1;
    if (event.key === 'ArrowLeft') crosshair.value = points[Math.max(0, index - 1)].steps;
    else if (event.key === 'ArrowRight') crosshair.value = points[Math.min(points.length - 1, index + 1)].steps;
    else if (event.key === 'Escape') crosshair.value = null;
    else return;
    event.preventDefault();
  }

  return (
    <figure class="curve">
      <figcaption>
        <span class="curve-title">{metric.label}</span>
        <span class="curve-value num">{shown ? metric.format(shown[metric.key] as number) : '—'}</span>
        {hovered ? <span class="curve-at num">at {compact(hovered.steps)} moves</span> : null}
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        tabIndex={0}
        role="img"
        aria-label={`${metric.label}: latest ${last ? metric.format(last[metric.key] as number) : 'not yet'}. Use left and right arrows to read earlier values.`}
        onPointerMove={move}
        onPointerLeave={() => (crosshair.value = null)}
        onKeyDown={key}
        onBlur={() => (crosshair.value = null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={Y(t)} y2={Y(t)} class="grid-line" />
            <text x={pad.l - 6} y={Y(t) + 3} class="tick" text-anchor="end">{metric.key === 'falls' ? `${Math.round(t * 100)}%` : compact(t)}</text>
          </g>
        ))}
        {phases.map((ph) => (
          <g key={ph.step}>
            <line x1={X(ph.step)} x2={X(ph.step)} y1={pad.t} y2={height - pad.b} class="phase-line" />
            <text x={X(ph.step) + 3} y={height - 4} class="tick">{ph.label}</text>
          </g>
        ))}
        {area ? <path d={area} class="curve-area" /> : null}
        {path ? <path d={path} class="curve-line" /> : null}
        {last ? <circle cx={X(last.steps)} cy={Y(last[metric.key] as number)} r={4} class="curve-dot" /> : null}
        {hovered ? (
          <g>
            <line x1={X(hovered.steps)} x2={X(hovered.steps)} y1={pad.t} y2={height - pad.b} class="crosshair" />
            <circle cx={X(hovered.steps)} cy={Y(hovered[metric.key] as number)} r={4} class="curve-dot" />
          </g>
        ) : null}
      </svg>
    </figure>
  );
}

/** Where each reward's points come from: earned to the right in red, penalties left in grey. */
export function RewardBars({ rewards, labels }: { rewards: Record<string, number>; labels: Record<string, string> }) {
  const rows = Object.entries(rewards)
    .filter(([, v]) => Math.abs(v) > 1e-4)
    .sort((a, b) => b[1] - a[1]);
  const max = Math.max(1e-6, ...rows.map(([, v]) => Math.abs(v)));
  const [hover, setHover] = useState<string | null>(null);
  if (!rows.length) return null;
  return (
    <div class="reward-bars" role="table" aria-label="Points per try from each reward">
      {rows.map(([key, value]) => {
        const share = Math.abs(value) / max;
        return (
          <div class={`reward-row${hover === key ? ' hover' : ''}`} role="row" key={key} tabIndex={0} onPointerEnter={() => setHover(key)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(key)} onBlur={() => setHover(null)}>
            <span class="reward-name" role="cell">{labels[key] ?? key}</span>
            <span class="reward-track" role="cell" aria-hidden="true">
              <span class="reward-zero" />
              <i class={value >= 0 ? 'gain' : 'cost'} style={value >= 0 ? { left: '50%', width: `${share * 50}%` } : { right: '50%', width: `${share * 50}%` }} />
            </span>
            <span class="reward-value num" role="cell">{value >= 0 ? '+' : '−'}{fixed(Math.abs(value), 2)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** The design search: candidates as dots on two chosen values, brighter for better scores. */
export function DesignScatter({ report, params, x, y, cad }: { report: CodesignReport; params: Record<string, DesignParam>; x: string; y: string; cad: Record<string, number> }) {
  const width = 340, height = 250, pad = { l: 44, r: 12, t: 12, b: 34 };
  const xi = report.keys.indexOf(x), yi = report.keys.indexOf(y);
  const px = params[x], py = params[y];
  const [hover, setHover] = useState<number | null>(null);
  const scored = report.candidates.map((c, i) => ({ ...c, i })).filter((c) => c.score !== null);
  const scores = scored.map((c) => c.score as number).sort((a, b) => a - b);
  const quartile = (s: number) => (scores.length < 2 ? 3 : Math.min(3, Math.floor(((scores.findIndex((v) => v >= s) + 0.5) / scores.length) * 4)));
  if (!px || !py || xi < 0 || yi < 0) return null;
  const X = (v: number) => pad.l + ((v - px.range[0]) / (px.range[1] - px.range[0] || 1)) * (width - pad.l - pad.r);
  const Y = (v: number) => pad.t + (1 - (v - py.range[0]) / (py.range[1] - py.range[0] || 1)) * (height - pad.t - pad.b);
  const xt = niceTicks(px.range[0], px.range[1], 4), yt = niceTicks(py.range[0], py.range[1], 4);
  const chosen = report.chosen;
  const h = hover !== null ? report.candidates[hover] : null;
  return (
    <figure class="scatter">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`Design candidates by ${px.label} and ${py.label}; the chosen design is ${designValue(chosen[x], px.unit, px.kind)}, ${designValue(chosen[y], py.unit, py.kind)}`}>
        {xt.map((t) => (
          <g key={`x${t}`}>
            <line x1={X(t)} x2={X(t)} y1={pad.t} y2={height - pad.b} class="grid-line" />
            <text x={X(t)} y={height - pad.b + 14} class="tick" text-anchor="middle">{compact(t)}</text>
          </g>
        ))}
        {yt.map((t) => (
          <g key={`y${t}`}>
            <line x1={pad.l} x2={width - pad.r} y1={Y(t)} y2={Y(t)} class="grid-line" />
            <text x={pad.l - 6} y={Y(t) + 3} class="tick" text-anchor="end">{compact(t)}</text>
          </g>
        ))}
        <text x={(pad.l + width - pad.r) / 2} y={height - 4} class="axis-label" text-anchor="middle">{px.label} ({px.unit})</text>
        <text x={10} y={(pad.t + height - pad.b) / 2} class="axis-label" text-anchor="middle" transform={`rotate(-90 10 ${(pad.t + height - pad.b) / 2})`}>{py.label} ({py.unit})</text>
        <g class="cad-mark">
          <line x1={X(cad[x]) - 6} x2={X(cad[x]) + 6} y1={Y(cad[y])} y2={Y(cad[y])} />
          <line x1={X(cad[x])} x2={X(cad[x])} y1={Y(cad[y]) - 6} y2={Y(cad[y]) + 6} />
          <text x={X(cad[x]) + 8} y={Y(cad[y]) - 6} class="tick">CAD</text>
        </g>
        {report.candidates.map((c, i) =>
          c.score === null ? (
            <circle key={i} cx={X(c.design[xi])} cy={Y(c.design[yi])} r={3} class="dot-unjudged" />
          ) : (
            <circle key={i} cx={X(c.design[xi])} cy={Y(c.design[yi])} r={4} fill={GREYS[theme.value][quartile(c.score)]} class="dot" />
          ),
        )}
        <g class="chosen-mark">
          <circle cx={X(chosen[x])} cy={Y(chosen[y])} r={6} />
          <path d={`M${X(chosen[x]) - 11},${Y(chosen[y]) - 6}v-5h5M${X(chosen[x]) + 11},${Y(chosen[y]) + 6}v5h-5`} />
        </g>
        {report.candidates.map((c, i) => (
          <circle
            key={`hit${i}`}
            cx={X(c.design[xi])}
            cy={Y(c.design[yi])}
            r={12}
            class="hit"
            tabIndex={0}
            onPointerEnter={() => setHover(i)}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
          />
        ))}
      </svg>
      <figcaption class="scatter-key">
        <span><i class="key-dot chosen" />Chosen design</span>
        <span><i class="key-ramp" />Score, low to high</span>
        <span><i class="key-cross" />CAD design</span>
      </figcaption>
      {h ? (
        <div class="tooltip" role="status">
          <strong class="num">{h.score === null ? 'Not judged yet' : `${fixed(h.score, 3)} points/s`}</strong>
          <span>{designValue(h.design[xi], px.unit, px.kind)} · {designValue(h.design[yi], py.unit, py.kind)} · {fixed(h.height, 3)} m tall · {fixed(h.seconds, 0)} robot-s</span>
        </div>
      ) : null}
    </figure>
  );
}

/** Each design value's search: range track, spread band, mean, and where it has been. */
export function DesignTracks({ history, params, cad }: { history: CodesignReport[]; params: Record<string, DesignParam>; cad: Record<string, number> }) {
  const latest = history[history.length - 1];
  const rows = useMemo(() => (latest ? latest.searched.map((key) => params[key]).filter(Boolean) : []), [latest, params]);
  if (!latest) return null;
  return (
    <div class="tracks" role="table" aria-label="Where the design search has settled for each value">
      {rows.map((p) => {
        const span = p.range[1] - p.range[0] || 1;
        const at = (v: number) => `${((v - p.range[0]) / span) * 100}%`;
        const mean = latest.mean[p.key];
        const spread = (latest.spread[p.key] ?? 0) * span;
        return (
          <div class="track-row" role="row" key={p.key}>
            <span class="track-name" role="cell">{p.label}</span>
            <span class="track" role="cell" aria-label={`${p.label} settling at ${designValue(latest.chosen[p.key], p.unit, p.kind)}`}>
              <span class="track-band" style={{ left: at(Math.max(p.range[0], mean - spread)), width: `${((Math.min(p.range[1], mean + spread) - Math.max(p.range[0], mean - spread)) / span) * 100}%` }} />
              {history.slice(-12, -1).map((h, i) => (h.mean[p.key] !== undefined ? <i class="track-ghost" key={i} style={{ left: at(h.mean[p.key]) }} /> : null))}
              <i class="track-cad" style={{ left: at(cad[p.key]) }} title="CAD design" />
              <i class="track-mean" style={{ left: at(mean) }} />
            </span>
            <span class="track-value num" role="cell">{designValue(latest.chosen[p.key], p.unit, p.kind)}</span>
          </div>
        );
      })}
    </div>
  );
}
