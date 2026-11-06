# Isla Studio for Windows

The Windows app opens Studio in its own window. Its installer includes Python,
MuJoCo, the interface, Luma-1 and the trained examples. Users do not need Python,
Node or a terminal. Windows 10 or 11, 64-bit, is required. The installer installs
for the current user and sets up Microsoft WebView2 if it is missing.

Projects live in **Documents/Isla Studio**: `robots`, `runs`, `policies` and
`settings`. Closing the window requests a clean simulation shutdown. Training
already running in its own process can continue, as it does in browser Studio.
The app uses its own loopback connection and never takes over an existing Studio.

CPU training works offline. NVIDIA support remains an optional download from the
Train page; it requires a supported NVIDIA GPU and driver. Those packages, caches
and `studio.log` live in `%LOCALAPPDATA%/technologies.isla.studio`. Optional packages
are kept outside the installation so app upgrades preserve them.

This initial installer is unsigned. Windows may show an unknown publisher warning.
macOS and Linux desktop packages are not built yet; their existing Python launchers
continue to work.

## Build

On a Windows build computer, install Node.js 22 or newer, stable Rust (MSVC target),
and Visual Studio C++ Build Tools with the Windows SDK. Then, from the repository:

```powershell
python scripts/build-desktop.py
```

The script builds the interface, downloads the official portable CPython 3.13.13
archive from python.org, checks its SHA-256, installs CPU dependencies and copies
only the shipped assets. It then builds Tauri's Rust shell and the NSIS installer.
The result is in `desktop/target/release/bundle/nsis/`.

Check the bundled interpreter, shipped robot and examples, live simulation frames,
clean shutdown and a short CPU training subprocess:

```powershell
python scripts/check-desktop.py
```

`--prepare-only` prepares the runtime for inspection. `--skip-ui` reuses an interface
that has already been built. Generated payloads, runtime downloads and Rust outputs
are ignored by Git. Commit both Cargo and npm lock files with source changes.

For a native development run, prepare the payload and run `npm run dev` in `desktop`.
The environment variables `ISLA_DESKTOP_PAYLOAD` and `ISLA_DESKTOP_DATA` can point a
test launch at a prepared payload and isolated projects folder. They are optional;
installed apps discover their resources and the user's Documents folder themselves.

Tauri provides the Rust window and installer; the training and physics core remains
Python with native MuJoCo. Rewriting that core is a separate project, and is not
needed to distribute a standalone app.
