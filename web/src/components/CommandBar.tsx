// The command bar: say what you want to do, in plain words. Ctrl+K anywhere.

import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { api } from '../lib/api';
import { estop, jump, jumpBlocked, send, STEER, steerBlocked, swarmBlocked } from '../lib/live';
import { hangarPick, hardware, say, section, simState, viewMode, type Section } from '../lib/store';
import { setTheme, theme } from '../lib/theme';
import { Icon } from './icons';

interface Command {
  id: string;
  title: string;
  hint: string;
  icon: string;
  words: string;
  run: () => void | Promise<void>;
  /** For one of a pair of opposite commands: only the one that would change something is listed. */
  shown?: () => boolean;
  /** What has to happen first; the command is listed greyed and says this when chosen. */
  blocked?: () => string | false | null;
}

const hw = (action: string, body: Record<string, unknown> = {}, ok?: string) => async () => {
  try {
    await api.post(`/api/hardware/${action}`, body);
    if (ok) say(ok);
  } catch (error) {
    say((error as Error).message, 'error');
  }
};

const go = (s: Section) => () => {
  section.value = s;
};

const compose = (preset: string) => () => {
  section.value = 'train';
  window.dispatchEvent(new CustomEvent('isla:compose', { detail: preset }));
};

const robotState = () => hardware.value?.state ?? 'disconnected';

function armBlocked(): string | null {
  const state = robotState();
  if (state === 'connected') return null;
  if (state === 'armed') return 'The robot is already armed.';
  if (state === 'fault') return 'Clear the stop first, in Connect.';
  return 'Connect the robot first, in Connect.';
}

const COMMANDS: Command[] = [
  { id: 'move', title: 'Teach it to walk and run', hint: 'Open a new walk-and-run training run', icon: 'route', words: 'train walk run jog preset learn move', run: compose('move') },
  { id: 'teach-jump', title: 'Teach it to jump', hint: 'Open a new jumping training run', icon: 'jump', words: 'train jump hop leap preset learn', run: compose('jump') },
  { id: 'design', title: 'Find the best leg design', hint: 'Walking with the rod and shock absorber search', icon: 'ruler', words: 'codesign co-design rods shock absorbers optimise optimize height', run: compose('codesign') },
  { id: 'stand', title: 'Stand and recover', hint: 'Open a new balancing run', icon: 'shield', words: 'train balance stand push', run: compose('stand') },
  { id: 'jump', title: 'Jump', hint: 'J', icon: 'jump', words: 'jump hop leap', run: jump, blocked: jumpBlocked },
  { id: 'walk-f', title: 'Walk forward', hint: 'Hold W', icon: 'route', words: 'walk forward go move steer', run: () => send({ type: 'sim.command', command: [STEER.forward, 0, 0] }), blocked: steerBlocked },
  { id: 'run-f', title: 'Run forward', hint: 'Hold Shift and W', icon: 'route', words: 'run sprint fast forward jog', run: () => send({ type: 'sim.command', command: [STEER.run, 0, 0] }), blocked: steerBlocked },
  { id: 'halt', title: 'Stop walking', hint: 'Let go of the keys', icon: 'pause', words: 'stop halt still walking running', run: () => send({ type: 'sim.command', command: [0, 0, 0] }), blocked: steerBlocked },
  { id: 'push-f', title: 'Push the robot forward', hint: '60 N for 0.2 s at the torso', icon: 'push', words: 'shove push forward', run: () => send({ type: 'sim.push', force: [0, -60, 0], seconds: 0.2 }) },
  { id: 'push-b', title: 'Push the robot back', hint: '60 N for 0.2 s', icon: 'push', words: 'shove push backward back', run: () => send({ type: 'sim.push', force: [0, 60, 0], seconds: 0.2 }) },
  { id: 'push-s', title: 'Push the robot sideways', hint: '50 N for 0.2 s', icon: 'push', words: 'shove push side left right', run: () => send({ type: 'sim.push', force: [50, 0, 0], seconds: 0.2 }) },
  { id: 'reset', title: 'Reset the simulation', hint: 'R', icon: 'reset', words: 'restart stand up again', run: () => send({ type: 'sim.reset' }) },
  { id: 'play', title: 'Run or pause the simulation', hint: 'Space', icon: 'play', words: 'pause play stop time', run: () => send({ type: 'sim.play', playing: !simState.value.playing }) },
  { id: 'limp', title: 'Turn the simulated motors off', hint: 'Watch the robot fall as a real one would', icon: 'unplug', words: 'limp relax motors off fall', run: () => send({ type: 'sim.mode', mode: 'limp' }) },
  { id: 'standpose', title: 'Hold the standing pose', hint: 'Back to the servos holding the stand', icon: 'humanoid', words: 'stand pose hold servo', run: () => send({ type: 'sim.mode', mode: 'stand' }) },
  { id: 'gantry', title: 'Hang the robot in the gantry', hint: 'A harness holds it upright', icon: 'gantry', words: 'gantry harness hang hold support', run: () => send({ type: 'sim.gantry', on: true }), shown: () => !simState.value.gantry },
  { id: 'ungantry', title: 'Let the robot out of the gantry', hint: 'It stands, or falls, on its own', icon: 'gantry', words: 'gantry release free harness drop', run: () => send({ type: 'sim.gantry', on: false }), shown: () => simState.value.gantry },
  { id: 'swarm', title: 'Show the practice robots', hint: 'Sixteen robots from the selected run', icon: 'grid', words: 'swarm training robots watch many', run: () => { viewMode.value = 'swarm'; }, blocked: swarmBlocked },
  { id: 'live', title: 'Show the live robot', hint: 'The real-time simulation', icon: 'eye', words: 'live single robot', run: () => { viewMode.value = 'live'; } },
  { id: 'practice', title: 'Connect the practice robot', hint: 'Rehearse bring-up without hardware', icon: 'plug', words: 'connect virtual simulated hardware rehearse', run: async () => { await api.put('/api/hardware/settings', { driver: 'virtual' }); await hw('connect', {}, 'Practice robot connected, motors relaxed.')(); section.value = 'connect'; }, blocked: () => robotState() !== 'disconnected' && 'A robot is already connected: disconnect it first, in Connect.' },
  { id: 'arm', title: 'Arm the robot', hint: 'Stiffness ramps up from its measured pose', icon: 'bolt', words: 'arm enable motors on', run: hw('arm', {}, 'Armed.'), blocked: armBlocked },
  { id: 'disarm', title: 'Disarm the robot', hint: 'Relax every motor', icon: 'lock', words: 'disarm relax off', run: hw('disarm', {}, 'Disarmed.'), blocked: () => robotState() !== 'armed' && 'The robot is not armed.' },
  { id: 'estop', title: 'Emergency stop', hint: 'Esc', icon: 'stop', words: 'stop estop emergency halt', run: estop },
  { id: 'go-hangar', title: 'Go to the Hangar', hint: '1', icon: 'hangar', words: 'section hangar robots choose switch load import bay', run: go('hangar') },
  { id: 'import', title: 'Import a robot', hint: 'MJCF or URDF, in the hangar', icon: 'upload', words: 'import add new robot urdf mjcf model', run: () => { section.value = 'hangar'; hangarPick.value = 'import'; } },
  { id: 'go-train', title: 'Go to Train', hint: '2', icon: 'flask', words: 'section train runs', run: go('train') },
  { id: 'go-design', title: 'Go to Design', hint: '3', icon: 'ruler', words: 'section design rods shocks', run: go('design') },
  { id: 'go-connect', title: 'Go to Connect', hint: '4', icon: 'plug', words: 'section connect hardware can motors', run: go('connect') },
  { id: 'go-deploy', title: 'Go to Deploy', hint: '5', icon: 'rocket', words: 'section deploy policy robot', run: go('deploy') },
  { id: 'go-robot', title: 'Go to Robot', hint: '6', icon: 'humanoid', words: 'section robot profile motor map', run: go('robot') },
  { id: 'light', title: 'Switch to light mode', hint: 'For a bright room', icon: 'sun', words: 'light mode theme day bright white appearance', run: () => setTheme('light'), shown: () => theme.value === 'dark' },
  { id: 'dark', title: 'Switch to dark mode', hint: 'For a dark room', icon: 'moon', words: 'dark mode theme night black appearance', run: () => setTheme('dark'), shown: () => theme.value === 'light' },
];

