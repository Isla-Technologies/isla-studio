// Light or dark: the choice is remembered; until one is made, the system's.
// index.html applies the same rule before the page paints, so it never flashes.

import { signal } from '@preact/signals';
import type { Theme } from '../three/theme';

export type { Theme };

const KEY = 'isla-theme';
const system = window.matchMedia('(prefers-color-scheme: light)');

function chosen(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null; // Storage blocked: follow the system.
  }
}

export const theme = signal<Theme>(chosen() ?? (system.matches ? 'light' : 'dark'));

function apply(value: Theme): void {
  document.documentElement.dataset.theme = value;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', value === 'light' ? '#F4F4F2' : '#050505');
}

apply(theme.value);
system.addEventListener('change', () => {
  if (chosen()) return;
  theme.value = system.matches ? 'light' : 'dark';
  apply(theme.value);
});

export function setTheme(value: Theme): void {
  try {
    localStorage.setItem(KEY, value);
  } catch {
    // Not remembered, but still applied.
  }
  theme.value = value;
  apply(value);
}
