# Native Rust development

The root Cargo workspace is a migration candidate, not the default Studio backend.
The existing `run.cmd`, `run.sh`, `run.command` and desktop installer still use
Python. [Migration status](rust-migration.md) lists the work required before that
changes.

## Build and run

Install stable Rust with the native compiler/linker for your operating system.
Windows needs the MSVC C++ build tools. The workspace uses Rust 2021; current
dependencies require a recent stable toolchain. Install the native **MuJoCo
3.15.0** distribution from its [official releases](https://github.com/google-deepmind/mujoco/releases).
Set `ISLA_MUJOCO_LIBRARY` to the full path of `mujoco.dll`,
`libmujoco.so.3.15.0` or `libmujoco.3.15.0.dylib`.
Alternatively, set `MUJOCO_DIR` to its installation folder.

The loader also searches beside the executable, its `lib/` folder and
`native/mujoco/` in the checkout. For development on Windows it can reuse the
DLL from `.venv/Lib/site-packages/mujoco/`. Loading that native DLL does not start
Python. The loader rejects a library whose version differs from the generated
3.15.0 bindings.

```sh
cargo build --release --locked
cargo run --release -p isla-server -- check
cargo run --release -p isla-server -- serve --port 8849
```

The `check` command builds and steps Luma-1 and reads both bundled policies.
Use a separate port if the reference backend is running. Build the interface
with `npm ci` and `npm run build` inside `ui/` if `ui/dist` is absent.

The CLI accepts `--host`, `--port`, `--robot`, `--no-browser` and
`--stop-with-stdin`. A parent may use `--port 0` to let the OS choose a free port;
the service prints its URL after binding. Closing standard input with
`--stop-with-stdin` shuts down simulation and disconnects hardware.

## Data locations

`ISLA_ROOT` selects the installed assets folder containing `robots/`, `examples/`
and `ui/`. `ISLA_ROBOTS`, `ISLA_RUNS`, `ISLA_POLICIES`, `ISLA_SETTINGS` and
`ISLA_CACHE` retain their existing meanings. If they are unset, the candidate
uses the checkout. Use temporary overrides for tests against your own projects.

Existing JSON profiles, policies and run files remain readable. The NPZ reader
accepts the shipped NumPy checkpoints, including optimizer and normalizer
state. This proves file compatibility, not resumed training parity: the native
training environment is unfinished.

## Checks

```sh
cargo fmt --all -- --check
cargo check --workspace --locked
cargo test --workspace --locked
```

MuJoCo must be available for native simulation and API tests. Hardware tests use
memory transports and fake readings; they never connect to a physical CAN bus.
The numerical fixtures were captured from the reference implementation and the
two bundled runs. Policy output tolerances are `2e-5`; the fixed PPO
loss/gradient fixture uses `2e-6`. Model geometry and standing-pose checks use
their own tolerances recorded in `crates/isla-sim/tests/native.rs`.

Continue running the existing Python tests as a baseline. Cross-platform CI is
configured separately from the Windows installer job. A local Windows test run
does not establish Linux, macOS, GPU or physical robot validation.

## Current limitations

- Native training environments, co-design, evaluation, import and design analysis
  are still incomplete. Those requests return an explicit unavailable response.
- GPU installation is unavailable in the candidate. The reference backend retains
  its CuPy/Warp/MuJoCo Warp training path.
- Native CAN transports currently include SLCAN and Linux SocketCAN. Direct
  gs_usb, PCAN and Kvaser support has not been ported.
- No Rust candidate is bundled into the desktop installer yet.
- Statistical learning quality, full simulation trace parity, physical hardware,
  remote watchdog/disconnect integration and native packaging remain release gates.