export function CommandBar() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (open) window.setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  // Commands that can run now come first; the rest follow greyed, each able to say what it needs.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const listed = COMMANDS.filter((c) => !c.shown || c.shown())
      .filter((c) => !q || `${c.title} ${c.words} ${c.hint}`.toLowerCase().split(/\s+/).some((w) => w.startsWith(q)) || c.title.toLowerCase().includes(q))
      .map((c) => ({ command: c, blocked: c.blocked?.() || null }));
    return [...listed.filter((m) => !m.blocked), ...listed.filter((m) => m.blocked)].slice(0, 9);
  }, [query, open]);

  function run(match: { command: Command; blocked: string | null } | undefined): void {
    if (!match) return;
    setOpen(false);
    setQuery('');
    setIndex(0);
    if (match.blocked) say(match.blocked);
    else Promise.resolve(match.command.run()).catch(() => undefined);
  }

  function key(event: KeyboardEvent): void {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setIndex((i) => Math.min(matches.length - 1, i + 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setIndex((i) => Math.max(0, i - 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      run(matches[index]);
    } else if (event.key === 'Escape') {
      setOpen(false);
    }
  }

  return (
    <div class={`command${open ? ' open' : ''}`}>
      {open ? (
        <ul class="command-list" id="command-list" role="listbox" aria-label="Commands">
          {matches.length ? (
            matches.map((m, i) => (
              <li
                key={m.command.id}
                id={`cmd-${m.command.id}`}
                role="option"
                aria-selected={i === index}
                aria-disabled={m.blocked ? 'true' : undefined}
                title={m.blocked ?? undefined}
                class={`${i === index ? 'on' : ''}${m.blocked ? ' blocked' : ''}`}
                onPointerEnter={() => setIndex(i)}
                onPointerDown={(e) => {
                  e.preventDefault();
                  run(m);
                }}
              >
                <Icon name={m.command.icon} />
                <span class="command-title">{m.command.title}</span>
                <span class="command-hint">{m.command.hint}</span>
              </li>
            ))
          ) : (
            <li class="command-empty">Nothing matches. Try "walk", "jump", "push", "arm" or "design".</li>
          )}
        </ul>
      ) : null}
      <div class="command-field" onClick={() => setOpen(true)}>
        <kbd class="num">Ctrl K</kbd>
        <input
          ref={input}
          role="combobox"
          aria-expanded={open}
          aria-controls="command-list"
          aria-activedescendant={open && matches[index] ? `cmd-${matches[index].command.id}` : undefined}
          aria-label="Command"
          placeholder='What should the robot do? "walk and run", "jump", "push it", "arm"'
          value={query}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onInput={(e) => {
            setQuery((e.currentTarget as HTMLInputElement).value);
            setIndex(0);
          }}
          onKeyDown={key}
        />
      </div>
    </div>
  );
}
