// Small building blocks in the brand's vocabulary: readouts, segments, panels.

import type { ComponentChildren } from 'preact';
import { notice, say } from '../lib/store';
import { Icon } from './icons';

/** The latest notice: what a click did, or what has to happen first. */
export function Notice() {
  const shown = notice.value;
  if (!shown) return null;
  return (
    <div class={`notice ${shown.tone}`} role={shown.tone === 'error' ? 'alert' : 'status'}>
      <Icon name={shown.tone === 'error' ? 'alert' : 'check'} />
      <span>{shown.text}</span>
    </div>
  );
}

/**
 * A button that always answers. When ``blocked`` says what has to happen first, it looks
 * inactive but a click shows that, where a disabled button would swallow the click.
 */
export function Action({
  blocked,
  onClick,
  class: cls = 'btn',
  title,
  pressed,
  label,
  children,
}: {
  blocked?: string | false | null;
  onClick: () => unknown;
  class?: string;
  title?: string;
  pressed?: boolean;
  label?: string;
  children: ComponentChildren;
}) {
  return (
    <button
      class={`${cls}${blocked ? ' is-blocked' : ''}`}
      type="button"
      aria-disabled={blocked ? 'true' : undefined}
      aria-pressed={pressed}
      aria-label={label}
      title={blocked || title}
      onClick={() => {
        if (blocked) say(blocked);
        else void onClick();
      }}
    >
      {children}
    </button>
  );
}

export function Readout({ label, value, unit, assumed, title }: { label: string; value: ComponentChildren; unit?: string; assumed?: boolean; title?: string }) {
  return (
    <div class={`readout${assumed ? ' assumed' : ''}`} title={title}>
      <span class="key">{label}</span>
      <span class="value">
        {value}
        {unit ? <small>{unit}</small> : null}
      </span>
    </div>
  );
}

/** The HUD progress bar: filled up to ``done``, the current step taller and glowing. */
export function Segments({ count, done, current, label }: { count: number; done: number; current?: boolean; label?: string }) {
  const cells = [];
  for (let i = 0; i < count; i++) {
    const cls = i < done ? 'done' : i === done && current ? 'now' : '';
    cells.push(<i class={cls} key={i} />);
  }
  return (
    <div
      class="segments"
      style={{ gridTemplateColumns: `repeat(${count}, 1fr)` }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={count}
      aria-valuenow={done}
      aria-label={label}
    >
      {cells}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
  class: cls = '',
  style,
  active,
}: {
  title?: ComponentChildren;
  actions?: ComponentChildren;
  children: ComponentChildren;
  class?: string;
  style?: Record<string, string | number>;
  active?: boolean;
}) {
  return (
    <section class={`hud-panel ${active ? 'brackets' : ''} ${cls}`} style={style}>
      {title || actions ? (
        <header class="hud-panel-head">
          {title ? <h2 class="t-headline">{title}</h2> : <span />}
          {actions ? <div class="hud-panel-actions">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function IconButton({ icon, label, onClick, pressed, blocked }: { icon: string; label: string; onClick: () => void; pressed?: boolean; blocked?: string | false | null }) {
  return (
    <Action class="btn ghost icon" label={label} title={label} pressed={pressed} blocked={blocked} onClick={onClick}>
      <Icon name={icon} size={16} />
    </Action>
  );
}

export function Choice<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string; blocked?: string | false | null }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div class="seg-choice" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          type="button"
          key={o.value}
          class={o.blocked ? 'is-blocked' : undefined}
          aria-pressed={o.value === value}
          aria-disabled={o.blocked ? 'true' : undefined}
          title={o.blocked || undefined}
          onClick={() => (o.blocked ? say(o.blocked) : onChange(o.value))}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Status({ tone, children }: { tone: 'ok' | 'off' | 'warn' | 'fault' | 'busy'; children: ComponentChildren }) {
  return <span class={`status ${tone}`}>{children}</span>;
}

/** A thin meter: share of a limit, red past ``hot``. */
export function Meter({ share, hot = 0.75, label }: { share: number; hot?: number; label?: string }) {
  const s = Math.max(0, Math.min(1, Number.isFinite(share) ? share : 0));
  return (
    <span class={`meter${s >= hot ? ' hot' : ''}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(s * 100)} aria-label={label}>
      <i style={{ width: `${s * 100}%` }} />
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ComponentChildren }) {
  return (
    <div class="empty">
      <p class="t-title">{title}</p>
      {children ? <p class="t-meta">{children}</p> : null}
    </div>
  );
}
