// One line family: Tabler's outline icons (MIT) and Isla's own
// humanoid and actuator, plus the few controls the app needs, drawn in the same
// 24-unit grid and stroke. Never a Unicode glyph standing in for an icon.

import actuator from '../brand/icons/isla-actuator.svg?raw';
import humanoid from '../brand/icons/isla-humanoid.svg?raw';
import bolt from '../brand/icons/bolt.svg?raw';
import brain from '../brand/icons/brain.svg?raw';
import chartBar from '../brand/icons/chart-bar.svg?raw';
import circleCheck from '../brand/icons/circle-check.svg?raw';
import cpu from '../brand/icons/cpu.svg?raw';
import cube from '../brand/icons/cube.svg?raw';
import flask from '../brand/icons/flask.svg?raw';
import handStop from '../brand/icons/hand-stop.svg?raw';
import home from '../brand/icons/home.svg?raw';
import rocket from '../brand/icons/rocket.svg?raw';
import route from '../brand/icons/route.svg?raw';
import search from '../brand/icons/search.svg?raw';
import server from '../brand/icons/server-2.svg?raw';
import settings from '../brand/icons/settings.svg?raw';
import shield from '../brand/icons/shield-check.svg?raw';
import stack from '../brand/icons/stack-2.svg?raw';
import steering from '../brand/icons/steering-wheel.svg?raw';
import target from '../brand/icons/target-arrow.svg?raw';
import tool from '../brand/icons/tool.svg?raw';
import trending from '../brand/icons/trending-up.svg?raw';

