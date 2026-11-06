# Contributing to Isla Studio

Thank you for helping. Bug reports, fixes, robot profiles and documentation are all welcome.

## Reporting a problem

Open an [issue](https://github.com/Isla-Technologies/isla-studio/issues) with what you did, what you expected, what happened, your operating system and Python version, and the end of Isla Studio's terminal output. For a robot that will not import or learn, attach its model or say where to find it.

## Setting up

You need Python 3.10 or newer. Node 22 or newer is needed only to change the interface.

```sh
git clone https://github.com/Isla-Technologies/isla-studio.git
cd isla-studio
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt     # Windows: .venv\Scripts\python
```

Run the tests and the linter before sending a change:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check isla_rl tests
```

## Where things are

| Folder | Holds |
| --- | --- |
| `isla_rl/robot/` | Robot profiles, URDF and MJCF import, the simulation model, design physics |
| `isla_rl/sim/` | The live simulation the 3D view shows |
| `isla_rl/learn/` | Practice robots (processor and GPU), PPO, skills and rewards, co-design, testing in full physics |
| `isla_rl/hardware/` | CAN drivers, motor protocols, the safety layer, the practice robot |
| `isla_rl/runtime/` | What runs on the robot's own computer |
| `isla_rl/server/` | The local server behind the interface |
| `isla_rl/examples.py`, `examples/` | The trained runs that come with Studio |
| `ui/` | The interface (Preact, TypeScript, Three.js). `ui/dist` is the built copy the server serves |
| `robots/` | The robots that come with Studio: each one's profile and model |
| `tests/` | The test suite |
| `docs/` | The user manual |

Made on each computer and kept out of the repository: `runs/`, `policies/`, `settings/`, `.cache/` and `.venv/`.

## Changing the interface

```sh
cd ui
npm ci
npm run dev       # Vite on http://localhost:5173, talking to Isla Studio's server on 8848
npm run build     # type-check, then update ui/dist
```

Commit the rebuilt `ui/dist` with your change: people who run Isla Studio do not need Node.

Some things the interface holds to:

- **Every control answers.** A control that cannot act yet says what to do first, through the `blocked` reason of `Action` or `Choice` in `ui/src/components/ui.tsx`. Never a silent disabled button.
- **Plain words.** Say what happens in the user's terms: "moves", "falls", "practice robots", not jargon.
- **Both themes.** Every colour is a token with a dark and a light value; check a change in both.
- **Narrow screens.** Check a layout at 1280, 1440 and 1920 pixels wide.

## Windows desktop app

The desktop app uses a Rust/Tauri shell around the same local server and interface. Its installer includes a private Python runtime so end users need neither Python nor Node. See [desktop/README.md](desktop/README.md) for prerequisites, building and checking the Windows installer. Keep the source launchers working on Windows, macOS and Linux when changing the desktop package.

## Sending a change

1. Fork the repository and make a branch.
2. Keep each commit to one step, with a message that says what changed and why.
3. Run the tests and the linter, and for interface changes, build `ui/dist`.
4. Open a pull request describing the change and how you checked it.

The repository uses LF line endings everywhere (`.gitattributes`), and `.cmd` files CRLF. Continuous integration runs the tests on Windows, macOS and Linux and builds the interface.

By contributing, you agree that your contribution is licensed under the [MIT licence](LICENSE).
