// The anatomy HUD: the robot as a glowing wireframe with every motor marked live.
// It floats top right of the training area; drag its corner to resize, expand it
// to study the robot, fold it away. Click a motor point or a part to select it.

import { effect, useComputed, useSignalEffect } from '@preact/signals';
import { useEffect, useRef, useState } from 'preact/hooks';
import { degrees, fixed, jointLabel } from '../lib/format';
import { anatomySource, design, hardware, live, meta, selection } from '../lib/store';
import type { RobotAssets } from '../lib/robot';
import { theme } from '../lib/theme';
import type { JointMeta } from '../lib/types';
import { AnatomyView, type ScreenPoint } from '../three/anatomy';
import { Icon } from './icons';
import { Choice, IconButton, Meter } from './ui';

type Size = 'normal' | 'large' | 'full';
const SIZES: Record<Size, { w: number; h: number }> = { normal: { w: 380, h: 420 }, large: { w: 640, h: 640 }, full: { w: 0, h: 0 } };

interface MarkerState {
  online: boolean;
  fault: string | null;
  armed: boolean;
  angle: number;
  torque: number | null;
  temp: number | null;
  target: number | null;
}

export function Anatomy({ assets }: { assets: RobotAssets }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const viewRef = useRef<AnatomyView | null>(null);
  const [size, setSize] = useState<Size>('normal');
  const [custom, setCustom] = useState<{ w: number; h: number } | null>(null);
  const [folded, setFolded] = useState(false);
  const [labels, setLabels] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const m = assets.meta;
  const motors = m.joints.filter((j) => j.motor || j.passive);
  const robotLinked = useComputed(() => !!hardware.value && hardware.value.state !== 'disconnected');
  const selected = selection.value;
  const selectedJoint = selected?.kind === 'joint' ? m.joints[selected.id] : null;
  const selectedBody = selected?.kind === 'body' ? m.bodies[selected.id] : null;
  const showAll = labels || size !== 'normal';

  // The live picture: renderer, posing, markers and leader lines, every animation frame.
  useEffect(() => {
    if (!canvas.current || folded) return;
    const view = new AnatomyView(canvas.current, assets);
    viewRef.current = view;
    const unfollow = effect(() => view.setTheme(theme.value)); // Light or dark, the wireframe follows.
    const anchors = new Float64Array(motors.length * 3);
    const screen: ScreenPoint[] = [];
    let raf = 0;
    let last = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (now - last < 30) return;
      last = now;
      const q = poseFor(m.qpos0, m.stand);
      const states = markerStates();
      view.update(q, design.value, strutLoads());
      motors.forEach((j, i) => {
        anchors[i * 3] = view.kin.anchors[j.id * 3];
        anchors[i * 3 + 1] = view.kin.anchors[j.id * 3 + 1];
        anchors[i * 3 + 2] = view.kin.anchors[j.id * 3 + 2];
      });
      view.project(anchors, motors.length, screen);
      placeMarkers(screen, states);
      view.render();
    };
    raf = requestAnimationFrame(loop);
    return () => {
      unfollow();
      cancelAnimationFrame(raf);
      view.dispose();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets, folded]);

  // Selected and faulty links glow red.
  useSignalEffect(() => {
    const view = viewRef.current;
    const sel = selection.value;
    const hw = hardware.value;
    if (!view) return;
    const links: number[] = [];
    if (sel?.kind === 'joint') links.push(m.joints[sel.id].body);
    if (sel?.kind === 'body') links.push(m.bodies[sel.id].link);
    const faults: number[] = [];
    if (hw && anatomySource.value === 'robot') {
      for (const j of m.joints) if (j.motor && hw.joints[j.name]?.fault) faults.push(j.body);
    }
    view.mark(links, faults);
  });

  function poseFor(qpos0: number[], stand: number[]): ArrayLike<number> {
    const frame = live.frame;
    const base = frame?.qpos ?? stand ?? qpos0;
    if (anatomySource.value !== 'robot' || !hardware.value || hardware.value.state === 'disconnected') return base;
    const q = Float64Array.from(stand);
    const hw = hardware.value;
    for (const j of m.joints) {
      const s = hw.joints[j.name];
      if (s && s.q !== null && j.type === 3) q[j.qpos] = s.q;
    }
    if (hw.imu) q.set(hw.imu.quat, 3);
    return q;
  }

  function strutLoads(): number[] {
    const frame = live.frame;
    if (!frame) return [];
    return m.struts.map((s, i) => {
      const length = frame.struts[i]?.[0];
      if (length === undefined) return 0;
      const middle = (s.length_min + s.length_max) / 2;
      return (length - middle) / ((s.length_max - s.length_min) / 2);
    });
  }

  function markerStates(): MarkerState[] {
    const frame = live.frame;
    const hw = hardware.value;
    const fromRobot = anatomySource.value === 'robot' && hw && hw.state !== 'disconnected';
    return motors.map((j) => {
      if (fromRobot && j.motor) {
        const s = hw!.joints[j.name];
        return {
          online: !!s?.online,
          fault: s?.fault ?? null,
          armed: hw!.state === 'armed',
          angle: s?.q ?? NaN,
          torque: s?.tau ?? null,
          temp: s?.temp ?? null,
          target: s?.target ?? null,
        };
      }
      const a = m.actuators.findIndex((x) => x.joint === j.name);
      const qv = frame?.qpos[j.qpos] ?? m.stand[j.qpos];
      return {
        online: true,
        fault: null,
        armed: false,
        angle: qv,
        torque: a >= 0 && frame ? frame.motors[a]?.[2] ?? null : null,
        temp: null,
        target: a >= 0 && frame ? frame.motors[a]?.[3] ?? null : null,
      };
    });
  }

  // Markers and labels: plain DOM over the canvas, moved every frame.
  function placeMarkers(screen: ScreenPoint[], states: MarkerState[]): void {
    const host = layer.current;
    const lines = svg.current;
    if (!host || !lines) return;
    const items = host.querySelectorAll<HTMLElement>('[data-marker]');
    const tags = host.querySelectorAll<HTMLElement>('[data-tag]');
    const w = host.clientWidth;
    const columns: { i: number; side: 'l' | 'r'; y: number }[] = [];
    items.forEach((el) => {
      const i = Number(el.dataset.marker);
      const p = screen[i];
      const s = states[i];
      el.style.transform = `translate(${p.x}px, ${p.y}px)`;
      el.style.visibility = p.visible ? 'visible' : 'hidden';
      const j = motors[i];
      const state = j.passive ? 'passive' : !s.online ? 'off' : s.fault ? 'fault' : s.armed ? 'armed' : 'ok';
      if (el.dataset.state !== state) el.dataset.state = state;
      const tagged = showAllRef.current || s.fault || hoverRef.current === j.id || selectedRef.current === j.id;
      if (tagged && p.visible) columns.push({ i, side: p.x < w / 2 ? 'l' : 'r', y: p.y });
    });
    // Labels sit in a column on each side, in screen order, never overlapping.
    const placed = new Map<number, { x: number; y: number; side: 'l' | 'r' }>();
    for (const side of ['l', 'r'] as const) {
      const col = columns.filter((c) => c.side === side).sort((a, b) => a.y - b.y);
      let lastY = 34;
      for (const c of col) {
        const y = Math.max(c.y, lastY);
        lastY = y + 30;
        placed.set(c.i, { x: side === 'l' ? 12 : w - 12, y, side });
      }
    }
    let path = '';
    tags.forEach((el) => {
      const i = Number(el.dataset.tag);
      const spot = placed.get(i);
      if (!spot) {
        el.style.visibility = 'hidden';
        return;
      }
      const p = screen[i];
      const s = states[i];
      el.style.visibility = 'visible';
      el.dataset.side = spot.side;
      el.style.transform = spot.side === 'l' ? `translate(${spot.x}px, ${spot.y - 13}px)` : `translate(calc(${spot.x}px - 100%), ${spot.y - 13}px)`;
      const value = el.querySelector<HTMLElement>('[data-value]');
      if (value) value.textContent = s.fault ? 'FAULT' : !s.online ? 'NO REPLY' : degrees(s.angle);
      const edge = spot.side === 'l' ? spot.x + el.offsetWidth + 6 : spot.x - el.offsetWidth - 6;
      const elbow = spot.side === 'l' ? edge + 16 : edge - 16;
      path += `M${edge},${spot.y} L${elbow},${spot.y} L${p.x},${p.y} `;
    });
    lines.querySelector('path')!.setAttribute('d', path);
  }

  // Refs the frame loop reads (it outlives renders).
  const showAllRef = useRef(showAll);
  const hoverRef = useRef(hover);
  const selectedRef = useRef<number | null>(null);
  showAllRef.current = showAll;
  hoverRef.current = hover;
  selectedRef.current = selectedJoint?.id ?? null;

  function onCanvasClick(event: MouseEvent): void {
    const view = viewRef.current;
    if (!view) return;
    const body = view.pick(event.clientX, event.clientY);
    if (body === null) {
      selection.value = null;
      return;
    }
    const link = m.bodies[body].link;
    const joint = m.joints.find((j) => j.body === link && (j.motor || j.passive));
    selection.value = joint ? { kind: 'joint', id: joint.id } : { kind: 'body', id: body };
  }

  // Resizing from the bottom-left corner (the panel hangs from the top right).
  function startResize(event: PointerEvent): void {
    event.preventDefault();
    const box = (event.currentTarget as HTMLElement).parentElement!.getBoundingClientRect();
    const startX = event.clientX, startY = event.clientY;
    const move = (e: PointerEvent) => {
      const wmax = window.innerWidth * 0.8, hmax = window.innerHeight - 120;
      setCustom({ w: Math.min(wmax, Math.max(280, box.width + (startX - e.clientX))), h: Math.min(hmax, Math.max(300, box.height + (e.clientY - startY))) });
      setSize('normal');
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  const dims = size === 'normal' ? custom ?? SIZES.normal : SIZES[size];
  const style = size === 'full' ? undefined : { width: `${dims.w}px`, height: `${dims.h}px` };
  const robotData = anatomySource.value === 'robot' && robotLinked.value;
  const motorCount = m.joints.filter((j) => j.motor).length;

  if (folded) {
    return (
      <button class="anatomy-folded" type="button" onClick={() => setFolded(false)} aria-label="Show the anatomy view">
        <Icon name="humanoid" size={18} />
        <span>Anatomy</span>
      </button>
    );
  }

  return (
    <section class={`anatomy brackets anatomy-${size}`} style={style} aria-label="Anatomy view">
      <header class="anatomy-head">
        <h2 class="t-headline">Anatomy</h2>
        <span class="anatomy-count num">{motorCount} motors · {robotData ? 'robot' : 'sim'}</span>
        <div class="anatomy-tools">
          <Choice
            label="Data source"
            value={robotData ? 'robot' : 'sim'}
            options={[
              { value: 'sim', label: 'Sim' },
              { value: 'robot', label: 'Robot', blocked: !robotLinked.value && 'Connect the robot first, in Connect. Until then this shows the simulation.' },
            ]}
            onChange={(v) => (anatomySource.value = v)}
          />
          <IconButton icon="table" label={labels ? 'Hide motor labels' : 'Show every motor label'} pressed={labels} onClick={() => setLabels(!labels)} />
          <IconButton icon="reset" label="Reset the view" onClick={() => viewRef.current?.home()} />
          <IconButton icon={size === 'normal' ? 'expand' : 'shrink'} label={size === 'normal' ? 'Enlarge' : 'Make smaller'} onClick={() => setSize(size === 'normal' ? 'large' : 'normal')} />
          <IconButton icon="grid" label={size === 'full' ? 'Leave full screen' : 'Full screen'} pressed={size === 'full'} onClick={() => setSize(size === 'full' ? 'normal' : 'full')} />
          <IconButton icon="fold" label="Fold away" onClick={() => setFolded(true)} />
        </div>
      </header>
      <div class="anatomy-stage">
        <canvas ref={canvas} onClick={onCanvasClick} aria-label="Wireframe robot: drag to turn, scroll to zoom, click a part to select it" />
        <svg ref={svg} class="anatomy-leaders" aria-hidden="true">
          <path d="" />
        </svg>
        <div ref={layer} class="anatomy-layer">
          {motors.map((j, i) => (
            <button
              type="button"
              key={j.id}
              data-marker={i}
              class={`marker${selectedJoint?.id === j.id ? ' selected' : ''}`}
              aria-label={`${jointLabel(j.name)}${j.passive ? ' (passive)' : ''}`}
              onClick={() => (selection.value = { kind: 'joint', id: j.id })}
              onPointerEnter={() => setHover(j.id)}
              onPointerLeave={() => setHover(null)}
            >
              <i />
            </button>
          ))}
          {motors.map((j, i) => (
            <span key={`t${j.id}`} data-tag={i} class={`tag${selectedJoint?.id === j.id ? ' selected' : ''}`}>
              <b>{jointLabel(j.name)}</b>
              <span data-value class="num">—</span>
            </span>
          ))}
        </div>
      </div>
      <Details joint={selectedJoint} body={selectedBody} robot={!!robotData} />
      {size !== 'full' ? <span class="anatomy-resize" onPointerDown={startResize} aria-hidden="true" title="Drag to resize" /> : null}
    </section>
  );
}

function Details({ joint, body, robot }: { joint: JointMeta | null; body: { label: string; mass: number; link: number } | null; robot: boolean }) {
  const m = meta.value;
  const [, tick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => tick((t) => t + 1), 120);
    return () => window.clearInterval(id);
  }, []);
  if (!m) return null;
  if (!joint && !body) {
    return <p class="anatomy-hint t-meta">Click a motor point or a part. Drag to turn the robot, scroll to zoom.</p>;
  }
  if (body) {
    const link = m.links.find((l) => l.body === body.link);
    return (
      <div class="anatomy-details">
        <div class="t-title">{body.label}</div>
        <div class="anatomy-facts">
          <span><em>Part</em><span class="num">{fixed(body.mass * 1000, 0)} g</span></span>
          <span><em>Link</em><span>{link?.label ?? '—'}</span></span>
          <span><em>Link mass</em><span class="num">{fixed(link?.mass ?? 0, 2)} kg</span></span>
        </div>
      </div>
    );
  }
  const j = joint!;
  const frame = live.frame;
  const hw = hardware.value;
  const a = m.actuators.findIndex((x) => x.joint === j.name);
  const state = robot && hw ? hw.joints[j.name] : null;
  const angle = state?.q ?? frame?.qpos[j.qpos] ?? null;
  const torque = state?.tau ?? (a >= 0 ? frame?.motors[a]?.[2] : null) ?? null;
  const target = state?.target ?? (a >= 0 ? frame?.motors[a]?.[3] : null) ?? null;
  const peak = j.motor?.peak_torque ?? 0;
  return (
    <div class="anatomy-details">
      <div class="t-title">{jointLabel(j.name)}{j.passive ? ' · passive, on shock absorbers' : ''}</div>
      <div class="anatomy-facts">
        <span><em>Angle</em><span class="num">{degrees(angle)}</span></span>
        {j.motor ? <span><em>Target</em><span class="num">{degrees(target)}</span></span> : null}
        {j.motor ? (
          <span class="wide">
            <em>Torque</em>
            <span class="num">{fixed(torque, 1)} / {fixed(peak, 0)} N·m</span>
            <Meter share={Math.abs(torque ?? 0) / Math.max(peak, 1e-6)} label="Torque against peak" />
          </span>
        ) : null}
        {j.motor ? <span><em>Drive</em><span class="num">{j.motor.bus} · node {j.motor.node}</span></span> : null}
        {j.motor ? <span><em>Actuator</em><span>{j.motor.label}</span></span> : null}
        {state?.temp !== undefined && state?.temp !== null ? <span><em>Temp</em><span class="num">{fixed(state.temp, 0)} °C</span></span> : null}
        {state?.fault ? <span class="wide fault"><em>Fault</em><span>{state.fault}</span></span> : null}
      </div>
    </div>
  );
}