const drawn = (paths: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;

const ICONS: Record<string, string> = {
  actuator,
  humanoid,
  bolt,
  brain,
  chart: chartBar,
  check: circleCheck,
  cpu,
  cube,
  flask,
  stop: handStop,
  home,
  rocket,
  route,
  search,
  server,
  settings,
  shield,
  stack,
  steering,
  target,
  tool,
  trending,
  play: drawn('<path d="M7 4v16l13 -8z"/>'),
  pause: drawn('<path d="M6 5h4v14h-4z"/><path d="M14 5h4v14h-4z"/>'),
  reset: drawn('<path d="M20 11a8.1 8.1 0 0 0 -15.5 -2m-.5 -4v4h4"/><path d="M4 13a8.1 8.1 0 0 0 15.5 2m.5 4v-4h-4"/>'),
  expand: drawn('<path d="M16 4h4v4"/><path d="M14 10l6 -6"/><path d="M8 20h-4v-4"/><path d="M4 20l6 -6"/>'),
  shrink: drawn('<path d="M5 9h4v-4"/><path d="M3 3l6 6"/><path d="M19 15h-4v4"/><path d="M15 15l6 6"/>'),
  fold: drawn('<path d="M5 12h14"/>'),
  close: drawn('<path d="M18 6l-12 12"/><path d="M6 6l12 12"/>'),
  plug: drawn('<path d="M9.785 6l8.215 8.215l-2.054 2.054a5.81 5.81 0 1 1 -8.215 -8.215l2.054 -2.054z"/><path d="M4 20l3.5 -3.5"/><path d="M15 4l-3.5 3.5"/><path d="M20 9l-3.5 3.5"/>'),
  unplug: drawn('<path d="M16.123 16.092l-.177 .177a5.81 5.81 0 1 1 -8.215 -8.215l.159 -.159"/><path d="M4 20l3.5 -3.5"/><path d="M15 4l-3.5 3.5"/><path d="M20 9l-3.5 3.5"/><path d="M3 3l18 18"/>'),
  download: drawn('<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2"/><path d="M7 11l5 5l5 -5"/><path d="M12 4v12"/>'),
  upload: drawn('<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2"/><path d="M7 9l5 -5l5 5"/><path d="M12 4v12"/>'),
  alert: drawn('<path d="M12 9v4"/><path d="M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0z"/><path d="M12 16h.01"/>'),
  push: drawn('<path d="M5 12h14"/><path d="M13 6l6 6l-6 6"/><path d="M5 7v10"/>'),
  eye: drawn('<path d="M10 12a2 2 0 1 0 4 0a2 2 0 0 0 -4 0"/><path d="M21 12c-2.4 4 -5.4 6 -9 6c-3.6 0 -6.6 -2 -9 -6c2.4 -4 5.4 -6 9 -6c3.6 0 6.6 2 9 6"/>'),
  camera: drawn('<path d="M5 7h1a2 2 0 0 0 2 -2a1 1 0 0 1 1 -1h6a1 1 0 0 1 1 1a2 2 0 0 0 2 2h1a2 2 0 0 1 2 2v9a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-9a2 2 0 0 1 2 -2"/><path d="M9 13a3 3 0 1 0 6 0a3 3 0 0 0 -6 0"/>'),
  grid: drawn('<path d="M4 4h6v6h-6z"/><path d="M14 4h6v6h-6z"/><path d="M4 14h6v6h-6z"/><path d="M14 14h6v6h-6z"/>'),
  lock: drawn('<path d="M5 13a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6z"/><path d="M11 16a1 1 0 1 0 2 0a1 1 0 0 0 -2 0"/><path d="M8 11v-4a4 4 0 1 1 8 0v4"/>'),
  ruler: drawn('<path d="M5 4h14a1 1 0 0 1 1 1v5a1 1 0 0 1 -1 1h-7a1 1 0 0 0 -1 1v7a1 1 0 0 1 -1 1h-5a1 1 0 0 1 -1 -1v-14a1 1 0 0 1 1 -1"/><path d="M4 8l2 0"/><path d="M4 12l3 0"/><path d="M4 16l2 0"/><path d="M8 4l0 2"/><path d="M12 4l0 3"/><path d="M16 4l0 2"/>'),
  spring: drawn('<path d="M12 3v3"/><path d="M8 7l8 2l-8 2l8 2l-8 2l8 2"/><path d="M12 18v3"/>'),
  table: drawn('<path d="M3 5a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14z"/><path d="M3 10h18"/><path d="M10 3v18"/>'),
  chevron: drawn('<path d="M9 6l6 6l-6 6"/>'),
  keyboard: drawn('<path d="M2 8a2 2 0 0 1 2 -2h16a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2l0 -8"/><path d="M6 10l0 .01"/><path d="M10 10l0 .01"/><path d="M14 10l0 .01"/><path d="M18 10l0 .01"/><path d="M6 14l0 .01"/><path d="M18 14l0 .01"/><path d="M10 14l4 .01"/>'),
  gantry: drawn('<path d="M4 21v-17h16v17"/><path d="M2 21h4"/><path d="M18 21h4"/><path d="M12 4v5"/><path d="M12 9a2 2 0 1 1 -2 2"/>'),
  hangar: drawn('<path d="M3 21v-9a9 7 0 0 1 18 0v9"/><path d="M2 21h20"/><path d="M8 21v-5h8v5"/><path d="M8 18.5h8"/>'),
  jump: drawn('<path d="M12 4v10"/><path d="M8 8l4 -4l4 4"/><path d="M4 20h16"/>'),
  sun: drawn('<path d="M8 12a4 4 0 1 0 8 0a4 4 0 1 0 -8 0"/><path d="M3 12h1m8 -9v1m8 8h1m-9 8v1m-6.4 -15.4l.7 .7m12.1 -.7l-.7 .7m0 11.4l.7 .7m-12.1 -.7l-.7 .7"/>'),
  moon: drawn('<path d="M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 0 7.92 12.446a9 9 0 1 1 -8.313 -12.454z"/>'),
  pin: drawn('<path d="M15 4.5l-4 4l-4 1.5l-1.5 1.5l7 7l1.5 -1.5l1.5 -4l4 -4"/><path d="M9 15l-4.5 4.5"/><path d="M14.5 4l5.5 5.5"/>'),
};

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 16, label }: { name: IconName | string; size?: number; label?: string }) {
  const svg = ICONS[name] ?? ICONS.cube;
  return (
    <span
      class="icon"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ width: `${size}px`, height: `${size}px` }}
      // Local, trusted SVG files from ../brand/icons.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
