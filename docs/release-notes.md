# Release notes

## 0.1.0 — 7 November 2026

This release ships the working Python backend with the Rust/Tauri Windows shell
and the built TypeScript interface. The optional Rust core preview is included
in the source repository, separately from the installer. The default launchers
continue to provide simulation, training, evaluation, robot import, design tools
and hardware workflows through the existing backend.

Download the unsigned Windows x64 setup from
[GitHub Releases](https://github.com/Isla-Technologies/isla-studio/releases/latest).
It includes a private Python 3.13 runtime, MuJoCo 3.15.0, Luma-1 and both trained
example runs. WebView2 setup needs internet if it is not already installed.
macOS and Linux use the [source launchers](getting-started.md).
Projects remain in Documents/Isla Studio when using the desktop app.

### Rust preview

Four native crates implement policy execution and PPO math, checkpoint reading,
MuJoCo model construction and live simulation, hardware protocols and safety,
and an HTTP/WebSocket service. Regression tests cover existing policies and
checkpoints, fixed learning calculations, native model geometry and standing
poses, torque envelopes, protocol bytes, stale readings and project persistence.

The preview is incomplete and is not selected by the installer or source
launchers. Native training environments, co-design, evaluation, robot import,
design analysis, GPU support and installer cutover remain unfinished.
See [migration status](rust-migration.md) and [native development](native-development.md)
for exact coverage, instructions and remaining work.

### Validation

The release checkout was checked locally on Windows x64:

- Python: 62 tests passed; Ruff checks passed.
- Interface: TypeScript checking and the production Vite build passed.
- Rust preview: 23 tests passed; formatting and Clippy completed. Clippy still
  reports style suggestions in the preview.
- Packaged runtime: dependency consistency, two fresh server starts, model and
  mesh delivery, bundled policies, live WebSocket frames and clean stdin-EOF
  shutdown passed. A small CPU training subprocess produced a policy.
- The rebuilt desktop app opened its hangar, rendered Luma-1 and loaded the
  bundled walking policy in its live training view.

Cross-platform checks are configured in CI; the local run does not establish
their outcome. Optional NVIDIA training, physical CAN adapters and real motors
were not validated for this package. The CPU smoke run verifies execution and
file output, not convergence or policy quality. The installer is unsigned.

For setup, updates, data locations and troubleshooting, use the
[user manual](README.md).
