# Staged Rust core migration

Status: planned, following the Windows desktop shell release. Core migration has not started. This plan describes proposed work; the [architecture page](architecture.md) describes what currently ships. Upstream references were checked on 6 November 2026; the repository's dependency pins remain authoritative for implementation.

The goal is to move Studio's application logic from Python to Rust while preserving existing robots, policies, training runs, hardware behavior and GPU training. The TypeScript interface can remain in place. MuJoCo remains the native physics engine. Python remains available during the transition wherever a replacement has not passed its checks.

## Why migrate in stages

The current Rust code manages the desktop window and server lifecycle. The remaining Python code covers robot import/building, simulation integration, inference, PPO, GPU kernels, hardware control and the API. Those responsibilities need separate ports and tests; packaging them in an executable did not port them to Rust.

MuJoCo already supplies a native C API, so the proposed simulation work is Rust bindings and integration. Existing matrix operations and GPU kernels also execute native code. A faster language for orchestration may help some workloads, but speed gains must be measured against the existing implementation. [MuJoCo documents its native API](https://mujoco.readthedocs.io/en/stable/overview.html).

The current GPU stack is Python-facing. Keep it working as a worker while exploring native integration. Current Warp documentation also describes generated C++/CUDA and native replay workflows; their suitability for Studio's pinned versions, mutable robot designs and training pipeline needs a prototype. A Python-free GPU path is unresolved, rather than assumed impossible. See [Warp native workflows](https://nvidia.github.io/warp/stable/user_guide/programming_model/cpp_cuda_workflows.html) and [MuJoCo Warp](https://mujoco.readthedocs.io/en/stable/mjwarp/index.html).

## Sequence and completion checks

Names such as `isla-core` below describe proposed new crates; they do not exist yet. Source paths in the table are relative to [`isla_rl/`](../isla_rl/). Complete and review one stage before making it the default.

| Stage | Work and current source | Check before switching |
| --- | --- | --- |
| 0. Baseline and boundaries | Record behavior and timings from `learn/policy.py`, `sim/session.py`, `server/`, `hardware/` and the training CLI. Specify file formats and process messages. Prototype the Rust/Python boundary. | Reproducible fixtures and benchmark commands; existing CPU tests, desktop smoke and demo checks pass; record GPU baseline separately on supported hardware. |
| 1. Policy execution | Add an `isla-core` crate for policy-file decoding, observation transforms, normalization, network forward passes, command ramps and gait/jump clocks. Reference `learn/policy.py` and the inference portions of `learn/network.py`. Call it from the existing Python simulation/control code through a narrow adapter. | Compare observations, actions and stateful runner traces against Python across shipped and legacy policies, fixed input sequences, boundaries and malformed files. Preserve float precision, joint ordering and currently supported file behavior. Benchmark the complete adapter call. |
| 2. Hardware and robot relay | Port MIT/ODrive codecs, safety calculations and configuration validation first; then the transport/control loop and `runtime/` relay. Reference `hardware/protocols.py`, `safety.py`, `config.py`, `link.py`, `can_driver.py` and `remote.py`. Keep the Python `VirtualDriver` behind an adapter until its model/physics dependencies move in stage 3. | Byte-identical protocol fixtures, matching limit/fault decisions, fake-driver and loopback integration tests, disconnect/watchdog/stop behavior and measured loop timing. Real adapter and motor validation follows only with hardware explicitly set up and requested. |
| 3. Robot models and CPU simulation | Wrap the pinned MuJoCo C API with explicit ownership of model/data memory. Port profiles, import/build/design transformations, mesh output, `VirtualDriver` dynamics and live/evaluation integration from `robot/`, `hardware/virtual.py`, `sim/`, and `learn/evaluate.py`. | Imported fixtures and Luma-1 build correctly; model structure, masses, limits, contact setup, torque-speed envelope and shock behavior match. Compare rollout traces within recorded numeric tolerances and rerun policy/evaluation checks. |
| 4. Backend services | Move project persistence, examples, run management and HTTP/WebSocket endpoints from `paths.py`, `examples.py` and `server/` to Rust. Continue launching the existing Python training worker through explicit messages. Keep browser access as well as the desktop window. | Existing UI and source/browser workflows work against the Rust service. Test API schemas, streaming/backpressure, path validation, cancellation, background runs, upgrades and clean shutdown across supported platforms. |
| 5. CPU learning and co-design | Port rewards, environments, PPO/network updates, optimizer/normalizer state, checkpoint handling and co-design from `learn/`. Reuse the tested Rust simulation and policy code. | Compare fixed-rollout losses/gradients/updates, resume existing checkpoints, run multiple seeded learning trials and compare learning quality and throughput. Existing policy export and full-physics evaluation continue to work. |
| 6. GPU integration and Python retirement | Prototype a native GPU path or a Rust-owned worker integration for the pinned CuPy/Warp/MuJoCo Warp workload. Replace the Python worker only when an equivalent GPU pipeline exists. | Validate batched physics, randomization, actuator limits, changing designs, GPU memory/streams, training quality, checkpoint resume and install behavior. Remove bundled Python only after all required paths have passed. |

Stages can be split into smaller pull requests. Hardware migration does not relocate policy execution to the robot by itself: the first implementation preserves Studio's current control topology. An autonomous robot-side policy runner would need a separate design and safety review.

The Python trainer also consumes robot builders and MuJoCo model metadata. During stage 3, define a supported interchange for model assets and metadata, and validate it with both CPU and GPU workers. Keep the Python builder available until those consumers work. Do not pass unowned MuJoCo pointers across languages or processes merely to remove a Python module sooner.

## First implementation milestone

Start with stage 0 and the stage 1 policy loader/forward-pass slice. It is small enough to compare independently and feeds both simulation and hardware without rewriting the training pipeline at once.

1. Capture versioned policy fixtures and fixed observations from the shipped walking and co-design examples. Include command, gait, design and jump cases with small synthetic fixtures where the examples do not cover them.
2. Define the public Rust data types and validation errors for the policy format. Preserve base64 little-endian float32 arrays, C row-major storage, matrix orientation and joint order. Record currently accepted optional defaults and legacy `gait_period` files: the Python reader does not strictly gate the `version` field, so a new version check must not silently reject usable old policies.
3. Prototype a thin Python extension using PyO3 and maturin, and test it with source-supported Python versions and bundled CPython. [PyO3 supports native Python extensions and Rust/Python integration](https://pyo3.rs/). Keep the Rust logic independent of that adapter.
4. Implement decoding, normalization/clipping, ELU and network forward execution, followed by observation/runner state in separate changes. Preserve float32 network boundaries and the runner's higher-precision state where used. Compare each stage to the existing Python implementation.
5. Measure single-robot inference and batched calls, including conversion overhead, allocations and warm/cold execution. Keep training's GPU arrays on their current path; avoid per-element or per-layer crossings into Python.
6. Add the built extension to the Windows payload and test a fresh installation. Keep the Python reference path selectable during validation. Make Rust the default only after parity and the recorded performance criteria pass.

This milestone would migrate real core behavior. It would still leave the Python server, model-building and learner in place until their later stages pass.

## Compatibility and measurement rules

- Preserve `robot.json`, `setup.json`, `status.json`, `policy.json`, `checkpoint.npz`, progress/evaluation files and old-run resume behavior. Version any required format change and provide a reader/migration path before writing it.
- Preserve HTTP/WebSocket payloads used by Studio and the remote relay. Keep the demo's exported data readable; coordinate changes with its separate TypeScript/WASM runtime.
- Establish numeric tolerances from the reference implementation and the operation being compared. Protocol bytes and discrete safety decisions should match exactly; floating-point physics and training need documented numerical/statistical comparisons.
- Record machine, OS, dependency versions, robot/design, batch size and seeds. Separate CPU, GPU and end-to-end timings. Set explicit acceptance thresholds from the baseline before evaluating the replacement; record regressions as well as improvements.
- Add Windows, macOS and Linux coverage for each Rust core component before replacing the cross-platform Python path. Windows installer verification does not establish macOS/Linux desktop support or robot-computer architecture support.
- Keep parity checks and a documented fallback during each transition. Delete the old implementation only after it is no longer required for a supported path.
- Keep simulator/fake-driver checks separate from physical validation. Rust alone does not establish real-time scheduling guarantees or prove safe behavior on a robot.

## Completion and open decisions

A Rust core migration is complete when Studio-owned backend, hardware, simulation integration and learning logic run in Rust, existing projects remain usable, CPU and required GPU workflows pass their checks, and Python can be removed from the runtime package. A TypeScript interface and native MuJoCo/CUDA dependencies can remain.

Before implementation, settle the adapter/wheel strategy, numeric baselines, Rust linear-algebra choice, MuJoCo binding ownership and GPU integration feasibility with small prototypes. Choose crate versions after those checks; do not change the pinned physics/GPU dependencies merely to make a port easier. Estimate the schedule after the first milestone and GPU prototype provide evidence.
