# Rust core migration status

Status: **in progress, not ready to replace the default backend**. Updated 7 November 2026.

Studio now has a root Cargo workspace with real native implementations. The
existing Python backend remains the default in source launchers and the desktop
installer. The TypeScript interface and native MuJoCo engine are retained.
A compiled Rust executable is not evidence that every workflow has been ported.

## Implemented candidate

| Crate | Implemented | Validation and gaps |
| --- | --- | --- |
| `isla-learn` | Policy JSON decoding/export, observations, stateful command/gait/jump runner, MLP forward/backward, normalization, Adam, PPO loss/update and NPZ checkpoint I/O | Shipped policy traces, fixed PPO losses/gradients and both checkpoints have parity fixtures. Native environments, trainer, co-design and evaluator are unfinished. |
| `isla-sim` | Exact-version dynamic C ABI, owned native model/data/spec handles, profiles, model construction, standing pose, hardware design values, geometry, mesh bundles and live simulation | Native physics and Luma-1 build/mesh checks exist. Imported-robot coverage and full trajectory equivalence remain incomplete. Import and design analysis endpoints are unavailable. |
| `isla-hardware` | MIT/ODrive codecs, configuration, safety filter, SLCAN/SocketCAN transports, remote driver, relay, practice dynamics and control loop | Byte fixtures, safety and stale-encoder regressions exist. Direct gs_usb/PCAN/Kvaser, relay timing integration, physical adapters and motors need further work. |
| `isla-server` | Axum HTTP/WebSocket service, project paths, sample installation, run records, native live-policy loading, hardware endpoints and CLI | API and persistence tests exist. Training, import and analysis explicitly report unavailable operations. Installer cutover is not done. |

The native service does not embed or invoke Python. It does not silently forward
unfinished endpoints to the reference backend. This is a candidate for validation,
not a feature-complete replacement. See [native development](native-development.md)
for build and run commands.

## Issues addressed in the candidate

- Reject nonfinite motor commands/readings and stale IMU data.
- Keep encoder freshness separate from ODrive heartbeat arrival.
- Clamp target slew after a control-loop stall and latch control-loop faults.
- Give the robot relay one controller lease and an independent watchdog thread.
- Validate project names and resolve parent symlinks before file operations.
- Stage bundled examples before installation and preserve deletion records.
- Reject cross-origin browser access to the local API and WebSocket controls.
- Replace settings atomically on Windows as well as Unix.
- Convert Windows canonical paths before passing them to MuJoCo's file loader.

Some changes above have unit tests but still require integration or hardware
validation. They do not establish hard real-time or physical safety guarantees.

## Remaining implementation and acceptance gates

1. **Models and simulation:** compare standing configurations, contacts, actuator
   gains, torque-speed envelopes, shock forces and trajectories with the reference
   across Luma-1 and imported MJCF/URDF fixtures. Port the asset importer and complete
   design analysis. Preserve source assets and profile behavior.
2. **Training:** port randomized practice environments, rewards, timeout handling,
   swarm reports, checkpoint resume, co-design and full-physics evaluation. Verify
   seeded rollouts, losses, updates, resume behavior, learning quality and throughput.
3. **GPU:** implement and validate a native replacement for the pinned
   CuPy/Warp/MuJoCo Warp pipeline. CPU fallback does not satisfy GPU migration.
   Installation, changing designs, batched physics, streams and memory behavior
   require their own checks. Keep the reference GPU path working until then.
4. **Hardware:** finish remaining supported adapters and test controller leases,
   watchdogs, disconnects, transport errors, stops and remote reconnection. Physical
   validation is separate and requires hardware explicitly set up for testing.
5. **Services:** complete all endpoint schemas and behavior, mirror/follow behavior,
   slow-client handling, background jobs and clean process shutdown. Imported
   profiles and existing runs must remain usable.
6. **Packaging:** switch source launchers, update the Tauri payload to the native
   executable, remove bundled Python only when all required paths are native, and
   update build/smoke scripts. Test a fresh installation and upgrades.
7. **Documentation and release:** change default user instructions only when the
   corresponding runtime changes. Run Windows, macOS and Linux checks and record
   the exact validation performed before publishing.

## Compatibility requirements

Preserve `robot.json`, `setup.json`, `status.json`, `policy.json`, `checkpoint.npz`,
progress/evaluation files and old-run resume behavior. Version any necessary
format change and provide a migration path before writing it. Preserve HTTP,
WebSocket and remote relay payloads consumed by the existing interface.

Protocol bytes and discrete safety decisions should match exactly except for
explicitly documented fixes. Floating-point physics and learning need recorded
numeric/statistical tolerances. Record machine, OS, dependency versions, robot,
design, batch size and seeds. CPU/GPU and end-to-end benchmarks are separate.

A complete Rust migration means Studio-owned backend, hardware, simulation
integration and learning logic run natively, existing projects remain usable,
required CPU/GPU workflows pass, and Python is no longer needed in the runtime
package. The current candidate has **not** met those conditions.
