# Contributing to Isla Studio

Thank you for helping. Bug reports, fixes, robot profiles and documentation are all welcome.

## Reporting a problem

Open an [issue](https://github.com/Isla-Technologies/isla-studio/issues) with what you did, what you expected, what happened, your operating system, and whether you use the desktop app or source launcher. For the desktop app, include the relevant end of `%LOCALAPPDATA%/technologies.isla.studio/studio.log`; copy it before restarting because each launch replaces it. For the source launcher, include the Python version and relevant terminal output. For a robot that will not import or learn, attach its model or say where to find it. Remove private paths and hardware details from logs before posting them.

## Setting up

You need Python 3.10 or newer to work on the core. Node 22 or newer is needed to build the interface or desktop app. The Windows desktop shell also needs Rust and the C++ build tools described in [desktop/README.md](desktop/README.md). End users of the installer do not need these build tools.

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
| `desktop/` | Rust/Tauri window, lifecycle and Windows installer configuration |
| `scripts/` | Windows runtime preparation, installer build and packaged runtime checks |
| `robots/` | The robots that come with Studio: each one's profile and model |
| `tests/` | The test suite |
| `docs/` | The user manual |

With the source launcher, `runs/`, `policies/`, `settings/`, `.cache/` and `.venv/` are local working folders kept out of the repository. The desktop app uses Documents and LocalAppData instead; see [project locations](docs/getting-started.md#your-files-updates-and-removal). Generated desktop payloads, Rust outputs and Node dependencies are also kept out of Git.

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

The desktop app uses a Rust/Tauri shell around the same local server and interface. Its installer includes a private Python runtime so end users need neither Python nor Node. Robot, simulation, training, API and hardware modules still live in `isla_rl/`; the desktop wrapper does not replace them. See [Architecture](docs/architecture.md), [desktop/README.md](desktop/README.md) for building and checking the installer, and [the staged Rust plan](docs/rust-migration.md) before starting core migration work. Keep the source launchers working on Windows, macOS and Linux when changing the desktop package.

For a desktop change, prepare the payload, run Rust formatting/type checks, build the installer and check its bundled runtime:

```powershell
python scripts/build-desktop.py --prepare-only
cargo fmt --manifest-path desktop/Cargo.toml -- --check
cargo check --manifest-path desktop/Cargo.toml
python scripts/build-desktop.py --skip-ui
python scripts/check-desktop.py
```

Check a fresh installation as well: load the bundled robot and example policy, use both themes, then close the window and confirm its backend exits. Stop test training before uninstalling the test copy. GPU and real-hardware checks are separate from the CPU/runtime smoke check; report which ones were actually run.

## Sending a change

1. Fork the repository and make a branch.
2. Keep each commit to one step, with a message that says what changed and why.
3. Run the tests and the linter, and for interface changes, build `ui/dist`.
4. Open a pull request describing the change and how you checked it.

The repository uses LF line endings everywhere (`.gitattributes`), and `.cmd` files CRLF. Continuous integration runs Python tests on Windows, macOS and Linux, builds the interface, and has a separate Windows installer build and bundled-runtime check for relevant pull requests or manual runs. See [ci.yml](.github/workflows/ci.yml) and [windows-desktop.yml](.github/workflows/windows-desktop.yml).

By contributing, you agree that your contribution is licensed under the [MIT licence](LICENSE).
