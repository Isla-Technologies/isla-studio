# Isla Studio for Windows

The Windows app opens Studio in its own window. Its installer includes Python,
MuJoCo, the interface, Luma-1 and the trained examples. Users do not need Python,
Node or a terminal. Windows 10 or 11, 64-bit, is required. The installer installs
for the current user and sets up Microsoft WebView2 if it is missing.

Projects live in **Documents/Isla Studio**: `robots`, `runs`, `policies` and
`settings`. Closing the window requests a clean simulation shutdown. Training
already running in its own process can continue, as it does in browser Studio.
The app uses its own loopback connection and never takes over an existing Studio.
See [Install and start](../docs/getting-started.md) for moving source projects,
updating and uninstalling the app. Stop background training before an update or
uninstall. Keeping projects in Documents also keeps them outside the uninstaller's
optional application-data cleanup; that cleanup can remove local GPU packages,
caches and logs.

After installation, CPU training works offline. WebView2 setup needs internet if
that runtime is missing. NVIDIA support remains an optional download from the
Train page; it requires a supported NVIDIA GPU and driver. Those packages, caches
and `studio.log` live in `%LOCALAPPDATA%/technologies.isla.studio`. Optional packages
are kept outside the installation so app upgrades preserve them.

This initial installer is unsigned. Windows may show an unknown publisher warning.
macOS and Linux desktop packages are not built yet; their existing Python launchers
continue to work.

## What the Rust code does

This release implements the desktop shell in Rust. `src/main.rs` creates the
WebView2 window, selects a private port, sets the project/package environment,
starts the bundled Python server, waits for it and owns its shutdown pipe.

Robot import/building, the live simulation integration, learning, hardware control
and the API still run in Python. The UI remains TypeScript, and MuJoCo supplies
the native physics library. Packaging those components together does not port
their logic to Rust. See [Architecture](../docs/architecture.md) and the
[staged core migration plan](../docs/rust-migration.md).

## Project and package locations

| Contents | Default location |
| --- | --- |
| User robots, runs, policies and settings | `Documents/Isla Studio/{robots,runs,policies,settings}` |
| Server log, replaced at each start | `%LOCALAPPDATA%/technologies.isla.studio/studio.log` |
| Caches | `%LOCALAPPDATA%/technologies.isla.studio/cache` |
| Optional packages for bundled Python 3.13 | `%LOCALAPPDATA%/technologies.isla.studio/python313-packages` |
| GPU installer output | `Documents/Isla Studio/runs/gpu-install.log` |

Documents follows Windows' configured location, including redirection. The shell
sets `ISLA_ROBOTS`, `ISLA_RUNS`, `ISLA_POLICIES`, `ISLA_SETTINGS` and `ISLA_CACHE`
for its server and training children. Optional packages use `PIP_TARGET`,
`PYTHONPATH` and a generated `sitecustomize.py` so their `.pth` files also run.
They remain separate from the installed runtime during app upgrades.

## Build

On a Windows build computer, install Python 3.10 or newer to run the build scripts,
Node.js 22 or newer, stable Rust (MSVC target),
and Visual Studio C++ Build Tools with the Windows SDK. Then, from the repository:

```powershell
python scripts/build-desktop.py
```

The script builds the interface, downloads the official portable CPython 3.13.13
archive from python.org, checks its SHA-256, installs CPU dependencies and copies
the interface, example runs and the robots listed in `BUNDLED_ROBOTS` in
`scripts/build-desktop.py`. Imported robots in a maintainer's checkout are not
automatically included. It then builds Tauri's Rust shell and the NSIS installer.
The result is `desktop/target/release/bundle/nsis/Isla Studio_0.1.0_x64-setup.exe`
for the current version; later versions use their own version in the filename.
The payload contains `runtime-packages.txt` and `build.json` to identify the
bundled dependency versions and Python archive checksum.

Check the bundled interpreter, shipped robot and examples, live simulation frames,
clean shutdown and a short CPU training subprocess:

```powershell
python scripts/check-desktop.py
```

The same smoke check can exercise files extracted by a test installation:

```powershell
python scripts/check-desktop.py --payload "C:/path/to/test-install/payload"
```

It uses temporary project/package directories and does not connect hardware.
Also open the installed app, try the example policy and both themes, then close
it and confirm its backend exits. A release check must include the installed
layout, not only an executable with development payload overrides. CPU/runtime
and fresh-install checks have passed locally; packaged GPU training and physical
hardware need separate validation. Test uninstalling the temporary copy after
stopping training and closing its window.

`--prepare-only` prepares the runtime for inspection. `--skip-ui` reuses an interface
that has already been built. Generated payloads, runtime downloads and Rust outputs
are ignored by Git. Commit both Cargo and npm lock files with source changes.

For a native development run, prepare the payload and run `npm run dev` in `desktop`.
The environment variables `ISLA_DESKTOP_PAYLOAD` and `ISLA_DESKTOP_DATA` can point a
test launch at a prepared payload and isolated projects folder. They are optional;
installed apps discover their resources and the user's Documents folder themselves.

```powershell
python scripts/build-desktop.py --prepare-only
cd desktop
npm ci
npm run dev
```

`ISLA_DESKTOP_DATA` redirects the project root for an isolated native test.
`ISLA_DESKTOP_PAYLOAD` redirects the bundled code/runtime location. Cache/log and
GPU package locations remain under LocalAppData. Test the default resource lookup
as well before shipping. The source/browser server supports additional `ISLA_*`
path overrides described in [Architecture](../docs/architecture.md).

Windows CI builds the installer and runs the packaged CPU smoke check for relevant
pull requests and manual runs; see
[windows-desktop.yml](../.github/workflows/windows-desktop.yml). macOS/Linux native
packaging, code signing and GPU/hardware qualification remain separate work.
