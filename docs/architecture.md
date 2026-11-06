# Architecture

Isla Studio currently combines a Rust desktop shell, a Python core and a TypeScript interface. The Windows installer packages them together. The core migration to Rust is planned in [Rust migration](rust-migration.md); the installer release completed the desktop shell only.

## What runs where

| Component | Implementation | Current responsibility |
| --- | --- | --- |
| Windows desktop shell | Rust/Tauri, [`desktop/src/main.rs`](../desktop/src/main.rs) | Open the window, choose a private loopback port, set project paths, launch the bundled server, wait for readiness and shut it down when the window closes |
| Local API and process management | Python, [`isla_rl/server/`](../isla_rl/server/) | Serve the interface, handle HTTP/WebSocket messages, manage live simulation, launch training/evaluation processes and coordinate hardware |
| Robot models and design | Python, [`isla_rl/robot/`](../isla_rl/robot/) | Import URDF/MJCF, validate profiles, prepare simulation models, generate mesh data and apply design values |
| Live simulation | Python integration, [`isla_rl/sim/`](../isla_rl/sim/), with native MuJoCo | Step physics, run a selected policy and publish robot state |
| Learning and evaluation | Python, [`isla_rl/learn/`](../isla_rl/learn/) | PPO, rewards, observation generation, co-design, CPU/GPU training and policy tests |
| Hardware control | Python, [`isla_rl/hardware/`](../isla_rl/hardware/) | Run the policy and safety filter in Studio, communicate with drivers and handle arming, limits and stops |
| Robot-side relay | Python, [`isla_rl/runtime/`](../isla_rl/runtime/) | Stream motor/IMU readings, apply commands from Studio and relax motors after a disconnect or watchdog timeout |
| Studio interface | TypeScript/Preact/Three.js, [`ui/src/`](../ui/src/) | Pages, controls, charts, the live 3D view and X-ray actuators |
| Public demo | Separate `isla-demo` repository, TypeScript and MuJoCo WebAssembly | Run its exported policy and physics in the visitor's browser; it does not use the desktop shell or a Python server |

The current robot-side relay does not load or evaluate a policy. Studio's `HardwareLink` owns that policy loop. Moving policy execution onto the robot would be an additional behavior change requiring its own validation.

## Desktop and browser operation

The desktop window loads the same built interface as browser Studio. Rust starts a bundled CPython interpreter with `python -m isla_rl.server --no-browser --stop-with-stdin` and connects the window to its private local address. This remains a local HTTP/WebSocket connection inside the app; users do not need to open a browser or start a terminal.

Closing the window closes the parent pipe. The Python server then runs normal application shutdown, including disconnecting its hardware link. Training and evaluation are separate processes; training can continue after the window closes. Stop training before updating or uninstalling files it is using.

The source launchers still start Python directly and open a browser, normally on port 8848. They remain available on Windows, macOS and Linux. Native desktop packages are currently built for Windows x64 only. See [Install and start](getting-started.md).

## Native physics and GPU training

Python calls MuJoCo's native library; the physics engine is already C/C++ with a C API. A Rust port of Studio would still call that engine through bindings. See the [MuJoCo overview](https://mujoco.readthedocs.io/en/stable/overview.html).

Studio's current GPU path uses CuPy, NVIDIA Warp and MuJoCo Warp through Python. Warp compiles Python-authored kernels for CPU/GPU execution; MuJoCo Warp exposes a Python API for batched GPU simulation. See [Warp](https://nvidia.github.io/warp/stable/) and [MuJoCo Warp](https://mujoco.readthedocs.io/en/stable/mjwarp/index.html). Replacing Python orchestration alone does not establish a speedup; the migration plan requires measurements for each proposed replacement.

The desktop installer includes CPU dependencies. Optional NVIDIA packages are downloaded separately into a per-user package folder; packaged GPU training still needs its own validation. The browser demo uses its WebAssembly physics path instead.

## Data and compatibility

Desktop projects live under **Documents/Isla Studio**; logs, caches and optional Python packages live under `%LOCALAPPDATA%/technologies.isla.studio`. Source projects use the checkout by default. The exact paths, overrides and migration steps are in [Install and start](getting-started.md#your-files-updates-and-removal) and the [desktop build guide](../desktop/README.md).

The Windows shell does not change robot profiles, policy files, training checkpoints or API formats. The Rust core plan treats those formats and existing runs as compatibility requirements throughout the transition.

For the source launcher and command-line tools, these environment variables select
data locations when set before starting the process:

| Variable | Checkout default |
| --- | --- |
| `ISLA_ROBOTS` | `robots/` |
| `ISLA_RUNS` | `runs/` |
| `ISLA_POLICIES` | `policies/` |
| `ISLA_SETTINGS` | `settings/` |
| `ISLA_CACHE` | `.cache/` |

The desktop shell sets these for its child processes. Use `ISLA_DESKTOP_DATA` to
redirect its project root during native testing; `ISLA_DESKTOP_PAYLOAD` redirects
its installed code/runtime resources. Neither redirects the shell's LocalAppData
cache/log/package root. These desktop overrides are intended for development and
testing, as described in the [desktop guide](../desktop/README.md).
